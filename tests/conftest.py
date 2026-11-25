"""Shared fixtures."""

import socket
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return


@pytest.fixture(autouse=True)
def no_udp():
    """Do not bind real UDP ports."""

    async def _start(self):
        return None

    with patch(
        "custom_components.mylocaltuya.discovery.TuyaDiscovery.start", _start
    ):
        yield


class FakeTuya:
    """Stand-in for tinytuya.Device with a selectable socket."""

    state: dict[str, dict] = {}
    accepted_version: float | None = None
    instances: list["FakeTuya"] = []
    # gateway id -> {cid: online}
    gateways: dict[str, dict[str, bool]] = {}

    def __init__(self, dev_id, address=None, local_key="", version=3.1, persist=False,
                 cid=None, parent=None, **kw):
        self.id = dev_id
        self.address = address
        self.version = version
        self.cid = cid
        self.parent = parent
        self.raw_recv = []
        self.sent = []
        self._pending = []
        if parent is None:
            self.socket, self._peer = socket.socketpair()
            self.socket.setblocking(False)
        FakeTuya.instances.append(self)

    def set_socketRetryLimit(self, _):
        pass

    def set_sendRetryLimit(self, _):
        pass

    def status(self, nowait=False):
        if self.parent is not None:  # sub-device: answer comes asynchronously
            self.parent.push({"dps": dict(FakeTuya.state[self.id]), "cid": self.cid})
            return None
        if nowait:
            return None
        if FakeTuya.accepted_version and self.version != FakeTuya.accepted_version:
            return {"Error": "Check device key or version", "Err": "914"}
        if self.id in FakeTuya.gateways and not FakeTuya.state.get(self.id):
            return {"Error": "Invalid JSON Response from Device", "Err": "900"}
        return {"dps": dict(FakeTuya.state[self.id])}

    def subdev_query(self, nowait=False):
        nodes = FakeTuya.gateways.get(self.id, {})
        self.push({
            "reqType": "subdev_online_stat_query",
            "data": {
                "online": [c for c, on in nodes.items() if on],
                "offline": [c for c, on in nodes.items() if not on],
            },
        })

    def set_multiple_values(self, data, nowait=False):
        self.sent.append(dict(data))
        FakeTuya.state[self.id].update(data)
        if self.parent is not None:
            self.parent.push({"dps": dict(data), "cid": self.cid})
        else:
            self.push({"dps": dict(data)})

    def push(self, msg):
        self._pending.append(msg)
        self._peer.send(b"x")

    def receive(self):
        try:
            self.socket.recv(1024)
        except BlockingIOError:
            pass
        if self._pending:
            self.raw_recv = [1]
            return self._pending.pop(0)
        self.raw_recv = []
        return None

    def heartbeat(self, nowait=True):
        return None

    def updatedps(self, index=None, nowait=False):
        return None

    def close(self):
        if self.parent is not None:
            return
        for sock in (self.socket, self._peer):
            try:
                sock.close()
            except OSError:
                pass


@pytest.fixture(autouse=True)
def cloud_ids():
    """Cloud device list used by the startup sync (default: cloud unreachable)."""
    from custom_components.mylocaltuya.cloud import CloudError

    state = {"ids": None}

    def _list(self):
        if state["ids"] is None:
            raise CloudError("offline in tests")
        return set(state["ids"])

    with patch(
        "custom_components.mylocaltuya.cloud.TuyaCloud.list_device_ids", _list
    ):
        yield state


@pytest.fixture
def fake_tuya():
    """Patch tinytuya.Device."""
    FakeTuya.state = {}
    FakeTuya.instances = []
    FakeTuya.accepted_version = None
    FakeTuya.gateways = {}
    with patch("custom_components.mylocaltuya.device.tinytuya.Device", FakeTuya):
        yield FakeTuya
