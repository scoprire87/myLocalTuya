"""Binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyLocalTuyaConfigEntry
from .entity import TuyaLocalEntity, entities_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyLocalTuyaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities(
        entities_for(entry.runtime_data, Platform.BINARY_SENSOR, TuyaBinarySensor)
    )


class TuyaBinarySensor(TuyaLocalEntity, BinarySensorEntity):
    """A read-only boolean DP."""

    @property
    def is_on(self) -> bool | None:
        """State."""
        return self.value()
