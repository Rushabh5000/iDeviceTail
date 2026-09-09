"""Optional SQLite persistence.

Trade-off (see docs/ARCHITECTURE.md): the live pipeline is a pure in-memory
stream; storage is a *side consumer* of the bus. If you don't need history,
run ``serve --no-store`` and this module is never touched — nothing else
changes. When enabled it batches inserts (``db_flush_rows`` / ``db_flush_seconds``)
inside a single transaction so a 10k lines/sec burst is ~20 commits/sec.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite

from .bus import LogBus
from .models import Device

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    model         TEXT,
    os_version    TEXT,
    udid          TEXT,
    first_seen    REAL,
    last_seen     REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    device_id   TEXT REFERENCES devices(id),
    source      TEXT,
    started_at  REAL,
    ended_at    REAL,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id          TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    device_id   TEXT,
    process     TEXT,
    process_id  INTEGER,
    subsystem   TEXT,
    category    TEXT,
    level       TEXT,
    message     TEXT,
    source      TEXT,
    raw_message TEXT,
    session_id  TEXT,
    seq         INTEGER
);

CREATE INDEX IF NOT EXISTS ix_logs_ts        ON logs(ts);
CREATE INDEX IF NOT EXISTS ix_logs_device_ts ON logs(device_id, ts);
CREATE INDEX IF NOT EXISTS ix_logs_session   ON logs(session_id);
CREATE INDEX IF NOT EXISTS ix_logs_level     ON logs(level);
"""

_INSERT = """
INSERT OR IGNORE INTO logs
(id, ts, device_id, process, process_id, subsystem, category, level, message, source, raw_message, session_id, seq)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


class Store:
    def __init__(self, db_path: Path, *, flush_rows: int = 500, flush_seconds: float = 1.0) -> None:
        self.db_path = db_path
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self._db: Optional[aiosqlite.Connection] = None
        self._task: Optional[asyncio.Task] = None
        self._pending: list[tuple] = []
        self._stop = asyncio.Event()
        self.rows_written = 0

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        for stmt in filter(None, (s.strip() for s in _SCHEMA.split(";"))):
            await self._db.execute(stmt)
        await self._db.commit()

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._flush()
        if self._db:
            await self._db.close()
            self._db = None

    # -- device / session bookkeeping ------------------------------------
    async def upsert_device(self, d: Device) -> None:
        if not self._db:
            return
        now = time.time()
        await self._db.execute(
            """INSERT INTO devices(id,name,model,os_version,udid,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, model=excluded.model,
                 os_version=excluded.os_version, udid=excluded.udid,
                 last_seen=excluded.last_seen""",
            (d.id, d.name, d.model, d.os_version, d.udid, now, now),
        )
        await self._db.commit()

    async def start_session(self, session_id: str, device_id: str, source: str, note: str = "") -> None:
        if not self._db:
            return
        await self._db.execute(
            "INSERT OR REPLACE INTO sessions(id,device_id,source,started_at,ended_at,note) VALUES(?,?,?,?,?,?)",
            (session_id, device_id, source, time.time(), None, note),
        )
        await self._db.commit()

    async def end_session(self, session_id: str) -> None:
        if not self._db:
            return
        await self._db.execute(
            "UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL",
            (time.time(), session_id),
        )
        await self._db.commit()

    # -- log ingest ----------------------------------------------------
    def run(self, bus: LogBus) -> None:
        """Subscribe now (synchronously, so no records are missed between this
        call and the task's first tick) and start the background consumer."""
        sub = bus.subscribe(maxsize=100_000)
        self._task = asyncio.create_task(self._consume(sub), name="store-consumer")

    async def _consume(self, sub) -> None:
        last_flush = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    wire = await asyncio.wait_for(sub.get(), timeout=self.flush_seconds)
                    self._pending.append(_wire_to_row(wire))
                except asyncio.TimeoutError:
                    pass
                if (
                    len(self._pending) >= self.flush_rows
                    or (self._pending and time.monotonic() - last_flush >= self.flush_seconds)
                ):
                    await self._flush()
                    last_flush = time.monotonic()
        finally:
            sub.close()
            await self._flush()

    async def _flush(self) -> None:
        if not self._db or not self._pending:
            return
        batch, self._pending = self._pending, []
        try:
            await self._db.executemany(_INSERT, batch)
            await self._db.commit()
            self.rows_written += len(batch)
        except Exception:
            # Never let a storage hiccup kill capture; drop the batch.
            pass

    # -- queries (used by REST /api/logs and export) --------------------
    async def query(
        self,
        *,
        device_id: str | None = None,
        level_min: int = 0,
        text: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 5000,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        if not self._db:
            return []
        where = ["1=1"]
        args: list[Any] = []
        if device_id:
            where.append("device_id = ?")
            args.append(device_id)
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if until is not None:
            where.append("ts <= ?")
            args.append(until)
        if text:
            where.append("(message LIKE ? OR process LIKE ? OR subsystem LIKE ?)")
            like = f"%{text}%"
            args += [like, like, like]
        if level_min > 0:
            where.append("level IN (" + ",".join("?" * len(_LEVELS_AT_OR_ABOVE(level_min))) + ")")
            args += _LEVELS_AT_OR_ABOVE(level_min)
        sql = (
            f"SELECT * FROM logs WHERE {' AND '.join(where)} "
            f"ORDER BY ts {'DESC' if order == 'desc' else 'ASC'}, seq {'DESC' if order == 'desc' else 'ASC'} "
            f"LIMIT ?"
        )
        args.append(int(limit))
        cur = await self._db.execute(sql, args)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def iter_all(self, **kw: Any) -> Iterable[dict[str, Any]]:
        kw.setdefault("limit", 10_000_000)
        return await self.query(**kw)


_LEVEL_ORDER = ["debug", "info", "notice", "warning", "error", "fault"]


def _LEVELS_AT_OR_ABOVE(rank: int) -> list[str]:
    return _LEVEL_ORDER[rank:]


def _wire_to_row(w: dict[str, Any]) -> tuple:
    return (
        w["id"], w["ts"], w["device_id"], w["process"], w["pid"],
        w["subsystem"], w["category"], w["level"], w["message"],
        w["source"], w["raw"], w["session_id"], w["seq"],
    )
