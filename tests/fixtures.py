"""Cloud-shaped sample data for a plug, a bulb and an air conditioner."""

import json


def _rel(dp_id, code, value_type, desc, convert="default", enum_map=None):
    return {
        "dpId": dp_id,
        "statusFormat": json.dumps({code: "$"}),
        "valueType": value_type,
        "valueDesc": json.dumps(desc),
        "valueConvert": convert,
        "enumMappingMap": enum_map or {},
        "supportLocal": True,
    }


def _spec(functions, status):
    return {
        "functions": [
            {"code": c, "type": t, "values": json.dumps(v)} for c, t, v in functions
        ],
        "status": [{"code": c, "type": t, "values": json.dumps(v)} for c, t, v in status],
    }


PLUG_SPEC = _spec(
    [
        ("switch_1", "Boolean", {}),
        ("countdown_1", "Integer", {"unit": "s", "min": 0, "max": 86400, "scale": 0, "step": 1}),
        ("relay_status", "Enum", {"range": ["power_off", "power_on", "last"]}),
        ("child_lock", "Boolean", {}),
    ],
    [
        ("switch_1", "Boolean", {}),
        ("countdown_1", "Integer", {"unit": "s", "min": 0, "max": 86400, "scale": 0, "step": 1}),
        ("add_ele", "Integer", {"unit": "kwh", "min": 0, "max": 50000, "scale": 3, "step": 100}),
        ("cur_current", "Integer", {"unit": "mA", "min": 0, "max": 30000, "scale": 0, "step": 1}),
        ("cur_power", "Integer", {"unit": "W", "min": 0, "max": 80000, "scale": 1, "step": 1}),
        ("cur_voltage", "Integer", {"unit": "V", "min": 0, "max": 5000, "scale": 1, "step": 1}),
        ("relay_status", "Enum", {"range": ["power_off", "power_on", "last"]}),
        ("child_lock", "Boolean", {}),
    ],
)
PLUG_STATUS = {
    "dpStatusRelationDTOS": [
        _rel(1, "switch_1", "bool", {}),
        _rel(9, "countdown_1", "value", {"min": 0, "max": 86400}),
        _rel(17, "add_ele", "value", {"min": 0, "max": 50000, "scale": 3}),
        _rel(18, "cur_current", "value", {}),
        _rel(19, "cur_power", "value", {}),
        _rel(20, "cur_voltage", "value", {}),
        _rel(
            38,
            "relay_status",
            "enum",
            {},
            convert="enum",
            enum_map={"0": {"value": "power_off"}, "1": {"value": "power_on"}, "2": {"value": "last"}},
        ),
        _rel(41, "child_lock", "bool", {}),
    ]
}

LIGHT_SPEC = _spec(
    [
        ("switch_led", "Boolean", {}),
        ("work_mode", "Enum", {"range": ["white", "colour", "scene", "music"]}),
        ("bright_value_v2", "Integer", {"min": 10, "max": 1000, "scale": 0, "step": 1}),
        ("temp_value_v2", "Integer", {"min": 0, "max": 1000, "scale": 0, "step": 1}),
        ("colour_data_v2", "Json", {"h": {"min": 0, "max": 360}}),
    ],
    [
        ("switch_led", "Boolean", {}),
        ("work_mode", "Enum", {"range": ["white", "colour", "scene", "music"]}),
        ("bright_value_v2", "Integer", {"min": 10, "max": 1000, "scale": 0, "step": 1}),
        ("temp_value_v2", "Integer", {"min": 0, "max": 1000, "scale": 0, "step": 1}),
        ("colour_data_v2", "Json", {"h": {"min": 0, "max": 360}}),
    ],
)
LIGHT_STATUS = {
    "dpStatusRelationDTOS": [
        _rel(20, "switch_led", "bool", {}),
        _rel(21, "work_mode", "enum", {}),
        _rel(22, "bright_value_v2", "value", {}),
        _rel(23, "temp_value_v2", "value", {}),
        _rel(24, "colour_data_v2", "string", {}, convert="dj_v2_color_alg"),
    ]
}

AC_SPEC = _spec(
    [
        ("switch", "Boolean", {}),
        ("temp_set", "Integer", {"unit": "℃", "min": 160, "max": 310, "scale": 1, "step": 10}),
        ("mode", "Enum", {"range": ["cold", "hot", "wet", "wind", "auto"]}),
        ("windspeed", "Enum", {"range": ["1", "2", "3", "4"]}),
        ("anion", "Boolean", {}),
    ],
    [
        ("switch", "Boolean", {}),
        ("temp_set", "Integer", {"unit": "℃", "min": 160, "max": 310, "scale": 1, "step": 10}),
        ("temp_current", "Integer", {"unit": "℃", "min": -200, "max": 1000, "scale": 1, "step": 1}),
        ("mode", "Enum", {"range": ["cold", "hot", "wet", "wind", "auto"]}),
        ("windspeed", "Enum", {"range": ["1", "2", "3", "4"]}),
        ("anion", "Boolean", {}),
    ],
)
AC_STATUS = {
    "dpStatusRelationDTOS": [
        _rel(1, "switch", "bool", {}),
        _rel(2, "temp_set", "value", {}),
        _rel(3, "temp_current", "value", {}),
        _rel(4, "mode", "enum", {}),
        _rel(5, "windspeed", "enum", {}),
        _rel(12, "anion", "bool", {}),
    ]
}


def device_conf(dev_id, name, category, spec, status, host="192.168.1.50"):
    """Stored device config as produced by the cloud download."""
    from custom_components.mylocaltuya.cloud import build_dps

    return {
        "id": dev_id,
        "name": name,
        "local_key": "0123456789abcdef",
        "category": category,
        "product_id": "pid",
        "product_name": "Test product",
        "sub": False,
        "dps": build_dps(spec, status),
        "host": host,
        "version": "3.3",
    }


VALVE_SPEC = _spec(
    [
        ("switch", "Boolean", {}),
        ("countdown", "Integer", {"unit": "s", "min": 0, "max": 86400, "scale": 0, "step": 1}),
        ("weather_delay", "Enum", {"range": ["cancel", "24h", "48h", "72h"]}),
    ],
    [
        ("switch", "Boolean", {}),
        ("countdown", "Integer", {"unit": "s", "min": 0, "max": 86400, "scale": 0, "step": 1}),
        ("weather_delay", "Enum", {"range": ["cancel", "24h", "48h", "72h"]}),
        ("battery_percentage", "Integer", {"unit": "%", "min": 0, "max": 100, "scale": 0, "step": 1}),
        ("work_state", "Enum", {"range": ["auto", "manual", "idle"]}),
    ],
)
VALVE_STATUS = {
    "dpStatusRelationDTOS": [
        _rel(1, "switch", "bool", {}),
        _rel(11, "countdown", "value", {}),
        _rel(10, "weather_delay", "enum", {}),
        _rel(7, "battery_percentage", "value", {}),
        _rel(12, "work_state", "enum", {}),
    ]
}


def hub_conf(dev_id, name, host="192.168.1.60"):
    """A hub without DPs of its own."""
    return {
        "id": dev_id,
        "name": name,
        "local_key": "fedcba9876543210",
        "category": "wfcon",
        "product_id": "hubpid",
        "product_name": "Wi-Fi hub",
        "sub": False,
        "dps": {},
        "host": host,
        "version": "3.3",
    }


def valve_conf(dev_id, name, parent, node_id=None):
    """A water timer behind a hub."""
    from custom_components.mylocaltuya.cloud import build_dps

    return {
        "id": dev_id,
        "name": name,
        "local_key": "",
        "category": "sfkzq",
        "product_id": "valvepid",
        "product_name": "Smart Water Timer",
        "sub": True,
        "dps": build_dps(VALVE_SPEC, VALVE_STATUS),
        "parent_id": parent,
        "node_id": node_id,
    }
