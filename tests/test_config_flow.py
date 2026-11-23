"""Config flow tests (cloud mocked)."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mylocaltuya.const import DOMAIN

from .fixtures import LIGHT_SPEC, LIGHT_STATUS, PLUG_SPEC, PLUG_STATUS, device_conf

LOGIN = {
    "t": 1700000000000,
    "uid": "uid123",
    "expire_time": 7200,
    "access_token": "a",
    "refresh_token": "r",
    "terminal_id": "term",
    "endpoint": "https://apigw.tuyaeu.com",
    "username": "utente",
}


def _devices():
    plug = device_conf("plug1", "Presa", "cz", PLUG_SPEC, PLUG_STATUS)
    bulb = device_conf("bulb1", "Lampada", "dj", LIGHT_SPEC, LIGHT_STATUS)
    for dev in (plug, bulb):
        dev.pop("host")
        dev.pop("version")
    sub = dict(plug, id="sub1", name="Timer acqua", sub=True)
    return [plug, bulb, sub]


async def test_full_flow(hass: HomeAssistant) -> None:
    with (
        patch("custom_components.mylocaltuya.config_flow.qr_login_start", return_value="qrtoken"),
        patch(
            "custom_components.mylocaltuya.config_flow.qr_login_result",
            side_effect=[None, LOGIN],
        ),
        patch(
            "custom_components.mylocaltuya.config_flow.TuyaCloud.fetch_devices",
            return_value=_devices(),
        ),
        patch("custom_components.mylocaltuya.config_flow.DISCOVERY_WAIT", 0.01),
        patch("custom_components.mylocaltuya.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"user_code": " abc123 "}
        )
        assert result["step_id"] == "scan"

        # Not scanned yet
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "scan"
        assert result["errors"] == {"base": "login_failed"}

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "devices"
        assert "Timer acqua" in result["description_placeholders"]["skipped"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"selected": ["plug1"]}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "utente"
    data = result["data"]
    assert data["user_code"] == "abc123"
    assert data["endpoint"] == "https://apigw.tuyaeu.com"
    assert list(data["devices"]) == ["plug1"]
    assert data["devices"]["plug1"]["local_key"] == "0123456789abcdef"
    assert data["token_info"]["refresh_token"] == "r"


async def test_invalid_user_code(hass: HomeAssistant) -> None:
    from custom_components.mylocaltuya.cloud import CloudError

    with patch(
        "custom_components.mylocaltuya.config_flow.qr_login_start",
        side_effect=CloudError("bad"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"user_code": "x"}
        )
    assert result["errors"] == {"base": "invalid_user_code"}
