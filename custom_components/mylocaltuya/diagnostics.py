"""Diagnostics: everything needed to debug a device mapping (keys redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MyLocalTuyaConfigEntry
from .const import CONF_TOKEN_INFO, CONF_UID, CONF_USER_CODE, CONF_USERNAME, DEV_LOCAL_KEY

TO_REDACT = {CONF_TOKEN_INFO, CONF_USER_CODE, CONF_UID, CONF_USERNAME, DEV_LOCAL_KEY, "terminal_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MyLocalTuyaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics."""
    runtime = entry.runtime_data
    devices = {}
    for dev_id, device in runtime.devices.items():
        devices[dev_id] = {
            "available": device.available,
            "connected_host": getattr(device, "connected_host", None),
            "connected_version": getattr(device, "connected_version", None),
            "node_id": getattr(device, "node_id", None),
            "children": list(getattr(device, "children", {})),
            "unknown_node_ids": getattr(device, "unknown_cids", []),
            "last_error": device.last_error,
            "dps_state": device.dps,
            "entities": [
                {"platform": str(p.platform), "key": p.key, "dps": {r: d.code for r, d in p.dps.items()}}
                for p in runtime.plans.get(device, [])
            ],
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "discovered": runtime.discovery.devices,
        "devices": devices,
    }
