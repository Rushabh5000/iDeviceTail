"""Engine B — ingest listener for the bundled iOS agent app.

Accepts TCP connections from ``IDeviceTailKit`` and turns their frames into
:class:`LogRecord`\\ s on the bus. This is the **cable-free, pairing-free,
Developer-Mode-free** path. Its hard limit (iOS App Sandbox): the agent can
only see *its own* process's ``os_log`` output plus anything routed through
``IDeviceTailKit`` in apps you build. It cannot read other apps or the system.

Wire format (see docs/PROTOCOL.md): either
  * length-prefixed  — ``uint32 BE length`` + UTF-8 JSON, or
  * NDJSON           — one JSON object per ``\\n``
auto-detected from the first byte (``{`` => NDJSON).

Message envelopes: ``hello``, ``log``, ``batch``, ``ping`` (-> ``pong``), ``bye``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import time
import uuid
from typing import Awaitable, Callable, Optional

from .bus import LogBus
from .models import SRC_AGENT, Device, LogRecord
from .normalize import agent_payload_to_record

DeviceUpdateCb = Callable[[Device], None]
SessionCb = Callable[[str, str, str], Awaitable[None]]  # (session_id, device_id, source)

MAX_FRAME = 8 * 1024 * 1024  # 8 MiB hard cap per frame


class AgentServer:
    def __init__(
        self,
        bus: LogBus,
        *,
        host: str = "0.0.0.0",
        port: int = 45455,
        bind_policy: str = "lan",
        on_device: Optional[DeviceUpdateCb] = None,
        on_session_start: Optional[SessionCb] = None,
        on_session_end: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.bus = bus
        self.host = host
        self.port = port
        self.bind_policy = bind_policy
        self._on_device = on_device or (lambda d: None)
        self._on_session_start = on_session_start
        self._on_session_end = on_session_end
        self._server: Optional[asyncio.AbstractServer] = None
        self._conns: set[asyncio.Task] = set()
        self.clients_total = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        socknames = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        print(f"[agent] listening on {socknames}  (policy={self.bind_policy})", flush=True)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for t in list(self._conns):
            t.cancel()
        for t in list(self._conns):
            with contextlib.suppress(asyncio.CancelledError):
                await t

    # -- per-connection --------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "?"
        if not _peer_allowed(peer_ip, self.bind_policy):
            print(f"[agent] rejected {peer_ip} (bind_policy={self.bind_policy})")
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return

        self.clients_total += 1
        task = asyncio.current_task()
        if task:
            self._conns.add(task)
        device: Optional[Device] = None
        session_id = ""
        n = 0
        try:
            first = await reader.readexactly(1)
            ndjson = first == b"{"
            async for obj in _frames(reader, ndjson=ndjson, prefix=first):
                mtype = obj.get("type", "log")
                if mtype == "ping":
                    await _send(writer, {"type": "pong", "t": obj.get("t", time.time())}, ndjson)
                    continue
                if mtype == "bye":
                    break
                if mtype == "hello":
                    device, session_id = await self._on_hello(obj, peer_ip)
                    await _send(
                        writer,
                        {"type": "welcome", "session": session_id, "server_time": time.time()},
                        ndjson,
                    )
                    continue
                if device is None:
                    # Tolerate a client that streams before hello.
                    device, session_id = await self._on_hello({"device": {}}, peer_ip)

                items = obj.get("logs", [obj]) if mtype == "batch" else [obj]
                for item in items:
                    rec = agent_payload_to_record(
                        item,
                        device_id=device.id,
                        device_name=device.name,
                        session_id=session_id,
                    )
                    self.bus.publish(rec)
                    n += 1
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:  # keep the listener alive no matter what one client does
            print(f"[agent] {peer_ip} error: {e!r}")
        finally:
            if task:
                self._conns.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if device is not None:
                # keep the "agent" transport (it marks the device *kind*); just
                # flag it offline so the UI shows a grey dot, not a fake "needs
                # USB provisioning" card.
                device.connection_state = "offline"
                device.detail = "agent disconnected — will reappear on reconnect"
                device.touch()
                self._on_device(device)
                if self._on_session_end and session_id:
                    with contextlib.suppress(Exception):
                        await self._on_session_end(session_id)
            print(f"[agent] {peer_ip} disconnected after {n} records")

    async def _on_hello(self, obj: dict, peer_ip: str) -> tuple[Device, str]:
        d = obj.get("device") or {}
        dev_id = str(d.get("id") or f"agent-{peer_ip}")
        device = Device(
            id=dev_id,
            name=str(d.get("name") or f"iOS agent @ {peer_ip}"),
            model=str(d.get("model") or ""),
            os_version=str(d.get("os") or ""),
            address=peer_ip,
        )
        device.transports.add("agent")
        device.connection_state = "streaming"
        device.developer_mode = None
        device.paired = None
        device.detail = f"app={d.get('app', '?')} protocol={obj.get('protocol', '?')}"
        device.touch()
        self._on_device(device)

        session_id = str(obj.get("session") or uuid.uuid4().hex)
        if self._on_session_start:
            with contextlib.suppress(Exception):
                await self._on_session_start(session_id, dev_id, SRC_AGENT)
        return device, session_id


# -- framing --------------------------------------------------------------
async def _frames(reader: asyncio.StreamReader, *, ndjson: bool, prefix: bytes = b""):
    """Yield decoded JSON objects. ``prefix`` is bytes already consumed from the
    stream (the 1-byte format probe) that must be treated as the start of the
    first frame."""
    if ndjson:
        buf = bytearray(prefix)
        while True:
            nl = buf.find(b"\n")
            while nl == -1:
                chunk = await reader.read(65536)
                if not chunk:
                    if buf.strip():
                        with contextlib.suppress(json.JSONDecodeError):
                            yield json.loads(buf)
                    return
                buf.extend(chunk)
                nl = buf.find(b"\n")
            line, del_ = bytes(buf[:nl]).strip(), nl + 1
            del buf[:del_]
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    else:
        buf = bytearray(prefix)
        while True:
            while len(buf) < 4:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                buf.extend(chunk)
            length = int.from_bytes(buf[:4], "big")
            if length > MAX_FRAME:
                raise ValueError(f"frame too large: {length}")
            while len(buf) < 4 + length:
                chunk = await reader.read(65536)
                if not chunk:
                    raise asyncio.IncompleteReadError(bytes(buf), 4 + length)
                buf.extend(chunk)
            payload = bytes(buf[4:4 + length])
            del buf[:4 + length]
            if length == 0:
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


async def _send(writer: asyncio.StreamWriter, obj: dict, ndjson: bool) -> None:
    data = json.dumps(obj, separators=(",", ":")).encode()
    if ndjson:
        writer.write(data + b"\n")
    else:
        writer.write(len(data).to_bytes(4, "big") + data)
    with contextlib.suppress(Exception):
        await writer.drain()


def _peer_allowed(ip: str, policy: str) -> bool:
    if policy == "any":
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if policy == "local":
        return False
    return addr.is_private or addr.is_link_local
