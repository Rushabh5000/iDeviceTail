"""Zero-config discovery over mDNS/Bonjour.

We browse two service types:

* ``_apple-mobdev2._tcp``  — advertised by an iPhone/iPad that has "Wi-Fi sync"
  (a.k.a. "Connect via network" in Xcode) enabled *and* a valid pairing with
  some host. This is the hint that Engine A can reach the device wirelessly.
  It carries an IP address but not a friendly name/UDID, so the device
  registry later correlates it with ``pymobiledevice3 usbmux list``.

* ``_idevtail._tcp``      — presence beacon from the bundled iOS agent app.
  TXT keys: ``id``, ``name``, ``model``, ``os``, ``v`` (protocol version).
  Authoritative agent data still comes from the actual TCP ``hello`` frame;
  this just makes the device visible before it connects.

The desktop also *publishes* ``_idevtail-host._tcp`` so agents can find it
without any manual IP entry (see :func:`publish_host`).
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Awaitable, Callable, Optional

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .models import Device

APPLE_MOBDEV = "_apple-mobdev2._tcp.local."
AGENT_BEACON = "_idevtail._tcp.local."
HOST_SERVICE = "_idevtail-host._tcp.local."

DeviceCb = Callable[[Device], None]
LostCb = Callable[[str], None]


class Discovery:
    def __init__(
        self,
        *,
        on_device: DeviceCb,
        on_lost: LostCb,
        want_apple: bool = True,
        want_agent: bool = True,
        stale_after: float = 90.0,
    ) -> None:
        self._on_device = on_device
        self._on_lost = on_lost
        self._types = []
        if want_apple:
            self._types.append(APPLE_MOBDEV)
        if want_agent:
            self._types.append(AGENT_BEACON)
        self._stale_after = stale_after
        self._aiozc: Optional[AsyncZeroconf] = None
        self._browser: Optional[AsyncServiceBrowser] = None
        self._seen: dict[str, float] = {}
        self._reaper: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        if not self._types:
            return
        self._loop = asyncio.get_running_loop()
        self._aiozc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(
            self._aiozc.zeroconf, self._types, handlers=[self._state_change]
        )
        self._reaper = asyncio.create_task(self._reap_loop(), name="discovery-reaper")

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        if self._browser:
            await self._browser.async_cancel()
        if self._aiozc:
            await self._aiozc.async_close()

    # -- zeroconf callback (may run off the event-loop thread) -----------
    def _state_change(
        self, zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        loop = self._loop
        if loop is None:
            return
        if state_change is ServiceStateChange.Removed:
            key = _key(service_type, name)
            self._seen.pop(key, None)
            loop.call_soon_threadsafe(self._on_lost, key)
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._resolve(service_type, name))
        )

    async def _resolve(self, service_type: str, name: str) -> None:
        assert self._aiozc is not None
        info = AsyncServiceInfo(service_type, name)
        try:
            ok = await info.async_request(self._aiozc.zeroconf, timeout=3000)
        except Exception:
            ok = False
        if not ok:
            return
        addrs = _addresses(info)
        key = _key(service_type, name)
        self._seen[key] = time.time()

        if service_type == AGENT_BEACON:
            txt = _txt(info)
            dev = Device(
                id=txt.get("id") or key,
                name=txt.get("name") or _instance(name),
                model=txt.get("model", ""),
                os_version=txt.get("os", ""),
                address=addrs[0] if addrs else "",
                udid="",
            )
            dev.transports.add("agent-beacon")
            dev.detail = "iOS agent seen on network (not yet connected)"
        else:  # APPLE_MOBDEV
            dev = Device(
                id=key,
                name=f"Apple device @ {addrs[0]}" if addrs else _instance(name),
                address=addrs[0] if addrs else "",
            )
            dev.transports.add("bonjour-apple")
            dev.paired = True  # it only advertises this if it trusts some host
            dev.detail = "Reachable for Engine A over Wi-Fi (needs this host to be paired)"
        dev.touch()
        self._on_device(dev)

    async def _reap_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._stale_after / 3)
                cutoff = time.time() - self._stale_after
                for key, seen in list(self._seen.items()):
                    if seen < cutoff:
                        self._seen.pop(key, None)
                        self._on_lost(key)
        except asyncio.CancelledError:
            pass


async def publish_host(port: int, *, instance: str | None = None) -> AsyncZeroconf:
    """Advertise ``_idevtail-host._tcp`` so agents can auto-find this computer.

    Returns the :class:`AsyncZeroconf` handle; caller must ``async_close`` it.
    """
    from zeroconf import ServiceInfo

    hostname = socket.gethostname().split(".")[0]
    inst = instance or f"iDeviceTail on {hostname}"
    addr = _primary_ipv4()
    info = ServiceInfo(
        HOST_SERVICE,
        f"{inst}.{HOST_SERVICE}",
        addresses=[socket.inet_aton(addr)] if addr else [],
        port=port,
        properties={b"v": b"1", b"host": hostname.encode()},
        server=f"{hostname}.local.",
    )
    aiozc = AsyncZeroconf()
    await aiozc.async_register_service(info)
    return aiozc


# -- helpers ----------------------------------------------------------------
def _key(service_type: str, name: str) -> str:
    return f"{service_type}|{name}"


def _instance(name: str) -> str:
    return name.split(".")[0]


def _txt(info: AsyncServiceInfo) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (info.properties or {}).items():
        try:
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))
            out[key] = val
        except Exception:
            continue
    return out


def _addresses(info: AsyncServiceInfo) -> list[str]:
    try:
        return [a for a in info.parsed_addresses() if not a.startswith("fe80")] or list(
            info.parsed_addresses()
        )
    except Exception:
        return []


def _primary_ipv4() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet actually sent
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
