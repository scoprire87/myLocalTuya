"""Datapoint (DP) helpers: conversions between local raw values and HA."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

UNIT_ALIASES = {
    "℃": "°C",
    "°c": "°C",
    "c": "°C",
    "℉": "°F",
    "°f": "°F",
    "f": "°F",
    "kwh": "kWh",
    "kw·h": "kWh",
    "kw.h": "kWh",
    "kw*h": "kWh",
    "度": "kWh",
    "w": "W",
    "kw": "kW",
    "ma": "mA",
    "a": "A",
    "v": "V",
    "%": "%",
    "s": "s",
    "min": "min",
    "h": "h",
}


def normalize_unit(unit: str | None) -> str | None:
    """Map Tuya units to Home Assistant ones."""
    if not unit:
        return None
    return UNIT_ALIASES.get(unit.strip().lower(), unit.strip())


@dataclass
class DP:
    """A single datapoint of a device."""

    id: str
    code: str
    type: str
    values: dict[str, Any] = field(default_factory=dict)
    writable: bool = False
    convert: str = "default"
    enum_map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, dp_id: str, conf: dict[str, Any]) -> DP:
        """Build from the stored config entry data."""
        return cls(
            id=str(dp_id),
            code=conf["code"],
            type=conf.get("type", "Raw"),
            values=conf.get("values") or {},
            writable=bool(conf.get("writable")),
            convert=conf.get("convert") or "default",
            enum_map=conf.get("enum_map") or {},
        )

    # ---- integer helpers -------------------------------------------------
    @property
    def scale(self) -> int:
        """Decimal scale of an Integer DP."""
        try:
            return int(self.values.get("scale", 0))
        except (TypeError, ValueError):
            return 0

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self.values.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def raw_min(self) -> float:
        """Minimum raw value."""
        return self._num("min", 0)

    @property
    def raw_max(self) -> float:
        """Maximum raw value."""
        return self._num("max", 100)

    @property
    def raw_step(self) -> float:
        """Raw step."""
        return self._num("step", 1) or 1

    @property
    def min(self) -> float:
        """Scaled minimum."""
        return self.raw_min / 10**self.scale

    @property
    def max(self) -> float:
        """Scaled maximum."""
        return self.raw_max / 10**self.scale

    @property
    def step(self) -> float:
        """Scaled step."""
        return self.raw_step / 10**self.scale

    @property
    def unit(self) -> str | None:
        """Home Assistant unit."""
        return normalize_unit(self.values.get("unit"))

    # ---- enum helpers ----------------------------------------------------
    @property
    def options(self) -> list[str]:
        """Enum options (cloud names)."""
        if self.enum_map:
            return [str(v) for v in self.enum_map.values()]
        return [str(v) for v in self.values.get("range") or []]

    # ---- conversions -----------------------------------------------------
    def to_ha(self, raw: Any) -> Any:
        """Convert a raw local value to a Home Assistant value."""
        if raw is None:
            return None
        if self.type == "Boolean":
            if isinstance(raw, str):
                return raw.lower() in ("true", "1", "on")
            return bool(raw)
        if self.type == "Integer":
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if self.scale:
                return round(value / 10**self.scale, self.scale)
            return int(value)
        if self.type == "Enum":
            if self.enum_map:
                return self.enum_map.get(str(raw), self.enum_map.get(str(raw).lower(), raw))
            return str(raw)
        return raw

    def to_raw(self, value: Any) -> Any:
        """Convert a Home Assistant value to the raw local value."""
        if self.type == "Boolean":
            return bool(value)
        if self.type == "Integer":
            raw = round(float(value) * 10**self.scale)
            return int(min(max(raw, self.raw_min), self.raw_max))
        if self.type == "Enum":
            for raw, mapped in self.enum_map.items():
                if mapped == value:
                    return raw
            return value
        return value


class DeviceSpec:
    """All DPs of a device, indexed by code."""

    def __init__(self, dps_conf: dict[str, dict[str, Any]]) -> None:
        """Initialize."""
        self.dps: dict[str, DP] = {
            dp_id: DP.from_config(dp_id, conf) for dp_id, conf in dps_conf.items()
        }
        self.by_code: dict[str, DP] = {dp.code: dp for dp in self.dps.values()}

    def get(self, *codes: str) -> DP | None:
        """Return the first DP that exists among `codes`."""
        for code in codes:
            if code in self.by_code:
                return self.by_code[code]
        return None


# ---- colour helpers ---------------------------------------------------------


def decode_colour(raw: Any) -> tuple[float, float, float] | None:
    """Decode a local colour DP to (hue 0-360, sat 0-1, value 0-1)."""
    if raw is None:
        return None
    if isinstance(raw, dict) or (isinstance(raw, str) and raw.startswith("{")):
        try:
            data = raw if isinstance(raw, dict) else json.loads(raw)
            hue = float(data["h"])
            sat = float(data["s"])
            val = float(data["v"])
        except (KeyError, TypeError, ValueError):
            return None
        div = 1000 if max(sat, val) > 255 else 255
        return hue, sat / div, val / div
    if not isinstance(raw, str):
        return None
    try:
        if len(raw) == 12:  # v2: hhhhssssvvvv, s/v 0-1000
            return (
                float(int(raw[0:4], 16)),
                int(raw[4:8], 16) / 1000,
                int(raw[8:12], 16) / 1000,
            )
        if len(raw) == 14:  # v1: rrggbb0hhhssvv, s/v 0-255
            return (
                float(int(raw[6:10], 16)),
                int(raw[10:12], 16) / 255,
                int(raw[12:14], 16) / 255,
            )
    except ValueError:
        return None
    return None


def encode_colour(
    hue: float, sat: float, val: float, template: Any, v2: bool
) -> Any:
    """Encode (hue 0-360, sat 0-1, val 0-1) in the device's colour format.

    `template` is the current raw value, used to keep the same format.
    """
    hue = int(round(hue)) % 360
    sat = min(max(sat, 0.0), 1.0)
    val = min(max(val, 0.0), 1.0)
    if isinstance(template, dict) or (
        isinstance(template, str) and template.startswith("{")
    ):
        div = 1000 if v2 else 255
        data = {"h": hue, "s": round(sat * div), "v": round(val * div)}
        return data if isinstance(template, dict) else json.dumps(data)
    if (isinstance(template, str) and len(template) == 14) or (
        template is None and not v2
    ):
        import colorsys  # noqa: PLC0415

        red, green, blue = colorsys.hsv_to_rgb(hue / 360, sat, val)
        return "{:02x}{:02x}{:02x}{:04x}{:02x}{:02x}".format(
            round(red * 255),
            round(green * 255),
            round(blue * 255),
            hue,
            round(sat * 255),
            round(val * 255),
        )
    return f"{hue:04x}{round(sat * 1000):04x}{round(val * 1000):04x}"
