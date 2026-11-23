"""Find Tuya devices on the LAN by listening to their UDP broadcasts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import socket
from typing import Any

from tinytuya.core.udp_helper import decrypt_udp

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# 6666: protocol 3.1 (clear), 6667: 3.3+ (encrypted), 7000: 3.5 / app
UDP_PORTS = (6666, 6667, 7000)

DiscoveryCallback = Callable[[str, dict[str, Any]], None]


def parse_broadcast(data: bytes) -> dict[str, Any] | None:
    """Decode a Tuya UDP broadcast into a dict, or None."""
    try:
        payload = decrypt_udp(data)
        if isinstance(payload, bytes):
            payload = payload.decode(errors="ignore")
        info = json.loads(payload)
    except Exception:  # noqa: BLE001 - garbage on the port is expected
        return None
    if not isinstance(info, dict) or "gwId" not in info:
        return None
    return info


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: TuyaDiscovery) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        info = parse_broadcast(data)
        if info is not None:
            info.setdefault("ip", addr[0])
            self._owner.handle(info)


class TuyaDiscovery:
    """Shared UDP listener, one per Home Assistant instance."""

    def __init__(self) -> None:
        """Initialize."""
        self.devices: dict[str, dict[str, Any]] = {}
        self._transports: list[asyncio.DatagramTransport] = []
        self._callbacks: list[DiscoveryCallback] = []
        self.users = 0

    async def start(self) -> None:
        """Bind the UDP ports (those already taken are skipped)."""
        loop = asyncio.get_running_loop()
        for port in UDP_PORTS:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind(("", port))
                sock.setblocking(False)
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _Protocol(self), sock=sock
                )
            except OSError as err:
                _LOGGER.warning("Cannot listen on UDP port %s: %s", port, err)
                continue
            self._transports.append(transport)

    def stop(self) -> None:
        """Close all sockets."""
        for transport in self._transports:
            transport.close()
        self._transports.clear()

    @callback
    def handle(self, info: dict[str, Any]) -> None:
        """Record a broadcast and notify listeners."""
        dev_id = info["gwId"]
        new = {"ip": info.get("ip"), "version": str(info.get("version", ""))}
        if self.devices.get(dev_id) != new:
            _LOGGER.debug("Discovered %s at %s (v%s)", dev_id, new["ip"], new["version"])
        self.devices[dev_id] = new
        for cb in list(self._callbacks):
            cb(dev_id, new)

    @callback
    def async_add_listener(self, cb: DiscoveryCallback) -> Callable[[], None]:
        """Register a callback for every broadcast."""
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb)

    async def async_wait_for(self, dev_ids: set[str], timeout: float) -> None:
        """Wait until all `dev_ids` were seen, or timeout."""
        if dev_ids <= self.devices.keys():
            return
        done = asyncio.Event()

        def _check(_dev_id: str, _info: dict[str, Any]) -> None:
            if dev_ids <= self.devices.keys():
                done.set()

        remove = self.async_add_listener(_check)
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except TimeoutError:
            pass
        finally:
            remove()


async def async_get_discovery(hass: HomeAssistant) -> TuyaDiscovery:
    """Return the shared discovery listener, starting it if needed."""
    data = hass.data.setdefault(DOMAIN, {})
    discovery: TuyaDiscovery | None = data.get("discovery")
    if discovery is None:
        discovery = TuyaDiscovery()
        await discovery.start()
        data["discovery"] = discovery
    discovery.users += 1
    return discovery


@callback
def async_release_discovery(hass: HomeAssistant) -> None:
    """Drop a reference to the shared listener, stopping it when unused."""
    data = hass.data.get(DOMAIN, {})
    discovery: TuyaDiscovery | None = data.get("discovery")
    if discovery is None:
        return
    discovery.users -= 1
    if discovery.users <= 0:
        discovery.stop()
        data.pop("discovery", None)
