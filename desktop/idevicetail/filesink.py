"""Rolling "real-time file" writer.

One pair of files per ``serve`` run, under ``<data_dir>/sessions/``:

* ``realtime-<timestamp>.log``    human-readable (Console-style) lines
* ``realtime-<timestamp>.ndjson`` loss-less, one JSON object per line

The web UI's **Open File** button downloads / reveals the ``.log``. This is a
side consumer of the bus, exactly like :class:`~idevicetail.store.Store` — capture
never blocks on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .bus import LogBus
from .exporter import format_line

ROTATE_BYTES = 64 * 1024 * 1024


class FileSink:
    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = self.dir / f"realtime-{stamp}.log"
        self.ndjson_path = self.dir / f"realtime-{stamp}.ndjson"
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._rotations = 0
        self.lines_written = 0
        self.started_at = time.time()

    # -- lifecycle ----------------------------------------------------
    def run(self, bus: LogBus) -> None:
        sub = bus.subscribe(maxsize=100_000)
        self._task = asyncio.create_task(self._consume(sub), name="filesink")

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _consume(self, sub) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not self._stop.is_set():
                try:
                    wire = await asyncio.wait_for(sub.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                batch = [wire]
                # opportunistically drain a burst
                for _ in range(2000):
                    try:
                        batch.append(sub.queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await loop.run_in_executor(None, self._write_batch, batch)
        finally:
            sub.close()

    def _write_batch(self, batch: list[dict]) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as lf, \
                 open(self.ndjson_path, "a", encoding="utf-8") as jf:
                for w in batch:
                    lf.write(format_line(w, "text"))
                    jf.write(json.dumps(w, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.lines_written += len(batch)
            self._maybe_rotate()
        except OSError:
            pass  # never let disk trouble kill capture

    def _maybe_rotate(self) -> None:
        try:
            if self.log_path.stat().st_size < ROTATE_BYTES:
                return
        except OSError:
            return
        self._rotations += 1
        for p in (self.log_path, self.ndjson_path):
            with contextlib.suppress(OSError):
                p.rename(p.with_suffix(p.suffix + f".{self._rotations}"))

    # -- helpers for the API ----------------------------------------
    def info(self) -> dict:
        def size(p: Path) -> int:
            try:
                return p.stat().st_size
            except OSError:
                return 0
        return {
            "log_path": str(self.log_path),
            "ndjson_path": str(self.ndjson_path),
            "log_bytes": size(self.log_path),
            "ndjson_bytes": size(self.ndjson_path),
            "lines_written": self.lines_written,
            "rotations": self._rotations,
            "dir": str(self.dir),
        }

    def open_in_os(self, which: str = "log") -> tuple[bool, str]:
        """Open the file (``which='log'|'ndjson'``) with the OS default handler,
        or reveal it in the file manager (``which='reveal'|'dir'``).
        Loopback-only — the caller MUST gate this to local requests."""
        if os.environ.get("IDEVICETAIL_NO_OS_OPEN") == "1":
            return False, "disabled"
        is_reveal = which in ("reveal", "dir")
        target = self.ndjson_path if which == "ndjson" else self.log_path
        try:
            if sys.platform == "win32":
                if is_reveal:
                    subprocess.Popen(f'explorer /select,"{target}"')
                else:
                    os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)] if is_reveal else ["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target.parent if is_reveal else target)])
            return True, str(target)
        except Exception as e:  # noqa: BLE001
            return False, f"{e}"
