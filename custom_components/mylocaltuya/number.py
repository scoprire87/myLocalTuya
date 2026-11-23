"""Numbers."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up numbers."""
    async_add_entities(entities_for(entry.runtime_data, Platform.NUMBER, TuyaNumber))


class TuyaNumber(TuyaLocalEntity, NumberEntity):
    """A writable integer DP."""

    _attr_mode = NumberMode.BOX

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        dp = plan.dp
        self._attr_native_min_value = dp.min
        self._attr_native_max_value = dp.max
        self._attr_native_step = dp.step
        self._attr_native_unit_of_measurement = dp.unit

    @property
    def native_value(self) -> float | None:
        """Value."""
        return self.value()

    async def async_set_native_value(self, value: float) -> None:
        """Set value."""
        await self.async_send({"value": value})
