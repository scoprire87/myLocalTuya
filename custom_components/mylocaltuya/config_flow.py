"""Config flow: user code -> QR code -> pick devices. That's it."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .cloud import (
    CloudError,
    TuyaCloud,
    qr_login_result,
    qr_login_start,
    token_from_login,
)
from .const import (
    CONF_DEVICES,
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_UID,
    CONF_USER_CODE,
    CONF_USERNAME,
    DEV_HOST,
    DEV_ID,
    DEV_MANUAL_HOST,
    DEV_NAME,
    DEV_SUB,
    DEV_VERSION,
    DISCOVERY_WAIT,
    DOMAIN,
    PROTOCOL_VERSIONS,
    TUYA_QR_PREFIX,
)
from .discovery import async_get_discovery, async_release_discovery

_LOGGER = logging.getLogger(__name__)

CONF_SELECTED = "selected"


def _qr_schema(qr_token: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("qr"): selector.QrCodeSelector(
                selector.QrCodeSelectorConfig(
                    data=f"{TUYA_QR_PREFIX}{qr_token}",
                    scale=5,
                    error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                )
            )
        }
    )


class MyLocalTuyaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._user_code = ""
        self._qr_token = ""
        self._login: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options."""
        return MyLocalTuyaOptionsFlow()

    # ---- step 1: user code ---------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the Smart Life user code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._user_code = user_input[CONF_USER_CODE].strip()
            try:
                self._qr_token = await self.hass.async_add_executor_job(
                    qr_login_start, self._user_code
                )
            except CloudError as err:
                _LOGGER.debug("QR request failed: %s", err)
                errors["base"] = "invalid_user_code"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("QR request failed")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_scan()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USER_CODE, default=self._user_code): str}
            ),
            errors=errors,
        )

    # ---- step 2: scan the QR code ----------------------------------------------
    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the QR code and wait for the scan."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                login = await self.hass.async_add_executor_job(
                    qr_login_result, self._qr_token, self._user_code
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Login check failed")
                login = None
            if login:
                self._login = login
                await self.async_set_unique_id(login.get(CONF_UID))
                if self.source == "reauth":
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    entry = self._get_reauth_entry()
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, **self._entry_auth_data()},
                    )
                self._abort_if_unique_id_configured()
                return await self.async_step_devices()
            errors["base"] = "login_failed"
        return self.async_show_form(
            step_id="scan", data_schema=_qr_schema(self._qr_token), errors=errors
        )

    def _entry_auth_data(self) -> dict[str, Any]:
        return {
            CONF_USER_CODE: self._user_code,
            CONF_TERMINAL_ID: self._login.get(CONF_TERMINAL_ID, ""),
            CONF_ENDPOINT: self._login.get(CONF_ENDPOINT, ""),
            CONF_UID: self._login.get(CONF_UID, ""),
            CONF_USERNAME: self._login.get(CONF_USERNAME, ""),
            CONF_TOKEN_INFO: token_from_login(self._login),
        }

    # ---- step 3: choose devices --------------------------------------------------
    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download devices and let the user pick them."""
        auth = self._entry_auth_data()
        if not self._devices:
            cloud = TuyaCloud(
                auth[CONF_USER_CODE],
                auth[CONF_TERMINAL_ID],
                auth[CONF_ENDPOINT],
                auth[CONF_TOKEN_INFO],
            )
            try:
                self._devices = await self.hass.async_add_executor_job(
                    cloud.fetch_devices
                )
            except CloudError as err:
                _LOGGER.error("Cannot download devices: %s", err)
                return self.async_abort(reason="cannot_fetch_devices")
            auth[CONF_TOKEN_INFO] = cloud.token_info
            self._login.update(auth[CONF_TOKEN_INFO])
            supported = [d for d in self._devices if not d[DEV_SUB] and d["dps"]]
            if not supported:
                return self.async_abort(reason="no_devices")

        candidates = {
            d[DEV_ID]: d for d in self._devices if not d[DEV_SUB] and d["dps"]
        }
        if user_input is not None:
            chosen = [candidates[i] for i in user_input[CONF_SELECTED] if i in candidates]
            if not chosen:
                return self.async_show_form(
                    step_id="devices",
                    data_schema=self._devices_schema(candidates),
                    errors={"base": "no_selection"},
                )
            await self._discover(chosen)
            return self.async_create_entry(
                title=auth[CONF_USERNAME] or "Tuya",
                data={**auth, CONF_DEVICES: {d[DEV_ID]: d for d in chosen}},
            )

        skipped = [d[DEV_NAME] for d in self._devices if d[DEV_SUB] or not d["dps"]]
        return self.async_show_form(
            step_id="devices",
            data_schema=self._devices_schema(candidates),
            description_placeholders={
                "count": str(len(candidates)),
                "skipped": ", ".join(skipped) if skipped else "-",
            },
        )

    @staticmethod
    def _devices_schema(candidates: dict[str, dict]) -> vol.Schema:
        options = [
            selector.SelectOptionDict(value=dev_id, label=dev[DEV_NAME])
            for dev_id, dev in candidates.items()
        ]
        return vol.Schema(
            {
                vol.Required(CONF_SELECTED, default=list(candidates)): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, multiple=True)
                )
            }
        )

    async def _discover(self, devices: list[dict[str, Any]]) -> None:
        """Listen briefly for UDP broadcasts to learn IP and protocol."""
        discovery = await async_get_discovery(self.hass)
        try:
            await discovery.async_wait_for({d[DEV_ID] for d in devices}, DISCOVERY_WAIT)
            for dev in devices:
                if found := discovery.devices.get(dev[DEV_ID]):
                    dev[DEV_HOST] = found.get("ip")
                    dev[DEV_VERSION] = found.get("version") or None
        finally:
            async_release_discovery(self.hass)

    # ---- reauth --------------------------------------------------------------------
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Log in again with a new QR code."""
        self._user_code = entry_data.get(CONF_USER_CODE, "")
        return await self.async_step_user()


class MyLocalTuyaOptionsFlow(OptionsFlow):
    """Options: refresh from the cloud or set an IP by hand."""

    def __init__(self) -> None:
        """Initialize."""
        self._device_id: str | None = None
        self._fetched: list[dict[str, Any]] | None = None
        self._token: dict[str, Any] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["refresh", "pick_device", "relogin"]
        )

    async def async_step_relogin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start a new QR login."""
        self.config_entry.async_start_reauth(self.hass)
        return self.async_abort(reason="reauth_started")

    async def async_step_refresh(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download keys and DPs again, optionally adding new devices."""
        entry = self.config_entry
        cloud = TuyaCloud(  # only used when nothing was fetched yet
            entry.data[CONF_USER_CODE],
            entry.data[CONF_TERMINAL_ID],
            entry.data[CONF_ENDPOINT],
            entry.data[CONF_TOKEN_INFO],
        )
        if self._fetched is None:
            try:
                self._fetched = await self.hass.async_add_executor_job(
                    cloud.fetch_devices
                )
            except CloudError as err:
                _LOGGER.error("Cloud refresh failed: %s", err)
                return self.async_abort(reason="cannot_fetch_devices")
            self._token = cloud.token_info
        fetched = self._fetched

        candidates = {d[DEV_ID]: d for d in fetched if not d[DEV_SUB] and d["dps"]}
        current = entry.data[CONF_DEVICES]
        if user_input is None:
            options = [
                selector.SelectOptionDict(value=dev_id, label=dev[DEV_NAME])
                for dev_id, dev in candidates.items()
            ]
            return self.async_show_form(
                step_id="refresh",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_SELECTED, default=[i for i in current if i in candidates]
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(options=options, multiple=True)
                        )
                    }
                ),
            )

        devices = {}
        for dev_id in user_input[CONF_SELECTED]:
            if dev_id not in candidates:
                continue
            new = dict(candidates[dev_id])
            old = current.get(dev_id, {})
            for key in (DEV_HOST, DEV_VERSION, DEV_MANUAL_HOST):
                if key in old:
                    new[key] = old[key]
            devices[dev_id] = new
        self.hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_DEVICES: devices, CONF_TOKEN_INFO: self._token},
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data=dict(entry.options))

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the device to edit."""
        devices = self.config_entry.data[CONF_DEVICES]
        if user_input is not None:
            self._device_id = user_input["device"]
            return await self.async_step_device()
        options = [
            selector.SelectOptionDict(value=dev_id, label=dev[DEV_NAME])
            for dev_id, dev in devices.items()
        ]
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set IP and protocol version by hand (empty IP = automatic)."""
        entry = self.config_entry
        devices = dict(entry.data[CONF_DEVICES])
        conf = dict(devices[self._device_id])
        if user_input is not None:
            host = (user_input.get(DEV_MANUAL_HOST) or "").strip()
            version = user_input.get(DEV_VERSION, "auto")
            if host:
                conf[DEV_MANUAL_HOST] = host
            else:
                conf.pop(DEV_MANUAL_HOST, None)
            if version != "auto":
                conf[DEV_VERSION] = version
            elif host:
                conf.pop(DEV_VERSION, None)
            devices[self._device_id] = conf
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_DEVICES: devices}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data=dict(entry.options))

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        DEV_MANUAL_HOST,
                        description={"suggested_value": conf.get(DEV_MANUAL_HOST, "")},
                    ): str,
                    vol.Required(DEV_VERSION, default="auto"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["auto", *PROTOCOL_VERSIONS],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            description_placeholders={
                "name": conf.get(DEV_NAME, ""),
                "host": conf.get(DEV_HOST) or "-",
                "version": conf.get(DEV_VERSION) or "-",
            },
        )
