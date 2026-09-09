"""Exercise the agent listener's framing against length-prefixed and NDJSON,
including split packets, oversized frames, batches and pings."""

import asyncio
import json
import struct

import pytest

from idevicetail.bus import LogBus
from idevicetail.engine_agent import AgentServer


async def _serve():
    bus = LogBus()
    seen: list = []
    orig = bus.publish
    bus.publish = lambda rec: (seen.append(rec), orig(rec))[1]  # type: ignore
    srv = AgentServer(bus, host="127.0.0.1", port=0)
    srv._server = await asyncio.start_server(srv._handle, "127.0.0.1", 0)
    port = srv._server.sockets[0].getsockname()[1]
    return srv, bus, seen, port


def _lp(obj) -> bytes:
    b = json.dumps(obj).encode()
    return struct.pack(">I", len(b)) + b


@pytest.mark.asyncio
async def test_length_prefixed_hello_and_logs():
    srv, bus, seen, port = await _serve()
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(_lp({"type": "hello", "protocol": 1, "device": {"id": "dev1", "name": "iPhone X"}}))
    w.write(_lp({"type": "log", "ts": 1_700_000_000, "level": "error", "message": "boom"}))
    w.write(_lp({"type": "batch", "logs": [
        {"ts": 1_700_000_001, "message": "a"},
        {"ts": 1_700_000_002, "message": "b"},
    ]}))
    await w.drain()
    await asyncio.sleep(0.2)
    assert len(seen) == 3
    assert seen[0].message == "boom" and seen[0].device_id == "dev1"
    w.close()
    await srv.stop()


@pytest.mark.asyncio
async def test_split_across_packets():
    srv, bus, seen, port = await _serve()
    r, w = await asyncio.open_connection("127.0.0.1", port)
    payload = _lp({"type": "hello", "device": {"id": "d"}}) + _lp({"message": "half"})
    w.write(payload[:5]); await w.drain(); await asyncio.sleep(0.05)
    w.write(payload[5:9]); await w.drain(); await asyncio.sleep(0.05)
    w.write(payload[9:]); await w.drain(); await asyncio.sleep(0.2)
    assert [s.message for s in seen] == ["half"]
    w.close()
    await srv.stop()


@pytest.mark.asyncio
async def test_ndjson_mode_and_ping():
    srv, bus, seen, port = await _serve()
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write((json.dumps({"type": "hello", "device": {"id": "n1"}}) + "\n").encode())
    w.write((json.dumps({"type": "ping", "t": 7}) + "\n").encode())
    w.write((json.dumps({"message": "ndjson-line"}) + "\n").encode())
    await w.drain()
    # server replies with a 'welcome' frame to hello, then 'pong' to ping
    types = []
    for _ in range(3):
        line = await asyncio.wait_for(r.readline(), timeout=1)
        if not line:
            break
        types.append(json.loads(line)["type"])
        if "pong" in types:
            break
    assert "welcome" in types and "pong" in types
    await asyncio.sleep(0.2)
    assert seen[-1].message == "ndjson-line"
    w.close()
    await srv.stop()


@pytest.mark.asyncio
async def test_oversized_frame_rejected_without_killing_server():
    srv, bus, seen, port = await _serve()
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(struct.pack(">I", 99_000_000))  # > MAX_FRAME
    await w.drain()
    await asyncio.sleep(0.2)
    w.close()
    # server still accepts a fresh connection
    r2, w2 = await asyncio.open_connection("127.0.0.1", port)
    w2.write(_lp({"type": "hello", "device": {"id": "again"}}))
    w2.write(_lp({"message": "still alive"}))
    await w2.drain()
    await asyncio.sleep(0.2)
    assert any(s.message == "still alive" for s in seen)
    w2.close()
    await srv.stop()
