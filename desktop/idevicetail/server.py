"""aiohttp app: REST + browser WebSocket + static log-viewer UI.

One process runs everything:

    discovery (mDNS)  ─┐
    agent listener  ───┼─►  LogBus  ──►  WebSocket clients (browser UI)
    Engine-A capture ─┘        └────────►  SQLite writer (optional)

Bind policy is enforced on the agent listener (see engine_agent) and, for the
web port, by choosing the interface. By default we bind 0.0.0.0 so the UI is
reachable from your LAN; pass ``--host 127.0.0.1`` to keep it on this machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import WSMsgType, web

from . import __version__
from .bus import LogBus
from .config import Config
from .device_names import device_kind, human_capacity, marketing_name
from .discovery import Discovery, publish_host
from .engine_agent import AgentServer
from .exporter import content_type, to_bytes
from .filesink import FileSink
from .manager import DeviceManager
from .models import SRC_SYSDIAGNOSE, Level, LogRecord
from .normalize import crash_to_record
from .store import Store

WEB_DIR = Path(__file__).parent / "web"
LEVEL_RANK = {lv.value: lv.rank for lv in Level}


class WSHub:
    """Fan-out of control-plane events (device/engine/stats) to browser sockets.

    Each browser socket registers a small bounded queue; broadcasts are
    ``put_nowait`` (dropped if a socket is wedged). The socket's own writer
    coroutine drains it, so frames never interleave on one connection.
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()

    def register(self, maxsize: int = 2000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def broadcast(self, obj: dict[str, Any]) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(obj)
            except asyncio.QueueFull:
                pass


async def run(cfg: Config) -> None:
    app = await create_app(cfg)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    print(f"[web] http://{_display_host(cfg.host)}:{cfg.port}   (UI + REST + /ws)", flush=True)
    print(f"[web] version {__version__}  data_dir={cfg.data_dir}", flush=True)
    stop = asyncio.Event()
    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
        pass
    finally:
        await app["shutdown"]()
        await runner.cleanup()


async def create_app(cfg: Config) -> web.Application:
    cfg.ensure_dirs()
    bus = LogBus(ring_size=cfg.ring_size)
    hub = WSHub()

    store: Optional[Store] = None
    if cfg.store_enabled:
        store = Store(cfg.db_path, flush_rows=cfg.db_flush_rows, flush_seconds=cfg.db_flush_seconds)
        await store.open()
        store.run(bus)

    manager = DeviceManager(cfg, bus, store=store)
    manager.set_notifier(hub.broadcast)
    await manager.start()

    agent = AgentServer(
        bus,
        host=cfg.agent_host,
        port=cfg.agent_port,
        bind_policy=cfg.bind_policy,
        on_device=lambda d: (manager.merge_device(d), None)[1],
        on_session_start=(store.start_session if store else None),
        on_session_end=(store.end_session if store else None),
    )
    await agent.start()

    discovery: Optional[Discovery] = None
    if cfg.discover_apple or cfg.discover_agent:
        discovery = Discovery(
            on_device=manager.merge_device,
            on_lost=manager.lose_bonjour,
            want_apple=cfg.discover_apple,
            want_agent=cfg.discover_agent,
        )
        await discovery.start()

    host_zc = None
    with contextlib.suppress(Exception):
        host_zc = await publish_host(cfg.agent_port)

    filesink = FileSink(cfg.data_dir)
    filesink.run(bus)

    app = web.Application(client_max_size=32 * 1024**2)
    app["cfg"] = cfg
    app["bus"] = bus
    app["hub"] = hub
    app["store"] = store
    app["manager"] = manager
    app["agent"] = agent
    app["filesink"] = filesink
    app["devinfo_cache"] = {}
    app["started"] = time.time()

    async def _shutdown() -> None:
        with contextlib.suppress(Exception):
            if discovery:
                await discovery.stop()
        with contextlib.suppress(Exception):
            if host_zc:
                await host_zc.async_close()
        with contextlib.suppress(Exception):
            await agent.stop()
        with contextlib.suppress(Exception):
            await manager.stop()
        with contextlib.suppress(Exception):
            await filesink.close()
        with contextlib.suppress(Exception):
            if store:
                await store.close()

    app["shutdown"] = _shutdown

    app.add_routes(
        [
            web.get("/", _index),
            web.get("/api/state", _state),
            web.get("/api/devices", _devices),
            web.get("/api/devices/{id}/info", _device_info),
            web.post("/api/devices/{id}/start", _start_capture),
            web.post("/api/devices/{id}/stop", _stop_capture),
            web.post("/api/devices/{id}/crashes", _pull_crashes),
            web.post("/api/devices/{id}/sysdiagnose", _sysdiagnose),
            web.post("/api/devices/{id}/provision", _provision),
            web.get("/api/logs", _logs),
            web.get("/api/export", _export),
            web.get("/api/session-file", _session_file),
            web.get("/api/session-file/info", _session_file_info),
            web.post("/api/session-file/open", _session_file_open),
            web.get("/ws", _ws),
            web.get("/healthz", _healthz),
        ]
    )
    if WEB_DIR.is_dir():
        app.router.add_static("/static/", WEB_DIR, name="static")
    return app


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
async def _healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _index(request: web.Request) -> web.StreamResponse:
    idx = WEB_DIR / "index.html"
    if idx.is_file():
        return web.FileResponse(idx)
    return web.Response(text="UI assets missing (desktop/idevicetail/web/index.html)", status=500)


async def _state(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    bus: LogBus = request.app["bus"]
    store: Optional[Store] = request.app["store"]
    agent: AgentServer = request.app["agent"]
    filesink: FileSink = request.app["filesink"]
    ver = await m.engine.version()
    sf = filesink.info()
    sf["can_open"] = _is_loopback(request)
    return web.json_response(
        {
            "version": __version__,
            "uptime": time.time() - request.app["started"],
            "devices": [d.to_wire() for d in m.devices.values()],
            "captures": m.capture_status(),
            "engines": {
                "device": {"pymobiledevice3": ver, "available": bool(ver)},
                "agent": {"port": agent.port, "clients_total": agent.clients_total,
                          "bind_policy": agent.bind_policy},
            },
            "session_file": sf,
            "stats": {
                "published": bus.total_published,
                "ring": len(bus.snapshot()),
                "stored": store.rows_written if store else 0,
                "store_enabled": store is not None,
            },
        }
    )


async def _devices(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    return web.json_response([d.to_wire() for d in m.devices.values()])


async def _device_info(request: web.Request) -> web.Response:
    """Richer identity for the device card: marketing name, serial, capacity,
    build. Cached (a `lockdown info` call is ~1 s). Agent devices return what
    the `hello` frame gave us."""
    m: DeviceManager = request.app["manager"]
    cache: dict = request.app["devinfo_cache"]
    dev_id = request.match_info["id"]
    dev = m.devices.get(dev_id)
    if not dev:
        return web.json_response({"error": "unknown device"}, status=404)

    base = {
        "id": dev.id,
        "name": dev.name,
        "model_id": dev.model,
        "model": marketing_name(dev.model),
        "kind": device_kind(dev.model),
        "os": dev.os_version,
        "serial": "",
        "capacity": "",
        "build": "",
        "udid": dev.udid,
        "dev_mode": dev.developer_mode,
        "transports": sorted(dev.transports),
        "source": "agent" if "agent" in dev.transports else "device",
    }
    force = request.query.get("refresh") == "1"
    if not dev.udid:
        return web.json_response(base)
    if dev_id in cache and not force:
        return web.json_response(cache[dev_id])

    raw = await m.engine.device_info(dev.udid, mobdev2="pymd-usb" not in dev.transports)
    if raw:
        base["name"] = raw.get("DeviceName") or base["name"]
        base["model_id"] = raw.get("ProductType") or base["model_id"]
        base["model"] = marketing_name(base["model_id"])
        base["kind"] = device_kind(base["model_id"])
        base["os"] = raw.get("ProductVersion") or base["os"]
        base["build"] = raw.get("BuildVersion") or ""
        base["serial"] = raw.get("SerialNumber") or ""
        base["capacity"] = human_capacity(
            raw.get("TotalDiskCapacity") or raw.get("TotalDataCapacity")
        )
        if raw.get("DeveloperModeStatus") is not None:
            base["dev_mode"] = raw.get("DeveloperModeStatus")
    cache[dev_id] = base
    return web.json_response(base)


async def _session_file_info(request: web.Request) -> web.Response:
    fs: FileSink = request.app["filesink"]
    info = fs.info()
    info["can_open"] = _is_loopback(request)
    return web.json_response(info)


async def _session_file(request: web.Request) -> web.StreamResponse:
    """Download the live 'real-time file' (log or ndjson)."""
    fs: FileSink = request.app["filesink"]
    which = request.query.get("format", "log")
    path = fs.ndjson_path if which == "ndjson" else fs.log_path
    if not path.exists():
        return web.Response(text="(no lines written yet)", status=404)
    return web.FileResponse(
        path,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


async def _session_file_open(request: web.Request) -> web.Response:
    """Open the real-time file (or reveal it) with the desktop's OS handler.
    Only honoured for requests coming from this machine."""
    if not _is_loopback(request):
        return web.json_response(
            {"error": "open-file is only available from the machine running the server"},
            status=403,
        )
    fs: FileSink = request.app["filesink"]
    body = await _json_body(request)
    ok, detail = fs.open_in_os(body.get("which", "log"))
    return web.json_response({"ok": ok, "detail": detail})


async def _start_capture(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    dev_id = request.match_info["id"]
    body = await _json_body(request)
    mode = body.get("mode", "syslog")
    rsd = None
    if body.get("rsd_host") and body.get("rsd_port"):
        rsd = (str(body["rsd_host"]), int(body["rsd_port"]))
    mobdev2 = body.get("mobdev2")
    try:
        ct = await m.start_capture(
            dev_id,
            mode=mode,
            rsd=rsd,
            use_tunnel=bool(body.get("use_tunnel")),
            mobdev2=None if mobdev2 is None else bool(mobdev2),
            process=body.get("process") or None,
        )
    except (KeyError, ValueError) as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(
        {"ok": True, "session_id": ct.session_id, "mode": ct.mode, "wireless": ct.mobdev2}
    )


async def _stop_capture(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    body = await _json_body(request)
    await m.stop_capture(request.match_info["id"], body.get("mode"))
    return web.json_response({"ok": True})


async def _pull_crashes(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    cfg: Config = request.app["cfg"]
    bus: LogBus = request.app["bus"]
    dev_id = request.match_info["id"]
    dev = m.devices.get(dev_id)
    if not dev or not dev.udid:
        return web.json_response({"error": "unknown or non-paired device"}, status=400)
    dest = cfg.data_dir / "crashes" / dev.udid
    mobdev2 = "pymd-usb" not in dev.transports
    try:
        new_files = await m.engine.pull_crashes(dev.udid, dest, mobdev2=mobdev2)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=502)
    published = 0
    for f in new_files:
        if f.suffix.lower() not in (".ips", ".crash", ".panic", ".txt"):
            continue
        try:
            text = f.read_text("utf-8", "replace")
        except Exception:
            continue
        bus.publish(
            crash_to_record(
                text, filename=f.name, device_id=dev.id, device_name=dev.name
            )
        )
        published += 1
    return web.json_response({"ok": True, "files": [f.name for f in new_files], "published": published})


async def _sysdiagnose(request: web.Request) -> web.Response:
    m: DeviceManager = request.app["manager"]
    cfg: Config = request.app["cfg"]
    bus: LogBus = request.app["bus"]
    dev = m.devices.get(request.match_info["id"])
    if not dev or not dev.udid:
        return web.json_response({"error": "unknown or non-paired device"}, status=400)
    dest = cfg.data_dir / "sysdiagnose" / dev.udid
    mobdev2 = "pymd-usb" not in dev.transports

    async def _job() -> None:
        try:
            archive = await m.engine.sysdiagnose(dev.udid, dest, mobdev2=mobdev2)
            bus.publish(
                LogRecord(
                    ts=time.time(), device_id=dev.id, device_name=dev.name,
                    source=SRC_SYSDIAGNOSE, level=Level.notice, process="idevicetail",
                    subsystem="sysdiagnose",
                    message=f"sysdiagnose archive ready: {archive}",
                )
            )
        except Exception as e:  # noqa: BLE001
            bus.publish(
                LogRecord(
                    ts=time.time(), device_id=dev.id, device_name=dev.name,
                    source=SRC_SYSDIAGNOSE, level=Level.error, process="idevicetail",
                    subsystem="sysdiagnose", message=f"sysdiagnose failed: {e}",
                )
            )

    asyncio.ensure_future(_job())
    return web.json_response({"ok": True, "note": "sysdiagnose started; can take several minutes"})


async def _provision(request: web.Request) -> web.Response:
    """One-time USB steps: pair / wifi-sync / devmode. Returns pymd output."""
    m: DeviceManager = request.app["manager"]
    dev = m.devices.get(request.match_info["id"])
    if not dev or not dev.udid:
        return web.json_response({"error": "connect the device by USB once for provisioning"}, status=400)
    body = await _json_body(request)
    action = body.get("action")
    if action == "pair":
        rc, out = await m.engine.pair(dev.udid)
    elif action == "wifi-sync":
        rc, out = await m.engine.enable_wifi_sync(dev.udid, bool(body.get("on", True)))
    elif action == "devmode":
        rc, out = await m.engine.enable_developer_mode(dev.udid)
    else:
        return web.json_response({"error": "action must be pair | wifi-sync | devmode"}, status=400)
    return web.json_response({"ok": rc == 0, "rc": rc, "output": out})


async def _logs(request: web.Request) -> web.Response:
    store: Optional[Store] = request.app["store"]
    if not store:
        return web.json_response({"error": "storage disabled (--no-store)"}, status=409)
    q = request.query
    rows = await store.query(
        device_id=q.get("device_id") or None,
        level_min=LEVEL_RANK.get(q.get("level", "debug"), 0),
        text=q.get("q") or None,
        since=_float(q.get("since")),
        until=_float(q.get("until")),
        limit=min(int(q.get("limit", 5000)), 200_000),
        order=q.get("order", "asc"),
    )
    return web.json_response({"count": len(rows), "logs": rows})


async def _export(request: web.Request) -> web.StreamResponse:
    store: Optional[Store] = request.app["store"]
    bus: LogBus = request.app["bus"]
    q = request.query
    fmt = q.get("format", "ndjson")
    if fmt not in ("ndjson", "csv", "text"):
        return web.json_response({"error": "format must be ndjson|csv|text"}, status=400)
    if store and q.get("source", "store") == "store":
        rows = await store.query(
            device_id=q.get("device_id") or None,
            level_min=LEVEL_RANK.get(q.get("level", "debug"), 0),
            text=q.get("q") or None,
            since=_float(q.get("since")),
            until=_float(q.get("until")),
            limit=min(int(q.get("limit", 1_000_000)), 5_000_000),
            order="asc",
        )
    else:
        rows = bus.snapshot()
    payload = to_bytes(rows, fmt)
    fname = f"idevicetail-{int(time.time())}.{ 'txt' if fmt=='text' else fmt}"
    return web.Response(
        body=payload,
        headers={
            "Content-Type": content_type(fmt),
            "Content-Disposition": f'attachment; filename="{fname}"',
        },
    )


async def _ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024**2)
    await ws.prepare(request)
    bus: LogBus = request.app["bus"]
    hub: WSHub = request.app["hub"]
    m: DeviceManager = request.app["manager"]
    ctrl_q = hub.register()

    # 1) initial snapshot
    snap = bus.snapshot(limit=int(request.query.get("snapshot", 5000)))
    await ws.send_str(json.dumps({"type": "snapshot", "logs": snap,
                                  "devices": [d.to_wire() for d in m.devices.values()],
                                  "captures": m.capture_status()}, default=str))

    sub = bus.subscribe(maxsize=50_000)

    async def _forward() -> None:
        batch: list[dict] = []
        try:
            while not ws.closed:
                # drain any control-plane events first (low volume)
                while not ctrl_q.empty():
                    await ws.send_str(json.dumps(ctrl_q.get_nowait(), default=str))
                try:
                    item = await asyncio.wait_for(sub.get(), timeout=0.25)
                    batch.append(item)
                    if len(batch) < 500:
                        continue
                except asyncio.TimeoutError:
                    pass
                if batch:
                    await ws.send_str(json.dumps({"type": "logs", "logs": batch,
                                                  "dropped": sub.dropped}, default=str))
                    batch = []
        except (ConnectionResetError, asyncio.CancelledError):
            pass

    fwd = asyncio.create_task(_forward())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                with contextlib.suppress(Exception):
                    _handle_ws_cmd(json.loads(msg.data), request.app)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        fwd.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fwd
        sub.close()
        hub.unregister(ctrl_q)
    return ws


def _handle_ws_cmd(obj: dict, app: web.Application) -> None:
    m: DeviceManager = app["manager"]
    if obj.get("type") == "start":
        asyncio.ensure_future(
            m.start_capture(obj["device_id"], mode=obj.get("mode", "syslog"),
                            use_tunnel=bool(obj.get("use_tunnel")))
        )
    elif obj.get("type") == "stop":
        asyncio.ensure_future(m.stop_capture(obj["device_id"], obj.get("mode")))


# -- small helpers ------------------------------------------------------
async def _json_body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    with contextlib.suppress(Exception):
        return await request.json()
    return {}


def _float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_loopback(request: web.Request) -> bool:
    import ipaddress

    remote = request.remote or ""
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return remote in ("localhost", "")


def _display_host(host: str) -> str:
    return "localhost" if host in ("0.0.0.0", "::", "") else host
