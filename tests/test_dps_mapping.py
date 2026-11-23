"""Unit tests for DP conversion and automatic entity mapping."""

from homeassistant.const import Platform

from custom_components.mylocaltuya.cloud import build_dps
from custom_components.mylocaltuya.dps import DeviceSpec, decode_colour, encode_colour
from custom_components.mylocaltuya.mapping import plan_entities

from .fixtures import AC_SPEC, AC_STATUS, LIGHT_SPEC, LIGHT_STATUS, PLUG_SPEC, PLUG_STATUS


def test_build_dps_plug():
    dps = build_dps(PLUG_SPEC, PLUG_STATUS)
    assert dps["1"]["code"] == "switch_1"
    assert dps["1"]["writable"] is True
    assert dps["19"]["code"] == "cur_power"
    assert dps["19"]["writable"] is False
    assert dps["19"]["values"]["scale"] == 1
    assert dps["38"]["enum_map"] == {"0": "power_off", "1": "power_on", "2": "last"}


def test_conversions():
    spec = DeviceSpec(build_dps(PLUG_SPEC, PLUG_STATUS))
    power = spec.get("cur_power")
    assert power.to_ha(1234) == 123.4
    energy = spec.get("add_ele")
    assert energy.to_ha(921) == 0.921
    relay = spec.get("relay_status")
    assert relay.to_ha("2") == "last"
    assert relay.to_raw("power_on") == "1"
    assert relay.options == ["power_off", "power_on", "last"]
    countdown = spec.get("countdown_1")
    assert countdown.to_raw(100000) == 86400  # clamped


def test_colour_roundtrip():
    raw = encode_colour(120, 0.5, 0.8, "000003e803e8", v2=True)
    assert raw == "007801f40320"
    assert decode_colour(raw) == (120.0, 0.5, 0.8)
    v1 = encode_colour(0, 1.0, 1.0, None, v2=False)
    assert v1 == "ff00000000ffff"
    hue, sat, val = decode_colour(v1)
    assert (hue, sat, val) == (0.0, 1.0, 1.0)
    assert decode_colour('{"h": 240, "s": 1000, "v": 500}') == (240.0, 1.0, 0.5)


def _platforms(plans):
    return {(p.platform, p.key) for p in plans}


def test_plug_plans():
    plans = plan_entities("cz", DeviceSpec(build_dps(PLUG_SPEC, PLUG_STATUS)))
    assert _platforms(plans) == {
        (Platform.SWITCH, "switch_1"),
        (Platform.NUMBER, "countdown_1"),
        (Platform.SENSOR, "add_ele"),
        (Platform.SENSOR, "cur_current"),
        (Platform.SENSOR, "cur_power"),
        (Platform.SENSOR, "cur_voltage"),
        (Platform.SELECT, "relay_status"),
        (Platform.SWITCH, "child_lock"),
    }
    main = next(p for p in plans if p.key == "switch_1")
    assert main.has_name is False  # takes the device name
    countdown = next(p for p in plans if p.key == "countdown_1")
    assert countdown.enabled_default is False


def test_light_plans():
    plans = plan_entities("dj", DeviceSpec(build_dps(LIGHT_SPEC, LIGHT_STATUS)))
    assert len(plans) == 1
    light = plans[0]
    assert light.platform == Platform.LIGHT
    assert set(light.dps) == {"switch", "brightness", "temp", "colour", "mode"}


def test_ac_plans():
    plans = plan_entities("kt", DeviceSpec(build_dps(AC_SPEC, AC_STATUS)))
    assert _platforms(plans) == {(Platform.CLIMATE, "climate"), (Platform.SWITCH, "anion")}
    climate = plans[0]
    assert set(climate.dps) == {"switch", "temp_set", "temp_current", "mode", "fan"}
