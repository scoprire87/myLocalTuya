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
    DEV_NODE_ID,
    DEV_PARENT,
    DEV_PRODUCT_ID,
    DEV_RAW_KEYS,
    DEV_PRODUCT_NAME,
    DEV_SUB,
    GATEWAY_CATEGORIES,
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

    def list_device_ids(self) -> set[str]:
        """Ids of all devices currently in the account (no details)."""
        homes = self._get("/v1.0/m/life/users/homes") or []
        if not homes:
            # Never treat an empty answer as "everything was removed"
            raise CloudError("no homes returned")
        ids: set[str] = set()
        for home in homes:
            items = self._get(
                "/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])}
            )
            ids.update(item["id"] for item in items or [] if "id" in item)
        return ids

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
        assign_gateways(devices)
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
            DEV_NODE_ID: _first(item, "node_id", "nodeId", "cid", "mesh_node_id"),
            DEV_PARENT: _first(
                item, "gateway_id", "gatewayId", "parent_id", "parentId", "parent_dev_id"
            ),
            # Only the field names, to understand new cloud payloads from diagnostics
            DEV_RAW_KEYS: sorted(item),
        }


def _first(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if item.get(key):
            return str(item[key])
    return None


def assign_gateways(devices: list[dict[str, Any]]) -> None:
    """Fill in the gateway of sub-devices that the cloud did not link.

    If an explicit parent is missing, a sub-device is attached to the only
    gateway of the account (a gateway category, or a device without DPs).
    """
    by_id = {d[DEV_ID]: d for d in devices}
    mains = [d for d in devices if not d[DEV_SUB]]
    gateways = [d for d in mains if d.get(DEV_CATEGORY) in GATEWAY_CATEGORIES]
    if not gateways:
        gateways = [d for d in mains if not d.get(DEV_DPS)]
    for dev in devices:
        if not dev[DEV_SUB]:
            continue
        parent = dev.get(DEV_PARENT)
        if parent in by_id and not by_id[parent][DEV_SUB]:
            continue
        dev[DEV_PARENT] = gateways[0][DEV_ID] if len(gateways) == 1 else None
