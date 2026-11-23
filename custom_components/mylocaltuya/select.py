"""Selects."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    """Set up selects."""
    async_add_entities(entities_for(entry.runtime_data, Platform.SELECT, TuyaSelect))


class TuyaSelect(TuyaLocalEntity, SelectEntity):
    """A writable enum DP."""

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        self._attr_options = plan.dp.options

    @property
    def current_option(self) -> str | None:
        """Current option."""
        value = self.value()
        return None if value is None else str(value)

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        await self.async_send({"value": option})
