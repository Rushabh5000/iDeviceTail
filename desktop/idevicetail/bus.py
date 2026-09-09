"""In-process log fan-out.

Every :class:`~idevicetail.models.LogRecord` from either engine is published here.
Consumers (the WebSocket server, the SQLite writer, exporters) each ``subscribe``
and get an independent asyncio queue.

Backpressure policy: each subscriber queue is bounded. If a consumer falls
behind, the *oldest* item in that consumer's queue is dropped to make room for
the newest — a slow browser tab can never stall the capture pipeline. Dropped
counts are tracked so the UI can show "N lines dropped".
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from .models import LogRecord


class Subscription:
    __slots__ = ("queue", "dropped", "_bus")

    def __init__(self, bus: "LogBus", maxsize: int) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self._bus = bus

    async def get(self) -> dict[str, Any]:
        return await self.queue.get()

    def close(self) -> None:
        self._bus._subs.discard(self)


class LogBus:
    def __init__(self, ring_size: int = 200_000) -> None:
        self._subs: set[Subscription] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._seq = 0
        self.total_published = 0

    # -- producers ---------------------------------------------------------
    def publish(self, rec: LogRecord) -> None:
        self._seq += 1
        rec.seq = self._seq
        wire = rec.to_wire()
        self._ring.append(wire)
        self.total_published += 1
        for sub in list(self._subs):
            _offer(sub, wire)

    def publish_many(self, recs: list[LogRecord]) -> None:
        for r in recs:
            self.publish(r)

    # -- consumers -------------------------------------------------------
    def subscribe(self, maxsize: int = 20_000) -> Subscription:
        sub = Subscription(self, maxsize)
        self._subs.add(sub)
        return sub

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None or limit >= len(self._ring):
            return list(self._ring)
        return list(self._ring)[-limit:]

    @property
    def seq(self) -> int:
        return self._seq


def _offer(sub: Subscription, wire: dict[str, Any]) -> None:
    try:
        sub.queue.put_nowait(wire)
        return
    except asyncio.QueueFull:
        pass
    # Drop oldest, retry once.
    try:
        sub.queue.get_nowait()
        sub.dropped += 1
    except asyncio.QueueEmpty:  # pragma: no cover - race
        pass
    try:
        sub.queue.put_nowait(wire)
    except asyncio.QueueFull:  # pragma: no cover - consumer is fully wedged
        sub.dropped += 1
