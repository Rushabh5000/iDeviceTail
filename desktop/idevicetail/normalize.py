"""Turn raw engine output into :class:`~idevicetail.models.LogRecord`.

Four sources, one shape:

* ``oslog_json_to_record``  — one line of ``pymobiledevice3 syslog live --format json``
  (preferred: structured, no parsing guesswork)
* ``parse_syslog_line``     — a line of ``pymobiledevice3 syslog live`` *text*
  (fallback for older pymobiledevice3 or the CLI's plain mode)
* ``agent_payload_to_record`` — one decoded JSON log object from the iOS agent
* ``crash_to_record``       — a crash report file (.ips / .crash) pulled off the device

Everything here is pure and unit-tested (``desktop/tests/test_normalize.py``).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .models import (
    SRC_AGENT,
    SRC_CRASH,
    SRC_DEVICE_OSLOG,
    SRC_DEVICE_SYSLOG,
    Level,
    LogRecord,
    parse_level,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# pymobiledevice3 / idevicesyslog style text lines. Very tolerant: optional
# hostname, optional image in (parens) OR {braces} (pymobiledevice3 >= 11 uses
# braces), optional [pid], optional leading [subsystem:category] label.
#   old idevicesyslog:  'Jun 22 10:15:32 iPhone SpringBoard(FrontBoard)[63] <Notice>: msg'
#   pymobiledevice3 11: '2026-09-09 00:54:19.046506 knowledgeconstructiond{CoreFoundation}[2123] <DEBUG>: msg'
_SYSLOG_RE = re.compile(
    r"""^
    (?P<ts>
        [A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?      # 'Jun 22 10:15:32.123'
      | \d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?           # '2026-06-22 10:15:32.123'
    )\s+
    (?:(?P<host>\S+)\s+)?
    (?P<process>[^\[\(\{<][^\[\(\{]*?)
    (?:[\(\{](?P<image>[^)}]*)[\)\}])?
    (?:\[(?P<pid>\d+)\])?
    \s*<(?P<level>[A-Za-z]+)>:\s*
    (?P<message>.*)
    $""",
    re.VERBOSE,
)

# leading "[com.apple.foo:Category]" or "[com.apple.foo]" on the message
_LABEL_RE = re.compile(r"^\[(?P<subsystem>[A-Za-z0-9_.\-]+)(?::(?P<category>[^\]]+))?\]\s*")

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_ONE_DAY = timedelta(days=1)


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _parse_ts(raw: str) -> float:
    raw = raw.strip()
    try:
        if raw[0].isalpha():  # 'Jun 22 10:15:32.123'  (no year -> assume current)
            mon, day, rest = raw.split(None, 2)
            hh, mm, ss = rest.split(":")
            now = datetime.now()
            sec = float(ss)
            dt = datetime(
                now.year, _MONTHS[mon], int(day), int(hh), int(mm),
                int(sec), int((sec % 1) * 1_000_000),
            )
            # Guard against year rollover near Jan 1.
            if dt - now > _ONE_DAY:
                dt = dt.replace(year=now.year - 1)
            return dt.timestamp()
        # ISO-ish
        raw = raw.replace("T", " ")
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in raw else "%Y-%m-%d %H:%M:%S"
        return datetime.strptime(raw, fmt).timestamp()
    except Exception:
        return time.time()


def _basename(path: str) -> str:
    if not path:
        return ""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or path


def _parse_ts_flexible(raw: Any) -> float:
    """Accept an epoch number or an ISO-ish string (naive => local time)."""
    if raw is None:
        return time.time()
    if isinstance(raw, (int, float)):
        return float(raw) / 1000.0 if raw > 10_000_000_000 else float(raw)
    s = str(raw).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return _parse_ts(s)


def oslog_json_to_record(
    obj: dict[str, Any],
    *,
    device_id: str,
    device_name: str,
    session_id: str = "",
    source: str = SRC_DEVICE_OSLOG,
) -> LogRecord:
    """One line of ``pymobiledevice3 syslog live --format json``.

    Fields seen in pymobiledevice3 >= 11:
      ``pid``/``procid``, ``thread_id``, ``timestamp`` (ISO, local, naive),
      ``level`` (UPPERCASE), ``image_name``, ``filename`` (process binary path),
      ``message``, ``label`` (``null`` or ``{"subsystem": ..., "category": ...}``).
    """
    label = obj.get("label")
    subsystem = category = ""
    if isinstance(label, dict):
        subsystem = str(label.get("subsystem") or "")
        category = str(label.get("category") or "")
    proc = _basename(str(obj.get("filename") or obj.get("image_name") or "")) or "?"
    pid = obj.get("pid")
    if pid in (None, ""):
        pid = obj.get("procid")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pid = None
    return LogRecord(
        ts=_parse_ts_flexible(obj.get("timestamp")),
        device_id=device_id,
        device_name=device_name,
        source=source,
        message=str(obj.get("message", "")),
        level=parse_level(obj.get("level")),
        process=proc,
        pid=pid,
        subsystem=subsystem,
        category=category,
        raw=json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
        session_id=session_id,
    )


def parse_syslog_line(
    line: str,
    *,
    device_id: str,
    device_name: str,
    session_id: str = "",
    source: str = SRC_DEVICE_SYSLOG,
) -> Optional[LogRecord]:
    """Parse one text line. Returns ``None`` for blank lines.

    Unmatched lines still produce a record (raw message, best-effort level) so
    nothing is silently lost — see the "malformed data" requirement.
    """
    raw = line.rstrip("\r\n")
    clean = strip_ansi(raw).strip()
    if not clean:
        return None

    m = _SYSLOG_RE.match(clean)
    if not m:
        # Best effort: find a <Level> token anywhere, else default.
        lvl = Level.info
        lm = re.search(r"<([A-Za-z]+)>", clean)
        if lm:
            lvl = parse_level(lm.group(1))
        return LogRecord(
            ts=time.time(), device_id=device_id, device_name=device_name,
            source=source, message=clean, level=lvl, raw=raw, session_id=session_id,
        )

    process = (m.group("process") or "").strip() or "?"
    message = m.group("message") or ""
    subsystem = category = ""
    lm = _LABEL_RE.match(message)
    if lm:
        subsystem = lm.group("subsystem") or ""
        category = (lm.group("category") or "").strip()
        message = message[lm.end():]

    return LogRecord(
        ts=_parse_ts(m.group("ts")),
        device_id=device_id,
        device_name=device_name,
        source=source,
        message=message.strip(),
        level=parse_level(m.group("level")),
        process=process,
        pid=int(m.group("pid")) if m.group("pid") else None,
        subsystem=subsystem,
        category=category,
        raw=raw,
        session_id=session_id,
    )


def agent_payload_to_record(
    obj: dict[str, Any],
    *,
    device_id: str,
    device_name: str,
    session_id: str = "",
) -> LogRecord:
    """One log object from the framed agent protocol (see docs/PROTOCOL.md)."""
    ts = obj.get("ts")
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = time.time()
    if ts > 10_000_000_000:  # milliseconds -> seconds
        ts /= 1000.0

    msg = str(obj.get("message", ""))
    extra = []
    if obj.get("file"):
        loc = obj["file"]
        if obj.get("line"):
            loc = f"{loc}:{obj['line']}"
        extra.append(loc)
    if obj.get("thread") is not None:
        extra.append(f"thread {obj['thread']}")
    if extra:
        msg = f"{msg}  ({', '.join(extra)})"

    return LogRecord(
        ts=ts,
        device_id=device_id,
        device_name=device_name,
        source=SRC_AGENT,
        message=msg,
        level=parse_level(obj.get("level")),
        process=str(obj.get("process", "") or ""),
        pid=_maybe_int(obj.get("pid")),
        subsystem=str(obj.get("subsystem", "") or ""),
        category=str(obj.get("category", "") or ""),
        raw=json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
        session_id=session_id,
    )


def _maybe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


_IPS_DATE_KEYS = ("captureTime", "date")
_LEGACY_KV = re.compile(r"^([A-Za-z /]+):\s+(.*)$")


def crash_to_record(
    text: str,
    *,
    filename: str,
    device_id: str,
    device_name: str,
    session_id: str = "",
    max_raw: int = 24_000,
) -> LogRecord:
    """Summarize a crash report (.ips modern JSON-header or legacy .crash)."""
    # process name from the filename prefix (before the first -YYYY- date) is a
    # good default for the many resource/jetsam/diagnostic .ips kinds.
    fn_proc = re.split(r"-\d{4}-\d\d-\d\d", filename, 1)[0].split(".")[0] or "?"
    process = fn_proc
    ts = time.time()
    summary = filename

    stripped = text.lstrip()
    if stripped.startswith("{"):
        # Modern .ips: first line is a JSON summary header, then a JSON body.
        first_nl = text.find("\n")
        header_txt = text[:first_nl] if first_nl != -1 else text
        try:
            header = json.loads(header_txt)
            process = str(
                header.get("app_name") or header.get("name")
                or header.get("procName") or fn_proc
            )
            for k in _IPS_DATE_KEYS:
                if header.get(k):
                    ts = _parse_iso(str(header[k])) or ts
                    break
            body_txt = text[first_nl + 1:] if first_nl != -1 else ""
            body = json.loads(body_txt) if body_txt.strip() else {}
            reason = (
                body.get("exception", {}).get("type")
                or body.get("termination", {}).get("indicator")
                or header.get("bug_type", "")
            )
            summary = f"{process} crashed: {reason}".strip().rstrip(":")
        except Exception:
            summary = f"{process} crash report {filename}"
    else:
        # Legacy plain text.
        for raw_line in text.splitlines()[:40]:
            kv = _LEGACY_KV.match(raw_line.strip())
            if not kv:
                continue
            key, val = kv.group(1).strip(), kv.group(2).strip()
            if key == "Process":
                process = val.split(" [")[0]
            elif key in ("Date/Time",):
                ts = _parse_iso(val) or ts
            elif key in ("Exception Type", "Termination Reason"):
                summary = f"{process} crashed: {val}"

    return LogRecord(
        ts=ts,
        device_id=device_id,
        device_name=device_name,
        source=SRC_CRASH,
        message=summary,
        level=Level.fault,
        process=process,
        subsystem="crash",
        category=filename,
        raw=text[:max_raw],
        session_id=session_id,
    )


def _parse_iso(s: str) -> Optional[float]:
    s = s.strip().replace("Z", "+00:00")
    for fmt in (
        None,  # fromisoformat
        "%Y-%m-%d %H:%M:%S.%f %z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            continue
    return None


__all__ = [
    "strip_ansi",
    "oslog_json_to_record",
    "parse_syslog_line",
    "agent_payload_to_record",
    "crash_to_record",
    "SRC_DEVICE_OSLOG",
    "SRC_DEVICE_SYSLOG",
]
