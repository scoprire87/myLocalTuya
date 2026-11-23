"""UDP broadcast decoding."""

import json

from tinytuya.core import header as H
from tinytuya.core.message_helper import TuyaMessage, pack_message
from tinytuya.core.udp_helper import encrypt, udpkey

from custom_components.mylocaltuya.discovery import TuyaDiscovery, parse_broadcast

INFO = {"ip": "192.168.1.77", "gwId": "abc123", "active": 2, "version": "3.3", "productKey": "k"}


def test_parse_encrypted_33_broadcast():
    # devices put a 4 byte return code before the encrypted JSON
    payload = b"\x00\x00\x00\x00" + encrypt(json.dumps(INFO).encode(), udpkey)
    msg = TuyaMessage(0, 0x13, 0, payload, 0, True, H.PREFIX_55AA_VALUE, False)
    data = pack_message(msg)
    assert parse_broadcast(data)["gwId"] == "abc123"


def test_parse_garbage():
    assert parse_broadcast(b"hello") is None


def test_handle_records_device():
    discovery = TuyaDiscovery()
    seen = []
    discovery.async_add_listener(lambda dev_id, info: seen.append((dev_id, info)))
    discovery.handle(INFO)
    assert discovery.devices["abc123"] == {"ip": "192.168.1.77", "version": "3.3"}
    assert seen[0][0] == "abc123"
