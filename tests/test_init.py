"""End-to-end test: entry setup, local push updates and commands."""

import asyncio

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mylocaltuya.const import DOMAIN

from .fixtures import (
    AC_SPEC,
    AC_STATUS,
    LIGHT_SPEC,
    LIGHT_STATUS,
    PLUG_SPEC,
    PLUG_STATUS,
    device_conf,
)


def _entry():
    devices = {
        "plug1": device_conf("plug1", "Presa", "cz", PLUG_SPEC, PLUG_STATUS),
        "bulb1": device_conf("bulb1", "Lampada", "dj", LIGHT_SPEC, LIGHT_STATUS),
        "ac1": device_conf("ac1", "Clima", "kt", AC_SPEC, AC_STATUS),
    }
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="uid",
        title="test",
        data={
            "user_code": "code",
            "terminal_id": "term",
            "endpoint": "https://example.invalid",
            "uid": "uid",
            "username": "me",
            "token_info": {},
            "devices": devices,
        },
    )


async def _wait(hass, predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        await hass.async_block_till_done()
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met")


async def test_setup_and_control(hass: HomeAssistant, fake_tuya) -> None:
    fake_tuya.state = {
        "plug1": {"1": True, "9": 0, "17": 921, "18": 250, "19": 575, "20": 2301, "38": "2", "41": False},
        "bulb1": {"20": True, "21": "white", "22": 1000, "23": 0, "24": "000003e803e8"},
        "ac1": {"1": True, "2": 240, "3": 265, "4": "cold", "5": "2", "12": False},
    }
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _wait(hass, lambda: hass.states.get("switch.presa").state != STATE_UNAVAILABLE)

    # Plug
    assert hass.states.get("switch.presa").state == "on"
    assert hass.states.get("sensor.presa_power").state == "57.5"
    assert hass.states.get("sensor.presa_voltage").state == "230.1"
    assert hass.states.get("sensor.presa_energy").state == "0.921"
    assert hass.states.get("select.presa_power_on_behavior").state == "last"

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.presa"}, blocking=True)
    await _wait(hass, lambda: hass.states.get("switch.presa").state == "off")
    plug = next(d for d in fake_tuya.instances if d.id == "plug1")
    assert plug.sent[-1] == {"1": False}

    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.presa_power_on_behavior", "option": "power_on"}, blocking=True,
    )
    await _wait(hass, lambda: plug.sent[-1] == {"38": "1"})

    # Energy keeps growing when the device resets its counter
    plug.push({"dps": {"17": 5}})
    await _wait(hass, lambda: hass.states.get("sensor.presa_energy").state == "0.926")

    # Light
    await _wait(hass, lambda: hass.states.get("light.lampada").state == "on")
    light = hass.states.get("light.lampada")
    assert light.attributes["color_mode"] == "color_temp"
    assert light.attributes["brightness"] == 255
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.lampada", "hs_color": [120, 50]}, blocking=True
    )
    bulb = next(d for d in fake_tuya.instances if d.id == "bulb1")
    await _wait(hass, lambda: bool(bulb.sent))
    assert bulb.sent[-1] == {"20": True, "21": "colour", "24": "007801f403e8"}
    await _wait(hass, lambda: hass.states.get("light.lampada").attributes.get("color_mode") == "hs")

    # Air conditioner
    await _wait(hass, lambda: hass.states.get("climate.clima").state == "cool")
    climate = hass.states.get("climate.clima")
    assert climate.attributes["temperature"] == 24.0
    assert climate.attributes["current_temperature"] == 26.5
    assert climate.attributes["fan_mode"] == "2"
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.clima", "temperature": 22}, blocking=True
    )
    ac = next(d for d in fake_tuya.instances if d.id == "ac1")
    await _wait(hass, lambda: bool(ac.sent) and ac.sent[-1] == {"2": 220})
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.clima", "hvac_mode": "off"}, blocking=True
    )
    await _wait(hass, lambda: hass.states.get("climate.clima").state == "off")
    assert hass.states.get("switch.clima_ionizer") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_protocol_version_autodetect(hass: HomeAssistant, fake_tuya) -> None:
    """A wrong stored version is corrected automatically and saved."""
    fake_tuya.accepted_version = 3.4
    fake_tuya.state = {
        "plug1": {"1": True},
        "bulb1": {"20": False},
        "ac1": {"1": False},
    }
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _wait(hass, lambda: hass.states.get("switch.presa").state == "on")
    await _wait(hass, lambda: entry.data["devices"]["plug1"]["version"] == "3.4")
    assert await hass.config_entries.async_unload(entry.entry_id)
