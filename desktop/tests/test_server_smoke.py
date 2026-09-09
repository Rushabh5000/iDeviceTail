"""End-to-end-ish: build the aiohttp app, publish a record, see it on the WS."""

import asyncio
import json
import os
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from idevicetail.config import Config
from idevicetail.models import Level, LogRecord
from idevicetail.server import create_app

os.environ["IDEVICETAIL_NO_OS_OPEN"] = "1"   # don't spawn a file manager during tests


@pytest.fixture
async def client(tmp_path):
    cfg = Config()
    cfg.discover_apple = False
    cfg.discover_agent = False
    cfg.agent_port = 0
    cfg.data_dir = tmp_path
    cfg.store_enabled = True
    app = await create_app(cfg)
    async with TestClient(TestServer(app)) as c:
        yield c
    await app["shutdown"]()


@pytest.mark.asyncio
async def test_state_endpoint(client):
    r = await client.get("/api/state")
    assert r.status == 200
    j = await r.json()
    assert "devices" in j and "engines" in j and j["stats"]["store_enabled"] is True


@pytest.mark.asyncio
async def test_ws_snapshot_then_live(client):
    ws = await client.ws_connect("/ws?snapshot=10")
    first = json.loads(await ws.receive_str())
    assert first["type"] == "snapshot"

    client.app["bus"].publish(
        LogRecord(ts=time.time(), device_id="d1", device_name="iPhone",
                  source="agent", message="hello-ws", level=Level.error)
    )
    got = None
    for _ in range(10):
        msg = json.loads(await ws.receive_str())
        if msg["type"] == "logs":
            got = msg["logs"][-1]
            break
    assert got and got["message"] == "hello-ws" and got["level"] == "error"
    await ws.close()


@pytest.mark.asyncio
async def test_start_capture_unknown_device_400(client):
    r = await client.post("/api/devices/nope/start", json={"mode": "syslog"})
    assert r.status == 400


@pytest.mark.asyncio
async def test_export_ndjson(client):
    client.app["bus"].publish(
        LogRecord(ts=time.time(), device_id="d1", device_name="iPhone",
                  source="agent", message="exp", level=Level.info)
    )
    r = await client.get("/api/export?format=ndjson&source=ring")
    assert r.status == 200
    body = await r.text()
    assert "exp" in body


@pytest.mark.asyncio
async def test_session_file_written_and_downloadable(client):
    for i in range(5):
        client.app["bus"].publish(
            LogRecord(ts=time.time(), device_id="d1", device_name="iPhone XR",
                      source="device-syslog", process="locationd", message=f"loc {i}",
                      level=Level.notice)
        )
    # give the filesink executor a beat to flush
    for _ in range(20):
        info = await (await client.get("/api/session-file/info")).json()
        if info["lines_written"] >= 5:
            break
        await asyncio.sleep(0.1)
    assert info["lines_written"] >= 5
    assert info["log_path"].endswith(".log")

    r = await client.get("/api/session-file?format=log")
    assert r.status == 200
    text = await r.text()
    assert "locationd" in text and "loc 3" in text

    r = await client.get("/api/session-file?format=ndjson")
    assert r.status == 200
    assert '"process":"locationd"' in await r.text()


@pytest.mark.asyncio
async def test_session_file_open_guarded_in_tests(client):
    r = await client.post("/api/session-file/open", json={"which": "log"})
    j = await r.json()
    # loopback from TestClient, but OS-open disabled via env -> ok False, detail 'disabled'
    assert j["ok"] is False and j["detail"] == "disabled"


@pytest.mark.asyncio
async def test_device_info_agent_device(client):
    client.app["bus"].publish(
        LogRecord(ts=time.time(), device_id="agent-x", device_name="Demo",
                  source="agent", message="hi", level=Level.info)
    )
    # register the device via the manager the way the agent server would
    from idevicetail.models import Device
    d = Device(id="agent-x", name="Demo iPhone", model="iPhone11,8", os_version="18.5")
    d.transports.add("agent")
    client.app["manager"].merge_device(d)

    j = await (await client.get("/api/devices/agent-x/info")).json()
    assert j["model"] == "iPhone XR" and j["kind"] == "iphone" and j["source"] == "agent"
