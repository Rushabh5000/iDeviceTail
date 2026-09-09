"""Command-line entry point.

    idevicetail serve                 # discovery + agent listener + web UI (the main mode)
    idevicetail doctor               # environment / connectivity check
    idevicetail discover [--secs N]  # list devices seen via Bonjour + pymobiledevice3
    idevicetail devices              # pymobiledevice3 device list only
    idevicetail stream <UDID> [--oslog] [--tunnel] [--rsd HOST PORT] [--process NAME]
    idevicetail crash-pull <UDID> <DIR>
    idevicetail sysdiagnose <UDID> [<DIR>]
    idevicetail pair <UDID> | wifi-sync <UDID> [--off] | devmode <UDID>
    idevicetail export --format ndjson|csv|text [--db PATH] [-o FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="idevicetail", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"idevicetail {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the full service (default use)")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--agent-port", type=int, default=None)
    s.add_argument("--data-dir", default=None)
    s.add_argument("--no-store", action="store_true")
    s.add_argument("--no-apple-discovery", action="store_true")
    s.add_argument("--bind-policy", choices=["lan", "any", "local"], default=None)
    s.add_argument("--no-open", action="store_true", help="don't open the web UI in a browser")

    sub.add_parser("doctor", help="check environment and ports")

    d = sub.add_parser("discover", help="list discoverable devices")
    d.add_argument("--secs", type=float, default=8.0)

    sub.add_parser("devices", help="pymobiledevice3 device list")

    st = sub.add_parser("stream", help="print a live log stream for one device")
    st.add_argument("udid")
    st.add_argument("--oslog", action="store_true", help="os_trace firehose (needs tunnel on iOS 17.4+)")
    st.add_argument("--tunnel", action="store_true", help="let pymobiledevice3 bring up a userspace tunnel")
    st.add_argument("--rsd", nargs=2, metavar=("HOST", "PORT"), help="use an existing RSD tunnel")
    st.add_argument("--process", default=None)
    st.add_argument("--raw", action="store_true", help="print unparsed lines")

    cp = sub.add_parser("crash-pull")
    cp.add_argument("udid")
    cp.add_argument("dest")
    cp.add_argument("--erase", action="store_true")

    sd = sub.add_parser("sysdiagnose")
    sd.add_argument("udid")
    sd.add_argument("dest", nargs="?", default="./sysdiagnose")

    for name in ("pair", "devmode"):
        q = sub.add_parser(name)
        q.add_argument("udid")
    ws = sub.add_parser("wifi-sync")
    ws.add_argument("udid")
    ws.add_argument("--off", action="store_true")

    ex = sub.add_parser("export")
    ex.add_argument("--format", choices=["ndjson", "csv", "text"], default="ndjson")
    ex.add_argument("--db", default=None)
    ex.add_argument("-o", "--out", default=None)
    ex.add_argument("--device-id", default=None)
    ex.add_argument("--level", default="debug")
    ex.add_argument("-q", "--query", default=None)

    args = p.parse_args(argv)
    try:
        return _DISPATCH[args.cmd](args)
    except KeyboardInterrupt:
        return 130


# -- commands -----------------------------------------------------------
def _serve(args) -> int:
    cfg = Config()
    if args.host is not None:
        cfg.host = args.host
    if args.port is not None:
        cfg.port = args.port
    if args.agent_port is not None:
        cfg.agent_port = args.agent_port
    if args.data_dir is not None:
        cfg.data_dir = Path(args.data_dir)
    if args.no_store:
        cfg.store_enabled = False
    if args.no_apple_discovery:
        cfg.discover_apple = False
    if args.bind_policy is not None:
        cfg.bind_policy = args.bind_policy

    from .server import run

    url = f"http://localhost:{cfg.port}"

    # Idempotent: if another idevicetail is already serving this port, don't start
    # a second one (and don't open a second browser tab) — just point at it.
    if _http_ok(f"{url}/healthz"):
        print(f"idevicetail is already running at {url}")
        if not args.no_open:
            _open_browser(url)
        return 0
    if not _port_free(cfg.host, cfg.port):
        print(f"port {cfg.port} is in use by something else — pick another with --port")
        return 1

    print(f"idevicetail {__version__}  —  Ctrl-C to stop")
    if not args.no_open:
        _schedule_browser(url)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


def _doctor(args) -> int:
    ok = True
    print(f"idevicetail {__version__}")
    print(f"python       {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        ok = False
        print("  ! Python 3.10+ required")

    for mod in ("aiohttp", "zeroconf", "aiosqlite"):
        try:
            m = __import__(mod)
            print(f"{mod:<12} {getattr(m, '__version__', 'ok')}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"{mod:<12} MISSING ({e})")

    cfg = Config()
    eng = _engine(cfg)
    ver = asyncio.run(eng.version())
    if ver:
        print(f"pymobiledevice3 {ver}   (Engine A available)")
    else:
        print("pymobiledevice3 not found   (Engine A disabled; Engine B still works)")
        print('  install with:  pip install "pymobiledevice3>=4.14"')
        if sys.version_info >= (3, 14):
            print(f"  NOTE: you are on Python {sys.version.split()[0]}. Some pymobiledevice3")
            print("        deps (lzfse/pylzss) have no wheels for it yet and need a C compiler.")
            print("        Easiest fix: install 3.13 or 3.12 and build the venv with that:")
            print("          py install 3.12   &&   py -3.12 -m venv .venv")
            print('          .venv\\Scripts\\python -m pip install -e ".[device]"')

    for port in (cfg.port, cfg.agent_port):
        free = _port_free(cfg.host, port)
        print(f"port {port:<6} {'free' if free else 'IN USE'}")
        ok &= free

    if sys.platform == "win32":
        print("\nWindows firewall: allow python.exe on Private networks, or run:")
        print(f'  netsh advfirewall firewall add rule name="idevicetail" dir=in action=allow '
              f'protocol=TCP localport={cfg.port},{cfg.agent_port} profile=private')
    print("\nOK" if ok else "\nsome checks failed (see above)")
    return 0 if ok else 1


def _discover(args) -> int:
    async def go() -> None:
        from .discovery import Discovery

        seen: dict[str, dict] = {}

        def on_dev(d) -> None:
            seen[d.id] = d.to_wire()
            print(f"  + {d.name:<28}  {','.join(sorted(d.transports)):<24}  {d.address}")

        disc = Discovery(on_device=on_dev, on_lost=lambda k: None)
        await disc.start()
        print(f"listening {args.secs:.0f}s for _apple-mobdev2._tcp and _idevtail._tcp ...")
        # also do a pymd pass
        try:
            for pd in await _engine(Config()).list_devices():
                tag = "pymd-network" if pd.wireless else "pymd-usb"
                print(f"  + {pd.name or pd.udid:<28}  {tag:<24}  {pd.address}  iOS {pd.os_version}")
        except Exception as e:  # noqa: BLE001
            print(f"  (pymobiledevice3 list unavailable: {e})")
        await asyncio.sleep(args.secs)
        await disc.stop()
        print(f"\n{len(seen)} device(s) via Bonjour")

    asyncio.run(go())
    return 0


def _devices(args) -> int:
    async def go() -> None:
        devs = await _engine(Config()).list_devices()
        if not devs:
            print("no devices (USB or Wi-Fi). Is the device unlocked / paired / Wi-Fi sync on?")
            return
        for d in devs:
            print(f"{d.udid}  {d.name!r}  {d.model}  iOS {d.os_version}  "
                  f"[{d.connection_type or '?'}]  {d.address}")

    asyncio.run(go())
    return 0


def _stream(args) -> int:
    import json as _json

    from .normalize import oslog_json_to_record, parse_syslog_line

    async def go() -> None:
        eng = _engine(Config())
        rsd = (args.rsd[0], int(args.rsd[1])) if args.rsd else None
        mode = "oslog" if args.oslog else "syslog"
        print(f"# streaming {mode} from {args.udid}  (Ctrl-C to stop)", file=sys.stderr)
        backoff = 1.0
        while True:
            got = False
            try:
                async for line in eng.stream(args.udid, mode=mode, rsd=rsd,
                                             use_tunnel=args.tunnel, process=args.process,
                                             on_stderr=lambda s: print(f"# {s}", file=sys.stderr)):
                    got = True
                    if args.raw:
                        sys.stdout.write(line)
                        continue
                    s = line.strip()
                    if not s:
                        continue
                    rec = None
                    if s[0] == "{":
                        try:
                            rec = oslog_json_to_record(_json.loads(s),
                                                       device_id=args.udid, device_name=args.udid)
                        except ValueError:
                            rec = None
                    if rec is None:
                        rec = parse_syslog_line(line, device_id=args.udid, device_name=args.udid)
                    if rec:
                        t = time.strftime("%H:%M:%S", time.localtime(rec.ts))
                        if rec.subsystem and rec.category:
                            lbl = f"[{rec.subsystem}:{rec.category}] "
                        elif rec.subsystem:
                            lbl = f"[{rec.subsystem}] "
                        else:
                            lbl = ""
                        print(f"{t}  {rec.process:<20.20} {rec.level.value.upper():<7} {lbl}{rec.message}")
            except KeyboardInterrupt:
                return
            except (BrokenPipeError, OSError):
                return  # downstream consumer (head/grep/…) went away — stop quietly
            except Exception as e:  # noqa: BLE001
                print(f"# stream error: {e}", file=sys.stderr)
            backoff = 1.0 if got else min(backoff * 2, 30)
            try:
                print(f"# reconnecting in {backoff:.0f}s ...", file=sys.stderr)
            except OSError:
                return
            await asyncio.sleep(backoff)

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    return 0


def _crash_pull(args) -> int:
    async def go() -> None:
        files = await _engine(Config()).pull_crashes(args.udid, Path(args.dest), erase=args.erase)
        print(f"{len(files)} new file(s) -> {args.dest}")
        for f in files:
            print(f"  {f}")

    asyncio.run(go())
    return 0


def _sysdiagnose(args) -> int:
    async def go() -> None:
        print("triggering sysdiagnose (this can take 5-10 minutes; keep the device unlocked) ...")
        archive = await _engine(Config()).sysdiagnose(args.udid, Path(args.dest))
        print(f"archive: {archive}")

    asyncio.run(go())
    return 0


def _pair(args) -> int:
    rc, out = asyncio.run(_engine(Config()).pair(args.udid))
    print(out)
    print("Now tap 'Trust' on the device and re-run if needed.")
    return rc


def _wifi_sync(args) -> int:
    rc, out = asyncio.run(_engine(Config()).enable_wifi_sync(args.udid, not args.off))
    print(out or f"wifi-sync {'off' if args.off else 'on'} (rc={rc})")
    return rc


def _devmode(args) -> int:
    rc, out = asyncio.run(_engine(Config()).enable_developer_mode(args.udid))
    print(out)
    print("The device will reboot; after unlock, confirm 'Turn On' under Settings > Privacy & Security > Developer Mode.")
    return rc


def _export(args) -> int:
    from .exporter import write_file
    from .store import Store

    async def go() -> int:
        cfg = Config()
        db = Path(args.db) if args.db else cfg.db_path
        if not db.exists():
            print(f"no database at {db}", file=sys.stderr)
            return 1
        store = Store(db)
        await store.open()
        from .models import Level
        rank = {lv.value: lv.rank for lv in Level}.get(args.level, 0)
        rows = await store.query(device_id=args.device_id, level_min=rank,
                                 text=args.query, limit=5_000_000, order="asc")
        await store.close()
        out = args.out or f"idevicetail-export.{ 'txt' if args.format=='text' else args.format}"
        n = write_file(rows, out, args.format)
        print(f"wrote {n} record(s) -> {out}")
        return 0

    return asyncio.run(go())


# -- helpers ----------------------------------------------------------
def _engine(cfg: Config):
    from .engine_device import DeviceEngine

    return DeviceEngine(cfg.pymd_bin)


def _port_free(host: str, port: int) -> bool:
    h = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((h, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _http_ok(url: str, timeout: float = 0.6) -> bool:
    """True if a GET to `url` returns 2xx — used to detect an already-running instance."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (localhost only)
            return 200 <= r.status < 300
    except Exception:
        return False


def _open_browser(url: str) -> None:
    """Open the default browser exactly once. `os.startfile` (ShellExecute) is
    the reliable single-tab path on Windows; `webbrowser` elsewhere."""
    import os as _os

    try:
        if sys.platform == "win32" and hasattr(_os, "startfile"):
            _os.startfile(url)  # type: ignore[attr-defined]
        else:
            import webbrowser

            webbrowser.open(url, new=2)
    except Exception:
        pass


_browser_opened = False


def _schedule_browser(url: str, delay: float = 1.5) -> None:
    import threading

    def _go() -> None:
        global _browser_opened
        if _browser_opened:
            return
        _browser_opened = True
        _open_browser(url)

    threading.Timer(delay, _go).start()
    print(f"opening {url} in your browser …")


_DISPATCH = {
    "serve": _serve,
    "doctor": _doctor,
    "discover": _discover,
    "devices": _devices,
    "stream": _stream,
    "crash-pull": _crash_pull,
    "sysdiagnose": _sysdiagnose,
    "pair": _pair,
    "wifi-sync": _wifi_sync,
    "devmode": _devmode,
    "export": _export,
}


if __name__ == "__main__":
    raise SystemExit(main())
