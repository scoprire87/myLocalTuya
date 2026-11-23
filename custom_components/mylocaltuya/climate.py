"""Climate (air conditioners, thermostats, heaters)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyLocalTuyaConfigEntry
from .entity import TuyaLocalEntity, entities_for

# Home Assistant 2026.11 renamed the temperature members to native_*
_NATIVE = hasattr(ClimateEntity, "native_current_temperature")

# Tuya mode value -> HA mode (first match wins when mapping back)
TUYA_TO_HVAC = {
    "cold": HVACMode.COOL,
    "cool": HVACMode.COOL,
    "cooling": HVACMode.COOL,
    "hot": HVACMode.HEAT,
    "heat": HVACMode.HEAT,
    "heating": HVACMode.HEAT,
    "wet": HVACMode.DRY,
    "dry": HVACMode.DRY,
    "dehumidification": HVACMode.DRY,
    "wind": HVACMode.FAN_ONLY,
    "fan": HVACMode.FAN_ONLY,
    "fan_only": HVACMode.FAN_ONLY,
    "auto": HVACMode.AUTO,
    "smart": HVACMode.AUTO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyLocalTuyaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities."""
    async_add_entities(entities_for(entry.runtime_data, Platform.CLIMATE, TuyaClimate))


class TuyaClimate(TuyaLocalEntity, ClimateEntity):
    """A Tuya climate device."""

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        features = ClimateEntityFeature(0)

        temp_set = self.role("temp_set")
        unit = (temp_set.unit if temp_set else None) or ""
        code = temp_set.code if temp_set else ""
        temp_unit = (
            UnitOfTemperature.FAHRENHEIT
            if "F" in unit or code.endswith("_f")
            else UnitOfTemperature.CELSIUS
        )
        if _NATIVE:
            self._attr_native_temperature_unit = temp_unit
        else:
            self._attr_temperature_unit = temp_unit
        if temp_set is not None:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_min_temp = temp_set.min
            self._attr_max_temp = temp_set.max
            self._attr_target_temperature_step = temp_set.step

        # HVAC modes
        self._mode_map: dict[HVACMode, str] = {}
        if (mode := self.role("mode")) is not None:
            for option in mode.options:
                hvac = TUYA_TO_HVAC.get(option.lower())
                if hvac is not None and hvac not in self._mode_map:
                    self._mode_map[hvac] = option
        modes = list(self._mode_map)
        if self.role("switch") is not None:
            modes.insert(0, HVACMode.OFF)
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            if not self._mode_map:
                modes.append(HVACMode.AUTO)
        self._attr_hvac_modes = modes or [HVACMode.AUTO]

        if (fan := self.role("fan")) is not None and fan.type == "Enum":
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = fan.options
        self._attr_supported_features = features

    @property
    def native_current_temperature(self) -> float | None:
        """Room temperature."""
        return self.value("temp_current")

    @property
    def native_target_temperature(self) -> float | None:
        """Target temperature."""
        return self.value("temp_set")

    if not _NATIVE:  # Home Assistant before 2026.11

        @property
        def current_temperature(self) -> float | None:
            """Room temperature."""
            return self.native_current_temperature

        @property
        def target_temperature(self) -> float | None:
            """Target temperature."""
            return self.native_target_temperature

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Current mode."""
        if self.role("switch") is not None and self.value("switch") is False:
            return HVACMode.OFF
        mode = self.value("mode")
        if mode is not None:
            hvac = TUYA_TO_HVAC.get(str(mode).lower())
            if hvac in self._attr_hvac_modes:
                return hvac
        if self.value("switch"):
            return next((m for m in self._attr_hvac_modes if m != HVACMode.OFF), None)
        return None

    @property
    def fan_mode(self) -> str | None:
        """Fan speed."""
        value = self.value("fan")
        return None if value is None else str(value)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set mode."""
        values: dict[str, Any] = {}
        if hvac_mode == HVACMode.OFF:
            values["switch"] = False
        else:
            if self.role("switch") is not None:
                values["switch"] = True
            if hvac_mode in self._mode_map:
                values["mode"] = self._mode_map[hvac_mode]
        await self.async_send(values)

    async def async_turn_on(self) -> None:
        """Turn on."""
        await self.async_send({"switch": True})

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_send({"switch": False})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature (and optionally the mode)."""
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.async_send({"temp_set": temp})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed."""
        await self.async_send({"fan": fan_mode})
