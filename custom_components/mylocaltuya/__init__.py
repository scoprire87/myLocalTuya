"""myLocalTuya: local control of Tuya devices, configured with a QR code."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .cloud import CloudError, TuyaCloud
from .const import (
    CLOUD_REFRESH_MIN_INTERVAL,
    CONF_DEVICES,
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    DEV_HOST,
    DEV_MANUAL_HOST,
    DEV_VERSION,
    PLATFORMS,
)
from .device import TuyaLocalDevice
from .discovery import TuyaDiscovery, async_get_discovery, async_release_discovery
from .mapping import Plan, plan_entities

_LOGGER = logging.getLogger(__name__)

# Keys that belong to the local setup and must survive a cloud refresh
LOCAL_KEYS = (DEV_HOST, DEV_VERSION, DEV_MANUAL_HOST)


@dataclass
class RuntimeData:
    """Objects living while the entry is loaded."""

    discovery: TuyaDiscovery
    devices: dict[str, TuyaLocalDevice] = field(default_factory=dict)
    plans: dict[TuyaLocalDevice, list[Plan]] = field(default_factory=dict)
    last_refresh: dict[str, float] = field(default_factory=dict)


type MyLocalTuyaConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MyLocalTuyaConfigEntry) -> bool:
    """Set up from a config entry."""
    discovery = await async_get_discovery(hass)
    runtime = RuntimeData(discovery=discovery)
    entry.runtime_data = runtime

    @callback
    def network_changed(dev_id: str, host: str, version: str) -> None:
        devices = dict(entry.data[CONF_DEVICES])
        conf = devices.get(dev_id)
        if conf is None:
            return
        new = dict(conf)
        if not conf.get(DEV_MANUAL_HOST):
            new[DEV_HOST] = host
        if version:
            new[DEV_VERSION] = version
        if new == conf:
            return
        _LOGGER.info("%s: now at %s (v%s)", conf.get("name"), host, version)
        devices[dev_id] = new
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICES: devices}
        )
        if (device := runtime.devices.get(dev_id)) is not None:
            device.async_update_config(new)

    @callback
    def need_refresh(dev_id: str) -> None:
        now = time.monotonic()
        last = runtime.last_refresh.get(dev_id)
        if last is not None and now - last < CLOUD_REFRESH_MIN_INTERVAL:
            return
        runtime.last_refresh[dev_id] = now
        _LOGGER.info(
            "%s: local key rejected, fetching a new one from the cloud", dev_id
        )
        entry.async_create_background_task(
            hass, async_refresh_from_cloud(hass, entry, {dev_id}), "mylocaltuya_refresh"
        )

    for dev_id, conf in entry.data[CONF_DEVICES].items():
        device = TuyaLocalDevice(hass, conf, discovery, network_changed, need_refresh)
        runtime.devices[dev_id] = device
        runtime.plans[device] = plan_entities(conf.get("category", ""), device.spec)

    for device in runtime.devices.values():
        device.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyLocalTuyaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for device in entry.runtime_data.devices.values():
            await device.async_stop()
        async_release_discovery(hass)
    return unloaded


async def async_refresh_from_cloud(
    hass: HomeAssistant, entry: ConfigEntry, only: set[str] | None = None
) -> list[dict]:
    """Download keys and DP tables again and store them in the entry.

    Returns the devices fetched from the cloud (all of them when `only` is
    None). Local data such as IP and protocol version is preserved.
    """
    cloud = TuyaCloud(
        entry.data[CONF_USER_CODE],
        entry.data[CONF_TERMINAL_ID],
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN_INFO],
    )
    try:
        fetched = await hass.async_add_executor_job(cloud.fetch_devices, only)
    except CloudError as err:
        _LOGGER.warning("Tuya cloud refresh failed: %s", err)
        return []

    devices = dict(entry.data[CONF_DEVICES])
    for new in fetched:
        old = devices.get(new["id"])
        if old is None:
            continue
        merged = {**new, **{k: old[k] for k in LOCAL_KEYS if k in old}}
        devices[new["id"]] = merged
        runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
        if runtime and (device := runtime.devices.get(new["id"])) is not None:
            device.async_update_config(merged)

    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_DEVICES: devices, CONF_TOKEN_INFO: cloud.token_info},
    )
    return fetched
