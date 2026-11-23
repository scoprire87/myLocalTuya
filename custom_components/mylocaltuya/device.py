"""Persistent local connection to Tuya devices.

Each directly connected device (Wi-Fi device or gateway/hub) gets a small
worker thread that keeps a TCP connection open (tinytuya, persistent
socket), receives pushed status updates, sends heartbeats and executes
commands queued by Home Assistant.

Sub-devices (Zigbee/BLE devices behind a hub) have no connection of their
own: they live on the connection of their gateway and are addressed with
their node id ("cid").
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import queue
import select
import socket
import threading
import time
from typing import Any

import tinytuya

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONNECTION_DEAD_AFTER,
    DEV_HOST,
    DEV_ID,
    DEV_LOCAL_KEY,
    DEV_MANUAL_HOST,
    DEV_NAME,
    DEV_NODE_ID,
    DEV_VERSION,
    FAILURES_BEFORE_CLOUD_REFRESH,
    HEARTBEAT_INTERVAL,
    POLL_INTERVAL,
    PROTOCOL_VERSIONS,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
)
from .discovery import TuyaDiscovery
from .dps import DeviceSpec

_LOGGER = logging.getLogger(__name__)

# tinytuya error codes meaning "cannot reach the device at all"
_UNREACHABLE = {"901", "902", "905"}
# tinytuya error codes meaning "wrong local key or protocol version"
_BAD_KEY = {"904", "914"}

SUBDEV_QUERY = "subdev_online_stat_query"


def _error(result: Any) -> str | None:
    if isinstance(result, dict) and "Err" in result:
        return str(result["Err"])
    return None


def _payload(msg: Any) -> dict[str, Any]:
    """Return the part of a message holding dps/cid (some protocols nest it)."""
    if not isinstance(msg, dict):
        return {}
    data = msg.get("data")
    if "dps" not in msg and isinstance(data, dict) and ("dps" in data or "cid" in data):
        return {**data, **{k: v for k, v in msg.items() if k != "data"}}
    return msg


def message_cid(msg: Any) -> str | None:
    """Node id a message is about, if any."""
    cid = _payload(msg).get("cid")
    return str(cid) if cid else None


def message_dps(msg: Any) -> dict[str, Any] | None:
    """Dps of a message, keys as strings."""
    dps = _payload(msg).get("dps")
    if not isinstance(dps, dict):
        return None
    return {str(k): v for k, v in dps.items()}


def message_online(msg: Any) -> tuple[list[str], list[str]] | None:
    """(online, offline) node ids of a sub-device status answer."""
    if not isinstance(msg, dict):
        return None
    data = msg.get("data")
    if msg.get("reqType") != SUBDEV_QUERY and not (
        isinstance(data, dict) and ("online" in data or "offline" in data)
    ):
        return None
    if not isinstance(data, dict):
        return None
    return (
        [str(c) for c in data.get("online") or []],
        [str(c) for c in data.get("offline") or []],
    )


class TuyaBaseDevice:
    """State shared by every device, local or behind a gateway."""

    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        """Initialize."""
        self.hass = hass
        self._conf = conf
        self.spec = DeviceSpec(conf.get("dps") or {})
        self.dps: dict[str, Any] = {}
        self.available = False
        self.last_error: str | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def id(self) -> str:
        """Device id."""
        return self._conf[DEV_ID]

    @property
    def name(self) -> str:
        """Device name."""
        return self._conf.get(DEV_NAME) or self.id

    @property
    def conf(self) -> dict[str, Any]:
        """Stored configuration."""
        return self._conf

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a state listener."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _async_set_state(self, dps: dict[str, Any] | None, available: bool) -> None:
        if dps:
            self.dps.update(dps)
        self.available = available
        for cb in list(self._listeners):
            cb()

    async def async_set_dps(self, dps: dict[str, Any]) -> None:
        """Send new values (dp id -> raw value) to the device."""
        raise NotImplementedError


class TuyaSubDevice(TuyaBaseDevice):
    """A device reached through a gateway."""

    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        """Initialize."""
        super().__init__(hass, conf)
        self.gateway: TuyaLocalDevice | None = None
        self.detected_node_id: str | None = None
        self.last_error = "gateway non configurato"

    @property
    def node_id(self) -> str | None:
        """Node id (cid) on the gateway."""
        return self._conf.get(DEV_NODE_ID) or self.detected_node_id

    @callback
    def async_update_config(self, conf: dict[str, Any]) -> None:
        """Use a new configuration."""
        self._conf = conf
        self.spec = DeviceSpec(conf.get("dps") or {})

    async def async_set_dps(self, dps: dict[str, Any]) -> None:
        """Send new values through the gateway."""
        if not self.available or self.gateway is None:
            raise HomeAssistantError(f"{self.name} non è raggiungibile")
        _LOGGER.debug("%s (via gateway): set %s", self.id, dps)
        self.gateway.enqueue(self.id, dps)


class TuyaLocalDevice(TuyaBaseDevice):
    """A Tuya device (or gateway) controlled over the LAN."""

    def __init__(
        self,
        hass: HomeAssistant,
        conf: dict[str, Any],
        discovery: TuyaDiscovery,
        on_network_change: Callable[[str, str, str], None],
        on_need_refresh: Callable[[str], None],
        on_node_id: Callable[[str, str], None] | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(hass, conf)
        self._discovery = discovery
        self._on_network_change = on_network_change
        self._on_need_refresh = on_need_refresh
        self._on_node_id = on_node_id

        self.connected_host: str | None = None
        self.connected_version: str | None = None
        self.children: dict[str, TuyaSubDevice] = {}
        self.unknown_cids: list[str] = []

        self._commands: queue.Queue[tuple[str | None, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._thread: threading.Thread | None = None
        self._remove_discovery: Callable[[], None] | None = None
        # worker-thread only
        self._tt_children: dict[str, tinytuya.Device] = {}

    # ---- children --------------------------------------------------------------
    @callback
    def async_add_child(self, child: TuyaSubDevice) -> None:
        """Attach a sub-device to this gateway."""
        child.gateway = self
        child.last_error = None
        self.children[child.id] = child

    @property
    def is_gateway(self) -> bool:
        """True if sub-devices are attached."""
        return bool(self.children)

    # ---- lifecycle -----------------------------------------------------------
    @callback
    def async_start(self) -> None:
        """Start the worker thread."""
        self._remove_discovery = self._discovery.async_add_listener(
            self._async_discovered
        )
        self._thread = threading.Thread(
            target=self._run, name=f"mylocaltuya-{self.id}", daemon=True
        )
        self._thread.start()

    async def async_stop(self) -> None:
        """Stop the worker thread."""
        if self._remove_discovery:
            self._remove_discovery()
            self._remove_discovery = None
        self._stop.set()
        self._poke()
        if self._thread:
            await self.hass.async_add_executor_job(self._thread.join, 10)
        self._wake_r.close()
        self._wake_w.close()

    @callback
    def async_update_config(self, conf: dict[str, Any]) -> None:
        """Use a new configuration (e.g. a refreshed local key)."""
        key_changed = conf.get(DEV_LOCAL_KEY) != self._conf.get(DEV_LOCAL_KEY)
        self._conf = conf
        self.spec = DeviceSpec(conf.get("dps") or {})
        if key_changed or not self.available:
            self._poke()

    def _poke(self) -> None:
        self._wake.set()
        try:
            self._wake_w.send(b"x")
        except OSError:
            pass

    # ---- state -------------------------------------------------------------------
    @callback
    def _async_set_state(self, dps: dict[str, Any] | None, available: bool) -> None:
        super()._async_set_state(dps, available)
        if not available:
            for child in self.children.values():
                if child.available:
                    child._async_set_state(None, False)  # noqa: SLF001

    @callback
    def _async_child_state(
        self, child_id: str, dps: dict[str, Any] | None, available: bool
    ) -> None:
        if (child := self.children.get(child_id)) is not None:
            child._async_set_state(dps, available)  # noqa: SLF001

    @callback
    def _async_discovered(self, dev_id: str, info: dict[str, Any]) -> None:
        if dev_id != self.id:
            return
        ip, version = info.get("ip"), info.get("version")
        if ip and (
            ip != self._conf.get(DEV_HOST) or version != self._conf.get(DEV_VERSION)
        ):
            self._on_network_change(self.id, ip, version or "")
        if not self.available:
            self._poke()

    # ---- commands ------------------------------------------------------------
    def enqueue(self, target: str | None, dps: dict[str, Any]) -> None:
        """Queue a command for this device (target None) or a child."""
        self._commands.put((target, dict(dps)))
        self._poke()

    async def async_set_dps(self, dps: dict[str, Any]) -> None:
        """Send new values (dp id -> raw value) to the device."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} non è raggiungibile")
        _LOGGER.debug("%s: set %s", self.id, dps)
        self.enqueue(None, dps)

    # ---- worker thread -------------------------------------------------------
    def _call(self, func: Callable, *args: Any) -> None:
        self.hass.loop.call_soon_threadsafe(func, *args)

    def _target(self) -> tuple[str | None, str | None]:
        found = self._discovery.devices.get(self.id, {})
        host = (
            self._conf.get(DEV_MANUAL_HOST)
            or found.get("ip")
            or self._conf.get(DEV_HOST)
        )
        version = found.get("version") or self._conf.get(DEV_VERSION)
        if self._conf.get(DEV_MANUAL_HOST) and self._conf.get(DEV_VERSION):
            version = self._conf[DEV_VERSION]
        return host, (version or None)

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        delay = RECONNECT_MIN_DELAY
        failures = 0
        while not self._stop.is_set():
            try:
                host, version = self._target()
                if not host:
                    self.last_error = "IP sconosciuto (in attesa di broadcast)"
                    self._sleep(RECONNECT_MAX_DELAY)
                    continue
                dev, status, reason = self._connect(host, version)
                if dev is None:
                    failures += 1
                    self.last_error = reason
                    if self.available or failures == 1:
                        self._call(self._async_set_state, None, False)
                    if reason == "auth" and failures >= FAILURES_BEFORE_CLOUD_REFRESH:
                        self._call(self._on_need_refresh, self.id)
                    self._sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)
                    continue
                failures = 0
                delay = RECONNECT_MIN_DELAY
                self.last_error = None
                own = message_dps(status) if not message_cid(status) else None
                self._call(self._async_set_state, own, True)
                try:
                    self._loop(dev)
                finally:
                    dev.close()
                    self._tt_children.clear()
                if not self._stop.is_set():
                    _LOGGER.debug("%s: connection lost", self.id)
                    self._call(self._async_set_state, None, False)
                    self._sleep(1)
            except Exception:  # noqa: BLE001 - never let the thread die
                _LOGGER.exception("%s: unexpected error", self.id)
                self._sleep(RECONNECT_MAX_DELAY)

    def _connect(
        self, host: str, version: str | None
    ) -> tuple[tinytuya.Device | None, Any, str]:
        versions = list(PROTOCOL_VERSIONS)
        if version in versions:
            versions.remove(version)
            versions.insert(0, version)
        elif version:
            versions.insert(0, version)
        gateway = self.is_gateway
        for ver in versions:
            if self._stop.is_set():
                break
            try:
                ver_f = float(ver)
            except ValueError:
                continue
            dev = tinytuya.Device(
                self.id,
                address=host,
                local_key=self._conf.get(DEV_LOCAL_KEY, ""),
                version=ver_f,
                persist=True,
                connection_timeout=5,
                connection_retry_limit=1,
                connection_retry_delay=1,
            )
            dev.set_socketRetryLimit(1)
            dev.set_sendRetryLimit(1)
            if gateway:
                self._create_children(dev)
            result = dev.status()
            err = _error(result)
            ok = isinstance(result, dict) and "dps" in result
            if gateway and not ok:
                # A hub may have no dps of its own: being connected with the
                # right key is enough.
                ok = err not in _BAD_KEY | _UNREACHABLE and dev.socket is not None
            if ok:
                _LOGGER.debug("%s: connected to %s (v%s)", self.id, host, ver)
                self.connected_host = host
                self.connected_version = ver
                if ver != self._conf.get(DEV_VERSION) or host != self._conf.get(
                    DEV_HOST
                ):
                    self._call(self._on_network_change, self.id, host, ver)
                return dev, result, ""
            dev.close()
            self._tt_children.clear()
            _LOGGER.debug("%s: v%s at %s failed: %s", self.id, ver, host, result)
            if err in _UNREACHABLE:
                return None, None, f"non raggiungibile ({host})"
        return None, None, "auth"

    def _create_children(self, dev: tinytuya.Device) -> None:
        """Register sub-devices with a known node id on the tinytuya gateway."""
        for child in list(self.children.values()):
            cid = child.node_id
            if cid and child.id not in self._tt_children:
                self._tt_children[child.id] = tinytuya.Device(
                    child.id,
                    cid=cid,
                    parent=dev,
                    local_key=self._conf.get(DEV_LOCAL_KEY, ""),
                    persist=True,
                )

    def _query_children(self, dev: tinytuya.Device) -> None:
        if not self.children:
            return
        dev.subdev_query(nowait=True)
        for tt_child in list(self._tt_children.values()):
            tt_child.status(nowait=True)

    def _handle_online(self, dev: tinytuya.Device, online: list[str], offline: list[str]) -> None:
        known = {c.node_id: c for c in self.children.values() if c.node_id}
        unknown = [cid for cid in online + offline if cid not in known]
        self.unknown_cids = unknown
        # A single sub-device without node id and a single unknown node:
        # they must be the same device.
        missing = [c for c in self.children.values() if not c.node_id]
        if len(missing) == 1 and len(unknown) == 1:
            child, cid = missing[0], unknown[0]
            _LOGGER.info("%s: node id of %s detected as %s", self.id, child.name, cid)
            child.detected_node_id = cid
            if self._on_node_id is not None:
                self._call(self._on_node_id, child.id, cid)
            known[cid] = child
            self._create_children(dev)
            self._tt_children[child.id].status(nowait=True)
        for child in missing:
            if not child.node_id:
                child.last_error = "node id sconosciuto"
        for cid, child in known.items():
            if cid in offline:
                child.last_error = "offline (segnalato dal gateway)"
                self._call(self._async_child_state, child.id, None, False)
            elif cid in online:
                child.last_error = None
                self._call(self._async_child_state, child.id, None, True)

    def _handle_message(self, dev: tinytuya.Device, msg: Any) -> None:
        if (online := message_online(msg)) is not None:
            self._handle_online(dev, *online)
            return
        dps = message_dps(msg)
        cid = message_cid(msg)
        if cid is None:
            if dps is not None:
                self._call(self._async_set_state, dps, True)
            return
        for child in self.children.values():
            if child.node_id == cid:
                child.last_error = None
                self._call(self._async_child_state, child.id, dps, True)
                return
        _LOGGER.debug("%s: message for unknown node %s: %s", self.id, cid, msg)

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(1024):
                pass
        except (BlockingIOError, OSError):
            pass

    def _send_commands(self) -> bool:
        """Send queued commands. Returns False if the connection broke."""
        batches: dict[str | None, dict[str, Any]] = {}
        while True:
            try:
                target, dps = self._commands.get_nowait()
            except queue.Empty:
                break
            batches.setdefault(target, {}).update(dps)
        for target, dps in batches.items():
            if target is None:
                result = self._dev.set_multiple_values(dps, nowait=True)
            elif (tt_child := self._tt_children.get(target)) is not None:
                result = tt_child.set_multiple_values(dps, nowait=True)
            else:
                _LOGGER.warning("%s: cannot reach sub-device %s", self.id, target)
                continue
            if _error(result):
                return False
        return True

    def _loop(self, dev: tinytuya.Device) -> None:
        self._dev = dev
        now = time.monotonic()
        last_rx = last_hb = last_poll = now
        refresh_ids = [
            int(dp.id)
            for dp in self.spec.dps.values()
            if dp.type == "Integer" and not dp.writable and dp.id.isdigit()
        ]
        self._query_children(dev)
        while not self._stop.is_set():
            sock = dev.socket
            if sock is None:
                return
            try:
                readable, _, _ = select.select([sock, self._wake_r], [], [], 1.0)
            except (OSError, ValueError):
                return
            if self._wake_r in readable:
                self._drain_wake()
                self._wake.clear()
            if self._stop.is_set():
                return

            if not self._send_commands():
                return

            if sock in readable:
                msg = dev.receive()
                if dev.raw_recv:
                    last_rx = time.monotonic()
                err = _error(msg)
                if err:
                    _LOGGER.debug("%s: receive error %s", self.id, msg)
                    if err in _UNREACHABLE:
                        return
                elif msg is not None:
                    last_rx = time.monotonic()
                    self._handle_message(dev, msg)

            now = time.monotonic()
            if now - last_hb >= HEARTBEAT_INTERVAL:
                last_hb = now
                if _error(dev.heartbeat(nowait=True)):
                    return
            if now - last_poll >= POLL_INTERVAL:
                last_poll = now
                if refresh_ids and float(self.connected_version or 0) >= 3.3:
                    dev.updatedps(refresh_ids, nowait=True)
                if _error(dev.status(nowait=True)):
                    return
                self._query_children(dev)
            if now - last_rx > CONNECTION_DEAD_AFTER:
                _LOGGER.debug("%s: no data for %ss", self.id, CONNECTION_DEAD_AFTER)
                return
