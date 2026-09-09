"""Export log records to a file or an async byte stream.

Formats: ``ndjson`` (one JSON object per line, loss-less), ``csv``, ``text``
(human-readable, Console-style). Used by the ``export`` CLI command and the
``GET /api/export`` route.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable

_TEXT_COLS = ("ts", "device_name", "process", "level", "subsystem", "category", "message")
_CSV_COLS = (
    "id", "seq", "ts", "iso", "device_id", "device_name", "source",
    "level", "process", "pid", "subsystem", "category", "message",
)


def _iso_utc(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds")
    except Exception:
        return ""


def _clock_local(ts: float) -> str:
    """HH:MM:SS.mmm in the desktop's local time — matches the web UI and the
    on-device Console-style timestamp people expect."""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S.") + f"{int(ts % 1 * 1000):03d}"
    except Exception:
        return ""


def format_line(rec: dict[str, Any], fmt: str) -> str:
    if fmt == "ndjson":
        return json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
    if fmt == "text":
        t = _clock_local(rec.get("ts", 0))
        sub = rec.get("subsystem") or ""
        cat = rec.get("category") or ""
        label = f" [{sub}:{cat}]" if sub or cat else (f" [{sub}]" if sub else "")
        return (
            f"{t}  {rec.get('device_name', '?'):<16.16}  "
            f"{rec.get('process', '?'):<20.20}  {str(rec.get('level', '')).upper():<7}"
            f"{label}  {rec.get('message', '')}\n"
        )
    raise ValueError(f"unknown format {fmt!r}")


def write_file(records: Iterable[dict[str, Any]], path: str, fmt: str) -> int:
    n = 0
    if fmt == "csv":
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=_CSV_COLS, extrasaction="ignore")
            w.writeheader()
            for rec in records:
                row = dict(rec)
                row["iso"] = _iso_utc(row.get("ts", 0))
                w.writerow(row)
                n += 1
        return n
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(format_line(rec, fmt))
            n += 1
    return n


def to_bytes(records: Iterable[dict[str, Any]], fmt: str) -> bytes:
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row = dict(rec)
            row["iso"] = _iso_utc(row.get("ts", 0))
            w.writerow(row)
        return buf.getvalue().encode("utf-8")
    return "".join(format_line(r, fmt) for r in records).encode("utf-8")


def content_type(fmt: str) -> str:
    return {
        "ndjson": "application/x-ndjson",
        "csv": "text/csv",
        "text": "text/plain; charset=utf-8",
    }.get(fmt, "application/octet-stream")
