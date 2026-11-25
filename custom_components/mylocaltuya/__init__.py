"""myLocalTuya: local control of Tuya devices, configured with a QR code."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

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
    DEV_NODE_ID,
    DEV_PARENT,
    DEV_PRODUCT_NAME,
    DEV_SUB,
    DEV_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .device import TuyaBaseDevice, TuyaLocalDevice, TuyaSubDevice
from .discovery import TuyaDiscovery, async_get_discovery, async_release_discovery
from .mapping import Plan, plan_entities

_LOGGER = logging.getLogger(__name__)

# Keys that belong to the local setup and must survive a cloud refresh
LOCAL_KEYS = (DEV_HOST, DEV_VERSION, DEV_MANUAL_HOST)
# Keys kept from the old config when the cloud does not provide them
STICKY_KEYS = (DEV_PARENT, DEV_NODE_ID)


def merge_device(new: dict, old: dict) -> dict:
    """Merge freshly downloaded data with what we learned locally."""
    merged = {**new, **{k: old[k] for k in LOCAL_KEYS if k in old}}
    for key in STICKY_KEYS:
        if not merged.get(key) and old.get(key):
            merged[key] = old[key]
    return merged


@dataclass
class RuntimeData:
    """Objects living while the entry is loaded."""

    discovery: TuyaDiscovery
    devices: dict[str, TuyaBaseDevice] = field(default_factory=dict)
    plans: dict[TuyaBaseDevice, list[Plan]] = field(default_factory=dict)
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

    @callback
    def node_id_detected(dev_id: str, node_id: str) -> None:
        devices = dict(entry.data[CONF_DEVICES])
        if dev_id not in devices:
            return
        devices[dev_id] = {**devices[dev_id], DEV_NODE_ID: node_id}
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICES: devices}
        )
        if isinstance(device := runtime.devices.get(dev_id), TuyaSubDevice):
            device.async_update_config(devices[dev_id])

    confs = entry.data[CONF_DEVICES]
    # Directly connected devices (and gateways) first, then sub-devices
    for dev_id, conf in sorted(confs.items(), key=lambda kv: bool(kv[1].get(DEV_SUB))):
        device: TuyaBaseDevice
        if conf.get(DEV_SUB):
            device = TuyaSubDevice(hass, conf)
            gateway = runtime.devices.get(conf.get(DEV_PARENT) or "")
            if isinstance(gateway, TuyaLocalDevice):
                gateway.async_add_child(device)
        else:
            device = TuyaLocalDevice(
                hass, conf, discovery, network_changed, need_refresh, node_id_detected
            )
        runtime.devices[dev_id] = device
        runtime.plans[device] = plan_entities(conf.get("category", ""), device.spec)

    # Register devices up front: hubs without entities must exist for via_device
    registry = dr.async_get(hass)
    for device in runtime.devices.values():
        parent = device.conf.get(DEV_PARENT) if device.conf.get(DEV_SUB) else None
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.id)},
            name=device.name,
            manufacturer="Tuya",
            model=device.conf.get(DEV_PRODUCT_NAME) or None,
            via_device=(DOMAIN, parent) if parent in runtime.devices else None,
        )

    # Drop registry devices that are no longer configured
    for dev_entry in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ids = {i for domain, i in dev_entry.identifiers if domain == DOMAIN}
        if not ids & runtime.devices.keys():
            registry.async_update_device(
                dev_entry.id, remove_config_entry_id=entry.entry_id
            )

    for device in runtime.devices.values():
        if isinstance(device, TuyaLocalDevice):
            device.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Devices removed from the Smart Life app disappear here too
    entry.async_create_background_task(
        hass, async_remove_deleted_devices(hass, entry), "mylocaltuya_sync"
    )
    return True


async def async_remove_deleted_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove devices that are no longer in the Tuya account."""
    cloud = TuyaCloud(
        entry.data[CONF_USER_CODE],
        entry.data[CONF_TERMINAL_ID],
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN_INFO],
    )
    try:
        in_cloud = await hass.async_add_executor_job(cloud.list_device_ids)
    except CloudError as err:
        _LOGGER.debug("Cannot check removed devices: %s", err)
        return
    configured = entry.data[CONF_DEVICES]
    removed = set(configured) - in_cloud
    if not removed:
        return
    _LOGGER.info(
        "Removing devices deleted from the Smart Life app: %s",
        ", ".join(configured[i].get("name", i) for i in removed),
    )
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_DEVICES: {i: c for i, c in configured.items() if i not in removed},
            CONF_TOKEN_INFO: cloud.token_info,
        },
    )
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device from the UI (it stops being controlled)."""
    ids = {i for domain, i in device_entry.identifiers if domain == DOMAIN}
    configured = entry.data[CONF_DEVICES]
    if ids & configured.keys():
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_DEVICES: {i: c for i, c in configured.items() if i not in ids},
            },
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyLocalTuyaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for device in entry.runtime_data.devices.values():
            if isinstance(device, TuyaLocalDevice):
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
        merged = merge_device(new, old)
        devices[new["id"]] = merged
        runtime: RuntimeData | None = getattr(entry, "runtime_data", None)
        if runtime and (device := runtime.devices.get(new["id"])) is not None:
            device.async_update_config(merged)

    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_DEVICES: devices, CONF_TOKEN_INFO: cloud.token_info},
    )
    return fetched
