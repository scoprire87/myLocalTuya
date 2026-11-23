"""Tuya cloud access, used only to log in and to download device data.

Everything here is blocking (requests based) and must run in the executor.
The cloud is never used to control devices.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tuya_sharing import LoginControl, Manager

from .const import (
    DEV_CATEGORY,
    DEV_DPS,
    DEV_ID,
    DEV_LOCAL_KEY,
    DEV_NAME,
    DEV_PRODUCT_ID,
    DEV_PRODUCT_NAME,
    DEV_SUB,
    TUYA_CLIENT_ID,
    TUYA_SCHEMA,
)

_LOGGER = logging.getLogger(__name__)

# Tuya "thing model" types -> names used by the specification API
_TYPE_ALIASES = {
    "bool": "Boolean",
    "boolean": "Boolean",
    "value": "Integer",
    "integer": "Integer",
    "enum": "Enum",
    "string": "String",
    "raw": "Raw",
    "bitmap": "Bitmap",
    "json": "Json",
}


class CloudError(Exception):
    """Error talking to the Tuya cloud."""


def qr_login_start(user_code: str) -> str:
    """Ask Tuya for a QR login token. Returns the token to embed in the QR."""
    response = LoginControl().qr_code(TUYA_CLIENT_ID, TUYA_SCHEMA, user_code)
    if not response.get("success"):
        raise CloudError(response.get("msg") or "unknown error")
    return response["result"]["qrcode"]


def qr_login_result(qr_token: str, user_code: str) -> dict[str, Any] | None:
    """Return login info once the QR has been scanned, None otherwise."""
    success, info = LoginControl().login_result(qr_token, TUYA_CLIENT_ID, user_code)
    if not success:
        _LOGGER.debug("QR login not completed: %s", info)
        return None
    return info


def token_from_login(info: dict[str, Any]) -> dict[str, Any]:
    """Extract the token part of a login result."""
    return {
        "t": info.get("t", 0),
        "expire_time": info.get("expire_time", 0),
        "uid": info.get("uid", ""),
        "access_token": info.get("access_token", ""),
        "refresh_token": info.get("refresh_token", ""),
    }


def _normalize_type(value: str | None) -> str:
    if not value:
        return "Raw"
    return _TYPE_ALIASES.get(value.lower(), value.capitalize())


def _load_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


def build_dps(spec: dict[str, Any] | None, status: dict[str, Any] | None) -> dict:
    """Build the DP table (local dp id -> description) for a device.

    `spec` is the result of the specifications API (cloud codes, types and
    ranges, plus which codes are writable), `status` the result of the
    status API which links every local dp id to its cloud code.
    """
    spec = spec or {}
    functions = {f["code"]: f for f in spec.get("functions") or []}
    statuses = {s["code"]: s for s in spec.get("status") or []}

    dps: dict[str, dict[str, Any]] = {}
    for rel in (status or {}).get("dpStatusRelationDTOS") or []:
        try:
            dp_id = str(rel["dpId"])
            code, _ = _load_json(rel["statusFormat"]).popitem()
        except (KeyError, TypeError, AttributeError):
            continue
        cloud = statuses.get(code) or functions.get(code)
        if cloud:
            dp_type = _normalize_type(cloud.get("type"))
            values = _load_json(cloud.get("values"))
        else:
            dp_type = _normalize_type(rel.get("valueType"))
            values = _load_json(rel.get("valueDesc"))
        enum_map = {}
        if rel.get("valueConvert") == "enum":
            enum_map = {
                str(raw): item["value"]
                for raw, item in (rel.get("enumMappingMap") or {}).items()
                if isinstance(item, dict) and "value" in item
            }
        dps[dp_id] = {
            "code": code,
            "type": dp_type,
            "values": values if isinstance(values, dict) else {},
            "writable": code in functions,
            "convert": rel.get("valueConvert") or "default",
            "enum_map": enum_map,
        }
    return dps


class TuyaCloud:
    """Small wrapper around the device sharing SDK."""

    def __init__(
        self,
        user_code: str,
        terminal_id: str,
        endpoint: str,
        token_info: dict[str, Any],
    ) -> None:
        """Initialize."""
        self._manager = Manager(
            TUYA_CLIENT_ID, user_code, terminal_id, endpoint, token_info, None
        )

    @property
    def token_info(self) -> dict[str, Any]:
        """Current (possibly refreshed) token, in a re-loadable form."""
        info = self._manager.customer_api.token_info
        return {
            "t": info.expire_time,
            "expire_time": 0,
            "uid": info.uid,
            "access_token": info.access_token,
            "refresh_token": info.refresh_token,
        }

    def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            response = self._manager.customer_api.get(path, params)
        except Exception as err:  # noqa: BLE001 - SDK raises many kinds
            raise CloudError(str(err)) from err
        if not response or not response.get("success"):
            raise CloudError(f"{path}: {response}")
        return response.get("result")

    def fetch_devices(self, only: set[str] | None = None) -> list[dict[str, Any]]:
        """Download devices with local keys and DP tables."""
        homes = self._get("/v1.0/m/life/users/homes") or []
        devices: list[dict[str, Any]] = []
        for home in homes:
            items = self._get(
                "/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])}
            )
            for item in items or []:
                if only is not None and item.get("id") not in only:
                    continue
                devices.append(self._device_from_item(item))
        return devices

    def _device_from_item(self, item: dict[str, Any]) -> dict[str, Any]:
        dev_id = item["id"]
        spec = status = None
        try:
            spec = self._get(f"/v1.1/m/life/{dev_id}/specifications")
        except CloudError as err:
            _LOGGER.warning("No specification for %s: %s", dev_id, err)
        try:
            status = self._get(f"/v1.0/m/life/devices/{dev_id}/status")
        except CloudError as err:
            _LOGGER.warning("No DP mapping for %s: %s", dev_id, err)
        return {
            DEV_ID: dev_id,
            DEV_NAME: item.get("name") or dev_id,
            DEV_LOCAL_KEY: item.get("local_key", ""),
            DEV_CATEGORY: item.get("category", ""),
            DEV_PRODUCT_ID: item.get("product_id", ""),
            DEV_PRODUCT_NAME: item.get("product_name", ""),
            DEV_SUB: bool(item.get("sub")),
            DEV_DPS: build_dps(spec, status),
        }
