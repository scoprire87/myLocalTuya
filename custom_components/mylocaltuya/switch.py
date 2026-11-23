"""Switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyLocalTuyaConfigEntry
from .entity import TuyaLocalEntity, entities_for

OUTLET_CATEGORIES = {"cz", "pc"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyLocalTuyaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    async_add_entities(entities_for(entry.runtime_data, Platform.SWITCH, TuyaSwitch))


class TuyaSwitch(TuyaLocalEntity, SwitchEntity):
    """A boolean DP."""

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        if device.conf.get("category") in OUTLET_CATEGORIES and plan.key.startswith(
            "switch"
        ):
            self._attr_device_class = SwitchDeviceClass.OUTLET

    @property
    def is_on(self) -> bool | None:
        """State."""
        return self.value()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self.async_send({"value": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.async_send({"value": False})
