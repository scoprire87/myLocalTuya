"""Lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_WHITE,
    ColorMode,
    LightEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyLocalTuyaConfigEntry
from .dps import decode_colour, encode_colour
from .entity import TuyaLocalEntity, entities_for

MIN_KELVIN = 2700
MAX_KELVIN = 6500
MODE_WHITE = "white"
MODE_COLOUR = "colour"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyLocalTuyaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lights."""
    async_add_entities(entities_for(entry.runtime_data, Platform.LIGHT, TuyaLight))


class TuyaLight(TuyaLocalEntity, LightEntity):
    """A Tuya light (bulb, strip, dimmer)."""

    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, device, plan) -> None:
        """Initialize."""
        super().__init__(device, plan)
        modes: set[ColorMode] = set()
        if self.role("colour"):
            modes.add(ColorMode.HS)
        if self.role("temp"):
            modes.add(ColorMode.COLOR_TEMP)
        elif modes and self.role("brightness"):
            modes.add(ColorMode.WHITE)
        if not modes:
            modes.add(ColorMode.BRIGHTNESS if self.role("brightness") else ColorMode.ONOFF)
        self._attr_supported_color_modes = modes

    # ---- helpers -------------------------------------------------------------
    @property
    def _v2(self) -> bool:
        dp = self.role("colour")
        return bool(dp and dp.code.startswith("colour_data_v2"))

    def _in_colour_mode(self) -> bool:
        if not self.role("colour"):
            return False
        mode = self.value("mode")
        if mode is not None:
            return str(mode) == MODE_COLOUR
        return not (self.role("brightness") or self.role("temp"))

    @staticmethod
    def _to_ha_scale(raw: Any, low: float, high: float) -> int | None:
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            return None
        if high <= low:
            return None
        return max(1, min(255, round((raw - low) * 255 / (high - low))))

    @staticmethod
    def _from_ha_scale(value: float, low: float, high: float) -> int:
        return round(low + (min(max(value, 1), 255) / 255) * (high - low))

    # ---- state -----------------------------------------------------------------
    @property
    def is_on(self) -> bool | None:
        """On/off."""
        return self.value("switch")

    @property
    def color_mode(self) -> ColorMode:
        """Current colour mode."""
        modes = self._attr_supported_color_modes
        if ColorMode.HS in modes and self._in_colour_mode():
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.WHITE in modes:
            return ColorMode.WHITE
        return next(iter(modes))

    @property
    def brightness(self) -> int | None:
        """Brightness 1-255."""
        if self._in_colour_mode():
            hsv = decode_colour(self.raw("colour"))
            return None if hsv is None else max(1, round(hsv[2] * 255))
        dp = self.role("brightness")
        if dp is None:
            return None
        return self._to_ha_scale(self.raw("brightness"), dp.raw_min, dp.raw_max)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Hue/saturation."""
        hsv = decode_colour(self.raw("colour"))
        return None if hsv is None else (hsv[0], hsv[1] * 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Colour temperature."""
        dp = self.role("temp")
        raw = self.raw("temp")
        if dp is None or raw is None or dp.raw_max <= dp.raw_min:
            return None
        ratio = (float(raw) - dp.raw_min) / (dp.raw_max - dp.raw_min)
        return round(MIN_KELVIN + ratio * (MAX_KELVIN - MIN_KELVIN))

    # ---- commands --------------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally changing colour/brightness."""
        dps: dict[str, Any] = {self.role("switch").id: True}
        mode_dp = self.role("mode")
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if ATTR_WHITE in kwargs:
            brightness = kwargs[ATTR_WHITE]

        def set_mode(mode: str) -> None:
            if mode_dp is not None and mode in mode_dp.options:
                dps[mode_dp.id] = mode_dp.to_raw(mode)

        if ATTR_HS_COLOR in kwargs and (colour := self.role("colour")):
            hue, sat = kwargs[ATTR_HS_COLOR]
            current = decode_colour(self.raw("colour"))
            if brightness is not None:
                val = brightness / 255
            elif current is not None:
                val = current[2]
            else:
                val = 1.0
            set_mode(MODE_COLOUR)
            dps[colour.id] = encode_colour(hue, sat / 100, val, self.raw("colour"), self._v2)
        elif ATTR_COLOR_TEMP_KELVIN in kwargs and (temp := self.role("temp")):
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            ratio = (kelvin - MIN_KELVIN) / (MAX_KELVIN - MIN_KELVIN)
            ratio = min(max(ratio, 0.0), 1.0)
            set_mode(MODE_WHITE)
            dps[temp.id] = round(temp.raw_min + ratio * (temp.raw_max - temp.raw_min))
            if brightness is not None and (bright := self.role("brightness")):
                dps[bright.id] = self._from_ha_scale(brightness, bright.raw_min, bright.raw_max)
        elif brightness is not None:
            colour = self.role("colour")
            bright = self.role("brightness")
            if colour and (self._in_colour_mode() or bright is None) and ATTR_WHITE not in kwargs:
                hsv = decode_colour(self.raw("colour")) or (0.0, 0.0, 1.0)
                dps[colour.id] = encode_colour(
                    hsv[0], hsv[1], brightness / 255, self.raw("colour"), self._v2
                )
            elif bright:
                if ATTR_WHITE in kwargs:
                    set_mode(MODE_WHITE)
                dps[bright.id] = self._from_ha_scale(brightness, bright.raw_min, bright.raw_max)
        await self.device.async_set_dps(dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.async_send({"switch": False})
