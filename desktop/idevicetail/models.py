"""Unified log + device data model. Both engines normalize into :class:`LogRecord`."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Level(str, Enum):
    debug = "debug"
    info = "info"
    notice = "notice"
    warning = "warning"
    error = "error"
    fault = "fault"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK[self]


_LEVEL_RANK = {
    Level.debug: 0,
    Level.info: 1,
    Level.notice: 2,
    Level.warning: 3,
    Level.error: 4,
    Level.fault: 5,
}

# Accept everything the various sources call these things and fold to our 6.
_LEVEL_ALIASES = {
    "debug": Level.debug,
    "trace": Level.debug,
    "info": Level.info,
    "informational": Level.info,
    "default": Level.notice,   # os_log "default" == Console "notice"
    "notice": Level.notice,
    "warn": Level.warning,
    "warning": Level.warning,
    "err": Level.error,
    "error": Level.error,
    "critical": Level.error,
    "crit": Level.error,
    "alert": Level.fault,
    "emerg": Level.fault,
    "emergency": Level.fault,
    "fault": Level.fault,
    "fatal": Level.fault,
}


def parse_level(raw: Any, default: Level = Level.info) -> Level:
    if raw is None:
        return default
    if isinstance(raw, Level):
        return raw
    s = str(raw).strip().strip("<>").strip("[]").lower()
    return _LEVEL_ALIASES.get(s, default)


# Source tags -----------------------------------------------------------------
SRC_DEVICE_SYSLOG = "device-syslog"   # lockdown syslog_relay  (classic, widest compat)
SRC_DEVICE_OSLOG = "device-oslog"     # lockdown os_trace_relay (structured firehose)
SRC_AGENT = "agent"                   # IDeviceTailKit app over framed TCP
SRC_CRASH = "crash"                   # crash report file pulled from the device
SRC_SYSDIAGNOSE = "sysdiagnose"       # note emitted when a sysdiagnose archive lands


@dataclass(slots=True)
class LogRecord:
    ts: float                         # epoch seconds, UTC
    device_id: str                    # UDID (Engine A) or agent-generated id (Engine B)
    device_name: str
    source: str
    message: str
    level: Level = Level.info
    process: str = ""
    pid: Optional[int] = None
    subsystem: str = ""
    category: str = ""
    raw: str = ""
    session_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    seq: int = 0                       # assigned by the bus, monotonic per process

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "ts": self.ts,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "source": self.source,
            "level": self.level.value,
            "process": self.process,
            "pid": self.pid,
            "subsystem": self.subsystem,
            "category": self.category,
            "message": self.message,
            "raw": self.raw,
            "session_id": self.session_id,
        }

    def to_row(self) -> tuple:
        return (
            self.id, self.ts, self.device_id, self.process, self.pid,
            self.subsystem, self.category, self.level.value, self.message,
            self.source, self.raw, self.session_id, self.seq,
        )


@dataclass(slots=True)
class Device:
    """A device we know about, from any of: Bonjour, the agent, or pymobiledevice3."""

    id: str                           # UDID if known, else agent id / bonjour key
    name: str = "Unknown device"
    model: str = ""
    os_version: str = ""
    address: str = ""                 # last known IP
    transports: set[str] = field(default_factory=set)   # {"agent", "bonjour-apple", "pymd-usb", "pymd-network"}
    udid: str = ""
    paired: Optional[bool] = None
    developer_mode: Optional[bool] = None
    connection_state: str = "discovered"   # discovered | connecting | streaming | error | offline
    last_seen: float = field(default_factory=time.time)
    detail: str = ""

    def touch(self) -> None:
        self.last_seen = time.time()

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "os_version": self.os_version,
            "address": self.address,
            "transports": sorted(self.transports),
            "udid": self.udid,
            "paired": self.paired,
            "developer_mode": self.developer_mode,
            "connection_state": self.connection_state,
            "last_seen": self.last_seen,
            "detail": self.detail,
        }
