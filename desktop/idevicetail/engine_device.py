"""Engine A — real device logs via the ``pymobiledevice3`` CLI.

We shell out to ``pymobiledevice3`` (a separate process) rather than importing
it, for three reasons: (1) its CLI is more stable than its internals across
releases, (2) process isolation — a hang in the device transport can't take
down our event loop, and (3) restart-on-failure becomes "respawn the child",
which is exactly the reconnect behaviour we want.

Log stream: ``pymobiledevice3 syslog live --format json --label``. In
pymobiledevice3 >= 11 this emits one JSON object per line (NDJSON) with
``pid``/``procid``, ``timestamp``, ``level``, ``filename`` (process path),
``image_name``, ``message`` and ``label`` (``{subsystem,category}`` or null).
That is the full os_log firehose with labels — the same data Console.app shows.
On iOS < 17 and (in practice) iOS 17/18 it works over classic lockdown, USB
**or Wi-Fi**. iOS 17.4+ *may* need a tunnel for some services; pass
``rsd=(host,port)`` from your own ``pymobiledevice3 lockdown start-tunnel`` /
``remote tunneld``, or ``use_tunnel=True`` for the no-root userspace tunnel.

Requires a one-time USB pairing + "Trust", Wi-Fi sync enabled, and (iOS 16+)
Developer Mode ON.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from .normalize import strip_ansi

_KV_RE = re.compile(r"[\"']?([A-Za-z][A-Za-z0-9 _]+?)[\"']?\s*[:=│|]\s*[\"']?([^\"'│|]+?)[\"']?\s*$")
_BOX_CHARS = set("│├└┌┐┘─╭╮╯╰┤┬┴┼")


@dataclass(slots=True)
class PymdDevice:
    udid: str
    name: str = ""
    model: str = ""
    os_version: str = ""
    build: str = ""
    connection_type: str = ""     # "USB" | "Network"
    address: str = ""

    @property
    def wireless(self) -> bool:
        return self.connection_type.lower() in ("network", "wifi", "wi-fi")


class DeviceEngineError(RuntimeError):
    pass


class DeviceEngine:
    def __init__(self, pymd_bin: str = "pymobiledevice3") -> None:
        self._bin = pymd_bin
        self._argv0 = self._resolve_bin(pymd_bin)

    @staticmethod
    def _resolve_bin(name: str) -> list[str]:
        if shutil.which(name):
            return [name]
        if os.sep in name and Path(name).exists():
            return [name]
        return [sys.executable, "-m", "pymobiledevice3"]

    @staticmethod
    def _env() -> dict[str, str]:
        e = dict(os.environ)
        e["NO_COLOR"] = "1"          # rich: no ANSI in captured output
        e["PYTHONIOENCODING"] = "utf-8"
        e.setdefault("PYTHONUNBUFFERED", "1")
        return e

    @property
    def available(self) -> bool:
        return True

    async def version(self) -> Optional[str]:
        rc, out, err = await self._run(["version"], timeout=20)
        if rc != 0:
            return None
        text = (out or err).strip()
        m = re.search(r"\d+\.\d+(?:\.\d+)?", text)
        return m.group(0) if m else (text.splitlines()[0] if text else None)

    async def list_devices(self) -> list[PymdDevice]:
        """USB devices from usbmuxd + Wi-Fi devices discovered over Bonjour.

        On Windows, usbmuxd only bridges *network* devices when Apple's Mobile
        Device Service is doing the discovery; when it isn't, `usbmux list` is
        empty even though a paired device is on Wi-Fi. `bonjour mobdev2` finds
        it directly, so we always merge both.
        """
        out_devs: list[PymdDevice] = []
        rc, out, err = await self._run(["usbmux", "list"], timeout=25)
        usbmux_err = _clean_err(err) if rc != 0 else ""
        if rc == 0:
            out_devs += _parse_device_list(out)

        rc2, out2, err2 = await self._run(
            ["bonjour", "mobdev2", "--timeout", "3"], timeout=20
        )
        if rc2 == 0:
            for d in _parse_device_list(out2):
                d.connection_type = d.connection_type or "Network"
                out_devs.append(d)

        if not out_devs and usbmux_err:
            raise DeviceEngineError(usbmux_err)
        return _dedupe(out_devs)

    @staticmethod
    def _mobdev2_args(mobdev2: bool) -> list[str]:
        return ["--mobdev2"] if mobdev2 else []

    async def developer_mode_status(self, udid: str, *, mobdev2: bool = False) -> Optional[bool]:
        rc, out, err = await self._run(
            ["amfi", "developer-mode-status", "--udid", udid, *self._mobdev2_args(mobdev2)],
            timeout=25,
        )
        if rc != 0:
            return None
        blob = (out or "").strip().lower()
        if "true" in blob:
            return True
        if "false" in blob:
            return False
        return None

    async def device_info(self, udid: str, *, mobdev2: bool = False) -> dict:
        rc, out, err = await self._run(
            ["lockdown", "info", "--udid", udid, *self._mobdev2_args(mobdev2)], timeout=25
        )
        if rc != 0:
            return {}
        try:
            return json.loads(out)
        except Exception:
            return _loose_kv(out)

    # -- one-time provisioning helpers (USB) ---------------------------
    async def pair(self, udid: str) -> tuple[int, str]:
        rc, out, err = await self._run(["lockdown", "pair", "--udid", udid], timeout=120)
        return rc, _clean_err(out + err)

    async def enable_wifi_sync(self, udid: str, on: bool = True) -> tuple[int, str]:
        rc, out, err = await self._run(
            ["lockdown", "wifi-connections", "on" if on else "off", "--udid", udid], timeout=60
        )
        return rc, _clean_err(out + err) or f"wifi-connections {'on' if on else 'off'} (rc={rc})"

    async def enable_developer_mode(self, udid: str) -> tuple[int, str]:
        rc, out, err = await self._run(
            ["amfi", "enable-developer-mode", "--udid", udid], timeout=120
        )
        return rc, _clean_err(out + err)

    # -- crash reports ------------------------------------------------
    async def pull_crashes(
        self, udid: str, dest: Path, *, erase: bool = False, mobdev2: bool = False
    ) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        before = {p for p in dest.rglob("*") if p.is_file()}
        args = ["crash", "pull", str(dest), "--udid", udid, *self._mobdev2_args(mobdev2)]
        if erase:
            args.append("--erase")
        rc, out, err = await self._run(args, timeout=180)
        if rc != 0:
            raise DeviceEngineError(_clean_err(err) or f"`crash pull` exited {rc}")
        after = {p for p in dest.rglob("*") if p.is_file()}
        return sorted(after - before)

    # -- sysdiagnose ------------------------------------------------
    async def sysdiagnose(
        self, udid: str, dest: Path, *, timeout: float = 900, mobdev2: bool = False
    ) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        rc, out, err = await self._run(
            ["crash", "sysdiagnose", str(dest), "--udid", udid, "-t", str(int(timeout)),
             *self._mobdev2_args(mobdev2)],
            timeout=timeout + 30,
        )
        if rc != 0:
            raise DeviceEngineError(_clean_err(err) or f"`sysdiagnose` exited {rc}")
        m = re.search(r"(\S+sysdiagnose\S*\.tar\.gz)", out + err)
        if m and Path(m.group(1)).exists():
            return Path(m.group(1))
        archives = sorted(dest.rglob("sysdiagnose*.tar.gz"), key=lambda p: p.stat().st_mtime)
        if not archives:
            archives = sorted(dest.rglob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
        if archives:
            return archives[-1]
        raise DeviceEngineError("sysdiagnose finished but no archive was found")

    # -- live log stream --------------------------------------------
    async def stream(
        self,
        udid: str,
        *,
        mode: str = "syslog",
        rsd: Optional[tuple[str, int]] = None,
        use_tunnel: bool = False,
        mobdev2: bool = False,
        process: Optional[str] = None,
        pid: Optional[int] = None,
        on_stderr=None,
    ) -> AsyncIterator[str]:
        """Yield JSON log lines (one object per line) until the child exits.
        The caller parses them and handles reconnect.

        ``mobdev2=True`` reaches a Wi-Fi-connected device directly over Bonjour
        (no cable, no Apple usbmuxd network bridge needed)."""
        args = ["syslog", "live", "--format", "json", "--label"]
        if udid:
            args += ["--udid", udid]
        if rsd:
            args += ["--rsd", rsd[0], str(rsd[1])]
        elif use_tunnel:
            args += ["--userspace"]
        elif mobdev2:
            args += ["--mobdev2"]
        if process:
            args += ["--process-name", process]
        if pid:
            args += ["--pid", str(pid)]

        argv = self._argv0 + args
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=self._env(),
                limit=2**20,
            )
        except FileNotFoundError:
            raise DeviceEngineError(_PYMD_MISSING) from None

        async def _pump_stderr() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                line = strip_ansi(_decode(raw)).rstrip()
                s = line.strip().strip("".join(_BOX_CHARS)).strip()
                if s and not set(line) <= _BOX_CHARS | {" "} and on_stderr:
                    on_stderr(s)

        stderr_task = asyncio.create_task(_pump_stderr())
        try:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                yield _decode(raw)
        finally:
            stderr_task.cancel()
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

    # -- subprocess plumbing --------------------------------------
    async def _run(self, args: list[str], *, timeout: float = 30) -> tuple[int, str, str]:
        argv = self._argv0 + args
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=self._env(),
                limit=2**20,
            )
        except FileNotFoundError:
            return 127, "", _PYMD_MISSING
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", f"timed out after {timeout}s: pymobiledevice3 {' '.join(args)}"
        return proc.returncode or 0, strip_ansi(_decode(out_b)), strip_ansi(_decode(err_b))


def _decode(b: bytes) -> str:
    """pymobiledevice3 usually emits UTF-8; on some Windows consoles it falls
    back to the ANSI codepage. Try UTF-8, then cp1252, then latin-1."""
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


_PYMD_MISSING = (
    "pymobiledevice3 not found or not runnable. Install Engine A support with:\n"
    '    pip install "pymobiledevice3>=11"\n'
    "in the same environment, or set IDEVICETAIL_PYMD to its full path.\n"
    "NOTE: on Python 3.14 some pymobiledevice3 deps have no wheels yet — use a "
    "3.12/3.13 venv for Engine A."
)


# -- parsing helpers --------------------------------------------------
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$|:")


def _looks_like_ip(s: str) -> bool:
    return bool(_IP_RE.search(s or ""))


def _norm_dev(d: dict) -> PymdDevice:
    """Handle both `usbmux list` (UDID in Identifier) and `bonjour mobdev2`
    (Identifier = IP, real UDID in UniqueDeviceID, plus an `ip` field)."""
    g = {str(k).lower().replace(" ", ""): ("" if v is None else str(v)) for k, v in d.items()}
    ident = g.get("identifier", "")
    udid = (
        g.get("uniquedeviceid")
        or (ident if ident and not _looks_like_ip(ident) else "")
        or g.get("udid")
        or g.get("serialnumber")
        or ""
    )
    address = (
        g.get("ip")
        or g.get("networkaddress")
        or g.get("ipaddress")
        or g.get("address")
        or (ident if _looks_like_ip(ident) else "")
    )
    conn = g.get("connectiontype") or g.get("connection_type") or ""
    return PymdDevice(
        udid=udid,
        name=g.get("devicename") or g.get("name") or "",
        model=g.get("producttype") or g.get("hardwaremodel") or g.get("model") or "",
        os_version=g.get("productversion") or g.get("osversion") or "",
        build=g.get("buildversion") or g.get("build") or "",
        connection_type=conn,
        address=address,
    )


def _parse_device_list(text: str) -> list[PymdDevice]:
    text = strip_ansi(text).strip()
    if not text:
        return []
    # find the JSON array/object even if a banner precedes it
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start != -1:
        try:
            data = json.loads(text[start:])
            if isinstance(data, dict):
                data = [data]
            devs = [_norm_dev(x) for x in data if isinstance(x, dict)]
            return _dedupe([d for d in devs if d.udid])
        except Exception:
            pass
    # fallback: rich table / plist-ish key:value blocks
    blocks: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in text.splitlines():
        s = strip_ansi(line).strip().strip("│|+-").strip()
        if not s:
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        m = _KV_RE.match(s)
        if m:
            cur[m.group(1).strip()] = m.group(2).strip()
    if cur:
        blocks.append(cur)
    return _dedupe([d for d in (_norm_dev(b) for b in blocks) if d.udid])


def _dedupe(devs: list[PymdDevice]) -> list[PymdDevice]:
    """Merge entries that share a UDID. Prefer a USB connection_type (more
    reliable for streaming) but keep the Wi-Fi address from the network entry."""
    by_udid: dict[str, PymdDevice] = {}
    for d in devs:
        if not d.udid:
            continue
        cur = by_udid.get(d.udid)
        if cur is None:
            by_udid[d.udid] = d
            continue
        for f in ("name", "model", "os_version", "build", "address"):
            if not getattr(cur, f) and getattr(d, f):
                setattr(cur, f, getattr(d, f))
        # USB wins for connection_type; otherwise take whatever is set
        if d.connection_type.upper() == "USB" or not cur.connection_type:
            cur.connection_type = d.connection_type or cur.connection_type
    return list(by_udid.values())


def _loose_kv(text: str) -> dict:
    out: dict[str, str] = {}
    for line in strip_ansi(text).splitlines():
        m = _KV_RE.match(line.strip())
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _clean_err(text: str) -> str:
    """Strip rich box-drawing noise from a captured traceback/message."""
    keep = []
    for line in strip_ansi(text or "").splitlines():
        s = line.strip().strip("".join(_BOX_CHARS)).strip()
        if s and not set(line.strip()) <= _BOX_CHARS | {" ", "-", "+"}:
            keep.append(s)
    return "\n".join(keep[-12:]).strip()
