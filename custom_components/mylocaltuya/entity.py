"""Base entity for myLocalTuya."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DEV_PARENT, DEV_PRODUCT_NAME, DEV_SUB, DOMAIN
from .device import TuyaBaseDevice
from .dps import DP
from .mapping import Plan


class TuyaLocalEntity(Entity):
    """Entity backed by one or more DPs of a local device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: TuyaBaseDevice, plan: Plan) -> None:
        """Initialize."""
        self.device = device
        self.plan = plan
        self._attr_unique_id = f"{device.id}_{plan.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.name,
            manufacturer="Tuya",
            model=device.conf.get(DEV_PRODUCT_NAME) or None,
        )
        if device.conf.get(DEV_SUB) and device.conf.get(DEV_PARENT):
            self._attr_device_info["via_device"] = (DOMAIN, device.conf[DEV_PARENT])
        if not plan.has_name:
            self._attr_name = None
        elif plan.translation_key:
            self._attr_translation_key = plan.translation_key
            self._attr_translation_placeholders = plan.placeholders
        else:
            self._attr_name = plan.name
        self._attr_entity_category = plan.entity_category
        self._attr_entity_registry_enabled_default = plan.enabled_default

    async def async_added_to_hass(self) -> None:
        """Subscribe to device updates."""
        self.async_on_remove(self.device.async_add_listener(self._handle_update))

    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Device reachable."""
        return self.device.available

    def role(self, name: str) -> DP | None:
        """DP for a role (e.g. 'brightness')."""
        return self.plan.dps.get(name)

    def value(self, name: str = "value") -> Any:
        """Converted HA value of a role."""
        dp = self.plan.dps.get(name)
        if dp is None:
            return None
        return dp.to_ha(self.device.dps.get(dp.id))

    def raw(self, name: str = "value") -> Any:
        """Raw local value of a role."""
        dp = self.plan.dps.get(name)
        return None if dp is None else self.device.dps.get(dp.id)

    async def async_send(self, values: dict[str, Any]) -> None:
        """Send role -> HA value pairs."""
        dps = {}
        for name, val in values.items():
            dp = self.plan.dps[name]
            dps[dp.id] = dp.to_raw(val)
        await self.device.async_set_dps(dps)


def entities_for(runtime, platform, factory) -> list[Entity]:
    """Build the entities of `platform` for all devices."""
    entities: list[Entity] = []
    for device, plans in runtime.plans.items():
        for plan in plans:
            if plan.platform == platform:
                entity = factory(device, plan)
                if entity is not None:
                    entities.append(entity)
    return entities
