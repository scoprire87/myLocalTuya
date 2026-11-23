"""Turn a device's DP table into Home Assistant entities, automatically."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from homeassistant.const import EntityCategory, Platform

from .dps import DP, DeviceSpec

LIGHT_CATEGORIES = {"dj", "dd", "xdd", "fwd", "dc", "fsd", "tgq", "tgkg", "gyd", "sxd"}
CLIMATE_CATEGORIES = {"kt", "ktkzq", "wk", "wkf", "qn", "rs", "wkcz"}

# Codes that are configuration rather than control
CONFIG_CODES = {
    "relay_status",
    "light_mode",
    "child_lock",
    "switch_inching",
    "overcharge_switch",
    "random_time",
    "cycle_time",
    "led_type_1",
    "led_type",
    "fan_beep",
    "beep",
    "switch_backlight",
    "temp_unit_convert",
    "c_f",
}
DIAGNOSTIC_CODES = {"fault", "test_bit", "voltage_coe", "electric_coe", "power_coe", "electricity_coe"}
DISABLED_CODES = {
    "test_bit",
    "voltage_coe",
    "electric_coe",
    "power_coe",
    "electricity_coe",
    "random_time",
    "cycle_time",
    "switch_inching",
}
DISABLED_PREFIXES = ("countdown",)

# Codes for which we ship a translation (entity.<platform>.<code>)
TRANSLATED = {
    Platform.SWITCH: {
        "child_lock",
        "anion",
        "sleep",
        "health",
        "lock",
        "swing",
        "switch_backlight",
        "light",
        "beep",
        "fan_beep",
        "eco",
        "overcharge_switch",
        "switch_usb",
        "switch_weather",
    },
    Platform.SENSOR: {
        "cur_power",
        "cur_current",
        "cur_voltage",
        "add_ele",
        "temp_current",
        "humidity_value",
        "battery_percentage",
        "fault",
        "work_state",
        "time_use",
        "use_time",
    },
    Platform.SELECT: {
        "relay_status",
        "light_mode",
        "work_mode",
        "mode",
        "windspeed",
        "weather_delay",
        "work_state",
    },
    Platform.NUMBER: set(),
    Platform.BINARY_SENSOR: set(),
}

_NUMBERED = re.compile(r"^(?P<base>[a-z_]+?)_?(?P<idx>\d+)$")


@dataclass
class Plan:
    """An entity to create."""

    platform: Platform
    key: str  # unique part of the unique_id
    dps: dict[str, DP] = field(default_factory=dict)  # role -> DP
    translation_key: str | None = None
    placeholders: dict[str, str] = field(default_factory=dict)
    name: str | None = None
    has_name: bool = True
    entity_category: EntityCategory | None = None
    enabled_default: bool = True

    @property
    def dp(self) -> DP:
        """Main DP."""
        return self.dps["value"]


def humanize(code: str) -> str:
    """'switch_usb1' -> 'Switch usb1'."""
    return code.replace("_", " ").strip().capitalize()


def _naming(platform: Platform, dp: DP) -> tuple[str | None, dict[str, str], str | None]:
    """Return (translation_key, placeholders, fallback_name)."""
    code = dp.code
    if code in TRANSLATED.get(platform, set()):
        return code, {}, None
    match = _NUMBERED.match(code)
    if match:
        base, idx = match["base"], match["idx"]
        if platform == Platform.SWITCH and base in ("switch", "switch_usb"):
            key = "switch_usb_n" if base == "switch_usb" else "switch_n"
            return key, {"index": idx}, None
        if platform == Platform.NUMBER and base == "countdown":
            return "countdown_n", {"index": idx}, None
    if platform == Platform.NUMBER and code == "countdown":
        return "countdown", {}, None
    return None, {}, humanize(code)


def _light_plans(spec: DeviceSpec, used: set[str]) -> list[Plan]:
    plans = []
    for dp in spec.dps.values():
        if not dp.code.startswith("switch_led") or dp.type != "Boolean":
            continue
        suffix = dp.code[len("switch_led") :]
        roles = {"switch": dp}
        for role, codes in (
            ("brightness", (f"bright_value_v2{suffix}", f"bright_value{suffix}")),
            ("temp", (f"temp_value_v2{suffix}", f"temp_value{suffix}")),
            ("colour", (f"colour_data_v2{suffix}", f"colour_data{suffix}")),
            ("mode", (f"work_mode{suffix}",)),
        ):
            found = spec.get(*codes)
            if found:
                roles[role] = found
        used.update(d.code for d in roles.values())
        plans.append(
            Plan(
                Platform.LIGHT,
                key=dp.code,
                dps=roles,
                has_name=bool(suffix),
                name=f"Luce {suffix.strip('_')}" if suffix else None,
            )
        )
    return plans


def _climate_plan(category: str, spec: DeviceSpec, used: set[str]) -> Plan | None:
    temp_set = spec.get("temp_set", "temp_set_f")
    if temp_set is None and category not in CLIMATE_CATEGORIES:
        return None
    roles: dict[str, DP] = {}
    for role, codes in (
        ("switch", ("switch",)),
        ("temp_set", ("temp_set", "temp_set_f")),
        ("temp_current", ("temp_current", "temp_current_f", "temp_indoor")),
        ("mode", ("mode",)),
        ("fan", ("windspeed", "fan_speed_enum", "wind", "level")),
    ):
        found = spec.get(*codes)
        if found is not None:
            roles[role] = found
    if "temp_set" not in roles and "switch" not in roles:
        return None
    used.update(d.code for d in roles.values())
    return Plan(Platform.CLIMATE, key="climate", dps=roles, has_name=False)


def plan_entities(category: str, spec: DeviceSpec) -> list[Plan]:
    """Return the entities to create for a device."""
    used: set[str] = set()
    plans: list[Plan] = []

    if category in LIGHT_CATEGORIES or spec.get("switch_led"):
        plans.extend(_light_plans(spec, used))
    if category in CLIMATE_CATEGORIES or spec.get("temp_set", "temp_set_f"):
        if (climate := _climate_plan(category, spec, used)) is not None:
            plans.append(climate)

    # A single main switch takes the device name
    main_switches = [
        dp
        for dp in spec.dps.values()
        if dp.code not in used
        and dp.type == "Boolean"
        and dp.writable
        and re.fullmatch(r"switch(_\d+)?", dp.code)
    ]
    main_code = main_switches[0].code if len(main_switches) == 1 else None

    for dp in sorted(spec.dps.values(), key=lambda d: int(d.id) if d.id.isdigit() else 0):
        if dp.code in used:
            continue
        if dp.type == "Boolean":
            platform = Platform.SWITCH if dp.writable else Platform.BINARY_SENSOR
        elif dp.type == "Integer":
            platform = Platform.NUMBER if dp.writable else Platform.SENSOR
        elif dp.type == "Enum":
            platform = Platform.SELECT if dp.writable else Platform.SENSOR
        else:
            continue  # Json / Raw / String / Bitmap: not mapped automatically

        translation_key, placeholders, name = _naming(platform, dp)
        category_ = None
        if dp.code in CONFIG_CODES or dp.code.startswith("countdown"):
            category_ = EntityCategory.CONFIG
        elif dp.code in DIAGNOSTIC_CODES:
            category_ = EntityCategory.DIAGNOSTIC
        plans.append(
            Plan(
                platform,
                key=dp.code,
                dps={"value": dp},
                translation_key=translation_key,
                placeholders=placeholders,
                name=name,
                has_name=dp.code != main_code,
                entity_category=category_,
                enabled_default=not (
                    dp.code in DISABLED_CODES or dp.code.startswith(DISABLED_PREFIXES)
                ),
            )
        )
    return plans
