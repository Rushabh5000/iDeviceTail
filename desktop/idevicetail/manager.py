"""Device registry + capture supervision.

* Merges device sightings from three places (Bonjour, the agent's ``hello``,
  ``pymobiledevice3 usbmux list``) into one deduplicated table keyed by a stable
  id (UDID when known).
* Supervises Engine-A capture tasks: run ``DeviceEngine.stream`` -> normalize ->
  publish; when the child exits (Wi-Fi blip, device reboot, sleep/wake) it
  reconnects with exponential backoff + jitter for as long as capture is
  "desired". Engine B needs no supervision here — the iOS agent reconnects
  itself and the listener just accepts it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
import uuid
from typing import Awaitable, Callable, Optional

from .bus import LogBus
from .config import Config
from .engine_device import DeviceEngine, DeviceEngineError, PymdDevice
from .models import (
    SRC_DEVICE_OSLOG,
    SRC_DEVICE_SYSLOG,
    Device,
    LogRecord,
)
import json as _json

from .normalize import oslog_json_to_record, parse_syslog_line

Notify = Callable[[dict], None]


def _friendly_stream_error(line: str, *, wireless: bool) -> str:
    low = line.lower()
    if "pass --udid" in low or "choose a device non-interactively" in low or (
        "no device" in low
    ):
        if wireless:
            return ("device not reachable now — unlock the iPhone and keep it awake "
                    "(Auto-Lock: Never) / on a charger; a locked iPhone stops "
                    "advertising on Wi-Fi")
        return "device not found — is it connected and unlocked?"
    if "developermode" in low.replace(" ", "") or "developer mode" in low:
        return "Developer Mode is required — enable it via the Setup button (USB once)"
    if "notpaired" in low.replace(" ", "") or "not paired" in low or "pairing" in low:
        return "not paired with this computer — connect by USB once and use Setup"
    if "muxerror" in low.replace(" ", "") or "connectionrefused" in low.replace(" ", ""):
        return "connection refused — device asleep or off the network"
    return line


class CaptureTask:
    def __init__(
        self,
        manager: "DeviceManager",
        device: Device,
        *,
        mode: str,
        rsd: Optional[tuple[str, int]],
        use_tunnel: bool,
        process: Optional[str],
        mobdev2: bool = False,
    ) -> None:
        self.m = manager
        self.device = device
        self.mode = mode
        self.rsd = rsd
        self.use_tunnel = use_tunnel
        self.mobdev2 = mobdev2
        self.process = process
        self.session_id = uuid.uuid4().hex
        self.desired = True
        self.task: Optional[asyncio.Task] = None
        self.lines = 0
        self.restarts = 0
        self.last_error = ""
        self.started_at = time.time()

    def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name=f"capture:{self.device.id}:{self.mode}")

    async def stop(self) -> None:
        self.desired = False
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _run(self) -> None:
        cfg = self.m.cfg
        src = SRC_DEVICE_OSLOG if self.mode == "oslog" else SRC_DEVICE_SYSLOG
        backoff = cfg.device_reconnect_min
        if self.m.store:
            with contextlib.suppress(Exception):
                await self.m.store.start_session(self.session_id, self.device.id, src)
        try:
            while self.desired:
                self.device.connection_state = "connecting"
                self.m._emit_device(self.device)
                try:
                    self.last_error = ""
                    got_any = await self._stream_once(src)
                    # clean EOF -> device went away or pymd exited
                    if got_any:
                        backoff = cfg.device_reconnect_min
                        self.device.connection_state = "offline"
                        self.device.detail = "stream ended; reconnecting…"
                    elif self.last_error:
                        # keep the friendly reason _on_stderr already set
                        self.device.connection_state = (
                            "unreachable" if "not reachable" in self.device.detail else "error"
                        )
                    else:
                        self.device.connection_state = "offline"
                        self.device.detail = "no data yet; retrying…"
                except asyncio.CancelledError:
                    raise
                except DeviceEngineError as e:
                    self.last_error = str(e)
                    self.device.connection_state = "error"
                    self.device.detail = self.last_error
                except Exception as e:  # noqa: BLE001
                    self.last_error = repr(e)
                    self.device.connection_state = "error"
                    self.device.detail = self.last_error
                self.m._emit_device(self.device)
                if not self.desired:
                    break
                self.restarts += 1
                sleep = min(backoff, cfg.device_reconnect_max)
                sleep = sleep * (0.7 + 0.6 * random.random())  # jitter
                await asyncio.sleep(sleep)
                backoff = min(backoff * 2, cfg.device_reconnect_max)
        finally:
            self.device.connection_state = "offline"
            self.m._emit_device(self.device)
            if self.m.store:
                with contextlib.suppress(Exception):
                    await self.m.store.end_session(self.session_id)

    async def _stream_once(self, src: str) -> bool:
        got_any = False
        pending: Optional[LogRecord] = None   # only used for the text fallback
        bus = self.m.bus
        eng = self.m.engine

        def _on_stderr(line: str) -> None:
            # pymobiledevice3 prints a rich traceback to stderr on
            # terminate/broken-pipe; only surface stderr if the stream never
            # produced data (i.e. it genuinely failed to start).
            if got_any:
                return
            self.last_error = line
            self.device.detail = _friendly_stream_error(line, wireless=self.mobdev2)
            self.device.connection_state = "unreachable" if "not reachable" in self.device.detail \
                else self.device.connection_state
            self.m._emit_device(self.device)

        agen = eng.stream(
            self.device.udid or self.device.id,
            mode=self.mode,
            rsd=self.rsd,
            use_tunnel=self.use_tunnel,
            mobdev2=self.mobdev2,
            process=self.process,
            on_stderr=_on_stderr,
        )
        async for line in agen:
            s = line.strip()
            if not s:
                continue
            if not got_any:
                got_any = True
                self.device.connection_state = "streaming"
                self.device.detail = ""
                self.m._emit_device(self.device)

            # Preferred path: --format json -> one JSON object per line.
            if s[0] == "{":
                try:
                    obj = _json.loads(s)
                except ValueError:
                    obj = None
                if obj is not None:
                    if pending is not None:
                        bus.publish(pending)
                        self.lines += 1
                        pending = None
                    bus.publish(
                        oslog_json_to_record(
                            obj,
                            device_id=self.device.id,
                            device_name=self.device.name,
                            session_id=self.session_id,
                            source=src,
                        )
                    )
                    self.lines += 1
                    continue

            # Text fallback (older pymobiledevice3 / plain mode).
            if pending is not None and line[:1] in (" ", "\t"):
                pending.message += "\n" + s
                pending.raw += "\n" + line.rstrip("\r\n")
                continue
            if pending is not None:
                bus.publish(pending)
                self.lines += 1
            pending = parse_syslog_line(
                line, device_id=self.device.id, device_name=self.device.name,
                session_id=self.session_id, source=src,
            )
        if pending is not None:
            bus.publish(pending)
            self.lines += 1
        return got_any


class DeviceManager:
    def __init__(self, cfg: Config, bus: LogBus, store=None) -> None:
        self.cfg = cfg
        self.bus = bus
        self.store = store
        self.engine = DeviceEngine(cfg.pymd_bin)
        self.devices: dict[str, Device] = {}
        self.captures: dict[str, CaptureTask] = {}   # key: f"{device_id}:{mode}"
        self._notify: Optional[Notify] = None
        self._pymd_task: Optional[asyncio.Task] = None
        self._bonjour_keys: dict[str, str] = {}      # bonjour key -> device id

    def set_notifier(self, fn: Notify) -> None:
        self._notify = fn

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        self._pymd_task = asyncio.create_task(self._pymd_refresh_loop(), name="pymd-refresh")

    async def stop(self) -> None:
        if self._pymd_task:
            self._pymd_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pymd_task
        await asyncio.gather(*(c.stop() for c in list(self.captures.values())), return_exceptions=True)

    # -- device table ------------------------------------------------
    def merge_device(self, incoming: Device) -> Device:
        key = self._identity(incoming)
        cur = self.devices.get(key)
        if cur is None:
            # A real (UDID) device may arrive after a bonjour-only sighting at
            # the same IP; fold that sighting in instead of showing two rows.
            if incoming.udid and incoming.address:
                for k, other in list(self.devices.items()):
                    if not other.udid and other.address == incoming.address:
                        incoming.transports |= other.transports
                        self.devices.pop(k, None)
                        self._bonjour_keys[k] = incoming.udid
                        self._emit_device_removed(k)
            self.devices[key] = incoming
            self._emit_device(incoming)
            if self.store:
                asyncio.ensure_future(self.store.upsert_device(incoming))
            return incoming
        # merge fields (prefer non-empty / more specific)
        incoming_is_bonjour_only = incoming.transports and incoming.transports <= {
            "bonjour-apple", "agent-beacon"
        }
        for f in ("name", "model", "os_version", "address", "udid", "detail"):
            v = getattr(incoming, f)
            if not v:
                continue
            # don't let a bare Bonjour sighting overwrite the richer detail/name
            # of a device pymobiledevice3 already identified (with a UDID)
            if f in ("detail", "name") and cur.udid and incoming_is_bonjour_only:
                continue
            if not getattr(cur, f) or f in ("address", "detail"):
                setattr(cur, f, v)
        cur.transports |= incoming.transports
        if incoming.paired is not None:
            cur.paired = incoming.paired
        if incoming.developer_mode is not None:
            cur.developer_mode = incoming.developer_mode
        if incoming.connection_state not in ("discovered",):
            cur.connection_state = incoming.connection_state
        cur.touch()
        self._emit_device(cur)
        return cur

    def lose_bonjour(self, bonjour_key: str) -> None:
        dev_id = self._bonjour_keys.pop(bonjour_key, None)
        dev = self.devices.get(dev_id) if dev_id else self.devices.get(bonjour_key)
        if not dev:
            return
        dev.transports.discard("bonjour-apple")
        dev.transports.discard("agent-beacon")
        if not dev.transports or dev.transports <= {"pymd-usb"}:
            dev.detail = "no longer advertised on the network"
        dev.touch()
        self._emit_device(dev)

    def _identity(self, d: Device) -> str:
        if d.udid:
            return d.udid
        # try to attach a bonjour sighting to an existing device by IP
        if "bonjour-apple" in d.transports and d.address:
            for dev in self.devices.values():
                if dev.address == d.address and dev.udid:
                    self._bonjour_keys[d.id] = dev.udid
                    return dev.udid
        return d.id

    def _emit_device(self, d: Device) -> None:
        if self._notify:
            self._notify({"type": "device", "device": d.to_wire()})

    def _emit_device_removed(self, device_id: str) -> None:
        if self._notify:
            self._notify({"type": "device_removed", "id": device_id})

    # -- pymobiledevice3 polling ------------------------------------
    async def _pymd_refresh_loop(self) -> None:
        try:
            while True:
                await self._refresh_pymd_once()
                await asyncio.sleep(6)
        except asyncio.CancelledError:
            pass

    async def _refresh_pymd_once(self) -> None:
        try:
            pdevs = await self.engine.list_devices()
        except Exception as e:  # pymd missing / usbmuxd down — not fatal
            self._maybe_pymd_warning(str(e))
            return

        seen_udids: set[str] = set()
        for p in pdevs:
            seen_udids.add(p.udid)
            dev = Device(
                id=p.udid,
                udid=p.udid,
                name=p.name or f"iPhone {p.udid[:6]}",
                model=p.model,
                os_version=p.os_version,
                address=p.address,
                connection_state="discovered",
            )
            dev.transports.add("pymd-network" if p.wireless else "pymd-usb")
            dev.paired = True
            if p.wireless and not p.address:
                dev.detail = "on Wi-Fi (Bonjour)"
            elif p.wireless:
                dev.detail = f"on Wi-Fi ({p.address})"
            else:
                dev.detail = "connected by USB"
            merged = self.merge_device(dev)
            if merged.developer_mode is None:
                merged.developer_mode = await self.engine.developer_mode_status(
                    p.udid, mobdev2=p.wireless
                )
                self._emit_device(merged)

        # A previously-seen pymd device that dropped out of the list is now
        # unreachable (iOS devices leave Wi-Fi when locked / off charge).
        for dev in self.devices.values():
            pymd_tp = {"pymd-usb", "pymd-network"} & dev.transports
            if not pymd_tp or dev.udid in seen_udids:
                continue
            if self.captures.get(self.capture_key(dev.id, "syslog")) or \
               self.captures.get(self.capture_key(dev.id, "oslog")):
                continue  # capture task owns the state while it retries
            if dev.connection_state not in ("offline", "unreachable"):
                dev.connection_state = "offline"
                dev.detail = "not reachable now — wake the device / keep it on Wi-Fi (locked iPhones drop off)"
                dev.touch()
                self._emit_device(dev)

    def _maybe_pymd_warning(self, msg: str) -> None:
        if self._notify:
            self._notify({"type": "engine", "engine": "device", "state": "unavailable", "detail": msg})

    # -- capture control ------------------------------------------
    def capture_key(self, device_id: str, mode: str) -> str:
        return f"{device_id}:{mode}"

    async def start_capture(
        self,
        device_id: str,
        *,
        mode: str = "syslog",
        rsd: Optional[tuple[str, int]] = None,
        use_tunnel: bool = False,
        mobdev2: Optional[bool] = None,
        process: Optional[str] = None,
    ) -> CaptureTask:
        dev = self.devices.get(device_id)
        if dev is None:
            raise KeyError(f"unknown device {device_id!r}")
        if "agent" in dev.transports and not dev.udid:
            raise ValueError("this is an agent device; its logs already stream automatically")
        if not dev.udid:
            raise ValueError(
                "this device is not paired with this computer yet — connect it by USB "
                "once and use Setup (pair + Wi-Fi sync + Developer Mode)"
            )
        # Auto: use Bonjour transport when the device is only reachable over Wi-Fi.
        if mobdev2 is None:
            mobdev2 = "pymd-usb" not in dev.transports and (
                "pymd-network" in dev.transports or "bonjour-apple" in dev.transports
            )
        key = self.capture_key(device_id, mode)
        existing = self.captures.get(key)
        if existing and existing.desired:
            return existing
        ct = CaptureTask(
            self, dev, mode=mode, rsd=rsd, use_tunnel=use_tunnel,
            mobdev2=bool(mobdev2), process=process,
        )
        self.captures[key] = ct
        ct.start()
        return ct

    async def stop_capture(self, device_id: str, mode: Optional[str] = None) -> None:
        keys = (
            [self.capture_key(device_id, mode)]
            if mode
            else [k for k in self.captures if k.startswith(device_id + ":")]
        )
        for k in keys:
            ct = self.captures.pop(k, None)
            if ct:
                await ct.stop()

    def capture_status(self) -> list[dict]:
        out = []
        for key, ct in self.captures.items():
            out.append(
                {
                    "key": key,
                    "device_id": ct.device.id,
                    "device_name": ct.device.name,
                    "mode": ct.mode,
                    "session_id": ct.session_id,
                    "desired": ct.desired,
                    "state": ct.device.connection_state,
                    "lines": ct.lines,
                    "restarts": ct.restarts,
                    "last_error": ct.last_error,
                    "uptime": time.time() - ct.started_at,
                }
            )
        return out
