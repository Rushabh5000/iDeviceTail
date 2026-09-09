import asyncio
import time

import pytest

from idevicetail.bus import LogBus
from idevicetail.models import Level, LogRecord
from idevicetail.store import Store


def _rec(msg="m", lvl=Level.info):
    return LogRecord(ts=time.time(), device_id="d1", device_name="iPhone",
                     source="agent", message=msg, level=lvl)


def test_bus_ring_and_seq():
    bus = LogBus(ring_size=3)
    for i in range(5):
        bus.publish(_rec(f"m{i}"))
    snap = bus.snapshot()
    assert [s["message"] for s in snap] == ["m2", "m3", "m4"]
    assert snap[-1]["seq"] == 5


def test_bus_backpressure_drops_oldest_for_slow_sub():
    bus = LogBus()
    sub = bus.subscribe(maxsize=2)
    for i in range(5):
        bus.publish(_rec(f"m{i}"))
    # queue holds the 2 newest; 3 were dropped
    assert sub.dropped == 3
    got = [sub.queue.get_nowait()["message"] for _ in range(2)]
    assert got == ["m3", "m4"]


@pytest.mark.asyncio
async def test_store_roundtrip(tmp_path):
    bus = LogBus()
    st = Store(tmp_path / "t.sqlite", flush_rows=10, flush_seconds=0.1)
    await st.open()
    st.run(bus)
    for i in range(25):
        bus.publish(_rec(f"line {i}", Level.error if i % 5 == 0 else Level.info))
    await asyncio.sleep(0.4)
    rows = await st.query(limit=100)
    assert len(rows) == 25
    errs = await st.query(level_min=Level.error.rank, limit=100)
    assert len(errs) == 5
    hit = await st.query(text="line 7", limit=100)
    assert hit and hit[0]["message"] == "line 7"
    await st.close()


@pytest.mark.asyncio
async def test_store_survives_bad_batch(tmp_path):
    st = Store(tmp_path / "t2.sqlite")
    await st.open()
    st._pending.append(("only", "three", "cols"))  # malformed row
    await st._flush()  # must not raise
    await st.close()
