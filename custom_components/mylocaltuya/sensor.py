"""Sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyLocalTuyaConfigEntry
from .entity import TuyaLocalEntity, entities_for

# code -> (device class, state class, default unit)
SENSOR_TYPES: dict[str, tuple[SensorDeviceClass | None, SensorStateClass | None, str | None]] = {
    "cur_power": (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT, "W"),
    "cur_current": (SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mA"),
    "cur_voltage": (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "V"),
    "add_ele": (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "kWh"),
    "temp_current": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "°C"),
    "temp_indoor": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "°C"),
    "va_temperature": (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "°C"),
    "humidity_value": (SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT, "%"),
    "va_humidity": (SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT, "%"),
    "battery_percentage": (SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "%"),
    "residual_electricity": (SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, "%"),
}

ENERGY_CODES = {"add_ele"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyLocalTuyaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""

    def factory(device, plan):
        if plan.dp.code in ENERGY_CODES:
            return TuyaEnergySensor(device, plan)
        return TuyaSensor(device, plan)

    async_add_entities(entities_for(entry.runtime_data, Platform.SENSOR, factory))


class TuyaSensor(TuyaLocalEntity, SensorEntity):
    """A read-only integer or enum DP."""

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        dp = plan.dp
        if dp.type == "Enum":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = dp.options
            return
        device_class, state_class, unit = SENSOR_TYPES.get(dp.code, (None, None, None))
        self._attr_device_class = device_class
        self._attr_state_class = state_class or SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = dp.unit or unit
        if dp.scale:
            self._attr_suggested_display_precision = dp.scale

    @property
    def native_value(self) -> Any:
        """Value."""
        value = self.value()
        if value is not None and self.plan.dp.type == "Enum":
            value = str(value)
            if value not in (self._attr_options or []):
                return None
        return value


class TuyaEnergySensor(TuyaSensor, RestoreSensor):
    """Energy counter that never goes backwards.

    Many plugs reset their local `add_ele` counter after uploading it to the
    cloud: we accumulate increments so the total keeps growing and can be
    used directly by the Energy dashboard and utility meters.
    """

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        self._total: float | None = None
        self._last_raw: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the accumulated total."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                self._total = float(last.native_value)
            except (TypeError, ValueError):
                self._total = None
        self._accumulate()

    def _accumulate(self) -> None:
        current = self.value()
        if current is None:
            return
        current = float(current)
        if self._total is None:
            self._total = current
        elif self._last_raw is not None:
            if current >= self._last_raw:
                self._total += current - self._last_raw
            else:  # counter was reset by the device
                self._total += current
        self._last_raw = current

    def _handle_update(self) -> None:
        self._accumulate()
        super()._handle_update()

    @property
    def native_value(self) -> float | None:
        """Accumulated total."""
        if self._total is None:
            return None
        return round(self._total, max(self.plan.dp.scale, 3))
