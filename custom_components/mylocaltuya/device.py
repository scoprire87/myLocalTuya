"""Persistent local connection to a single Tuya device.

Each device gets a small worker thread that keeps a TCP connection open
(tinytuya, persistent socket), receives pushed status updates, sends
heartbeats and executes commands queued by Home Assistant.
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


def _error(result: Any) -> str | None:
    if isinstance(result, dict) and "Err" in result:
        return str(result["Err"])
    return None


class TuyaLocalDevice:
    """A Tuya device controlled over the LAN."""

    def __init__(
        self,
        hass: HomeAssistant,
        conf: dict[str, Any],
        discovery: TuyaDiscovery,
        on_network_change: Callable[[str, str, str], None],
        on_need_refresh: Callable[[str], None],
    ) -> None:
        """Initialize."""
        self.hass = hass
        self._conf = conf
        self.spec = DeviceSpec(conf.get("dps") or {})
        self._discovery = discovery
        self._on_network_change = on_network_change
        self._on_need_refresh = on_need_refresh

        self.dps: dict[str, Any] = {}
        self.available = False
        self.connected_host: str | None = None
        self.connected_version: str | None = None
        self.last_error: str | None = None

        self._listeners: list[Callable[[], None]] = []
        self._commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._thread: threading.Thread | None = None
        self._remove_discovery: Callable[[], None] | None = None

    # ---- properties --------------------------------------------------------
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

    # ---- listeners -----------------------------------------------------------
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a state listener."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _async_notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def _async_set_state(self, dps: dict[str, Any] | None, available: bool) -> None:
        if dps:
            self.dps.update(dps)
        self.available = available
        self._async_notify()

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
    async def async_set_dps(self, dps: dict[str, Any]) -> None:
        """Send new values (dp id -> raw value) to the device."""
        if not self.available:
            raise HomeAssistantError(f"{self.name} non è raggiungibile")
        _LOGGER.debug("%s: set %s", self.id, dps)
        self._commands.put(dict(dps))
        self._poke()

    # ---- worker thread -------------------------------------------------------
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
                        self.hass.loop.call_soon_threadsafe(
                            self._async_set_state, None, False
                        )
                    if reason == "auth" and failures >= FAILURES_BEFORE_CLOUD_REFRESH:
                        self.hass.loop.call_soon_threadsafe(
                            self._on_need_refresh, self.id
                        )
                    self._sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)
                    continue
                failures = 0
                delay = RECONNECT_MIN_DELAY
                self.last_error = None
                self.hass.loop.call_soon_threadsafe(
                    self._async_set_state, self._dps_of(status), True
                )
                try:
                    self._loop(dev)
                finally:
                    dev.close()
                if not self._stop.is_set():
                    _LOGGER.debug("%s: connection lost", self.id)
                    self.hass.loop.call_soon_threadsafe(
                        self._async_set_state, None, False
                    )
                    self._sleep(1)
            except Exception:  # noqa: BLE001 - never let the thread die
                _LOGGER.exception("%s: unexpected error", self.id)
                self._sleep(RECONNECT_MAX_DELAY)

    def _connect(
        self, host: str, version: str | None
    ) -> tuple[tinytuya.Device | None, dict | None, str]:
        versions = list(PROTOCOL_VERSIONS)
        if version in versions:
            versions.remove(version)
            versions.insert(0, version)
        elif version:
            versions.insert(0, version)
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
            result = dev.status()
            if isinstance(result, dict) and "dps" in result:
                _LOGGER.debug("%s: connected to %s (v%s)", self.id, host, ver)
                self.connected_host = host
                self.connected_version = ver
                if ver != self._conf.get(DEV_VERSION) or host != self._conf.get(
                    DEV_HOST
                ):
                    self.hass.loop.call_soon_threadsafe(
                        self._on_network_change, self.id, host, ver
                    )
                return dev, result, ""
            dev.close()
            err = _error(result)
            _LOGGER.debug("%s: v%s at %s failed: %s", self.id, ver, host, result)
            if err in _UNREACHABLE:
                return None, None, f"non raggiungibile ({host})"
        return None, None, "auth"

    @staticmethod
    def _dps_of(msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or "cid" in msg:
            return None
        dps = msg.get("dps")
        if not isinstance(dps, dict):
            return None
        return {str(k): v for k, v in dps.items()}

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(1024):
                pass
        except (BlockingIOError, OSError):
            pass

    def _loop(self, dev: tinytuya.Device) -> None:
        now = time.monotonic()
        last_rx = last_hb = last_poll = now
        refresh_ids = [
            int(dp.id)
            for dp in self.spec.dps.values()
            if dp.type == "Integer" and not dp.writable and dp.id.isdigit()
        ]
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

            commands: dict[str, Any] = {}
            while True:
                try:
                    commands.update(self._commands.get_nowait())
                except queue.Empty:
                    break
            if commands:
                if _error(dev.set_multiple_values(commands, nowait=True)):
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
                elif (dps := self._dps_of(msg)) is not None:
                    last_rx = time.monotonic()
                    self.hass.loop.call_soon_threadsafe(
                        self._async_set_state, dps, True
                    )

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
            if now - last_rx > CONNECTION_DEAD_AFTER:
                _LOGGER.debug("%s: no data for %ss", self.id, CONNECTION_DEAD_AFTER)
                return
