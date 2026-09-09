# Architecture

## Components

```
┌──────────────────────────── desktop (Python, one process) ─────────────────────────────┐
│                                                                                        │
│  discovery.py ── mDNS browse ──►  _apple-mobdev2._tcp     (paired device on Wi-Fi)      │
│                                   _idevtail._tcp         (agent presence beacon)       │
│                 mDNS publish ──►  _idevtail-host._tcp    (so agents find this PC)       │
│                                                                                        │
│  manager.py    device registry (dedupe by UDID) + capture supervision (backoff)        │
│      │                                                                                  │
│      ├── engine_device.py ── child proc ──►  pymobiledevice3 syslog live / crash / …    │
│      │        (Engine A)                     (USB pair once, then Wi-Fi)                 │
│      │                                                                                  │
│      └── engine_agent.py ── TCP :45455 ◄──   IDeviceTailKit  (Engine B, cable-free)       │
│                                                                                        │
│  normalize.py  →  LogRecord  →  bus.py (fan-out + ring buffer, drop-oldest backpressure)│
│                                   │                                                     │
│                     ┌─────────────┼──────────────┐                                      │
│                     ▼             ▼              ▼                                      │
│               store.py       server.py /ws   exporter.py                                │
│              (SQLite, opt)   (browser UI)    (ndjson/csv/text)                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                     ▲  http://<pc>:3017
                                     │
                             browser (web/ UI: virtualized list, filters, search)
```

## Data flow

1. **Discovery.** `zeroconf` browses `_apple-mobdev2._tcp` (Engine-A candidates)
   and `_idevtail._tcp` (agents). A 6 s poll of `pymobiledevice3 usbmux list`
   adds UDID / name / OS / USB‑vs‑Wi‑Fi and merges by IP into Bonjour sightings.
2. **Selection / connect.** UI or CLI asks the manager to start a capture for a
   device id + mode (`syslog` or `oslog`). Agent devices need no action — they
   stream on connect.
3. **Capture.**
   * *Engine A:* `DeviceEngine.stream()` spawns `pymobiledevice3 syslog live
     --udid …`; stdout lines → `parse_syslog_line()` → `LogRecord`.
     Child exit → supervised reconnect (exp backoff + jitter, 1→30 s).
   * *Engine B:* `AgentServer` accepts a framed TCP connection, reads `hello`
     then `log`/`batch` frames → `agent_payload_to_record()` → `LogRecord`.
4. **Bus.** `LogBus.publish()` appends to a bounded ring (default 200k) and
   offers to every subscriber queue; a slow subscriber has its **oldest** queued
   item dropped (tracked as `dropped`). Capture never blocks on a consumer.
5. **Sinks.** (a) each browser WebSocket forwards batched records +
   device/engine events; (b) `Store` batches inserts into SQLite
   (500 rows / 1 s, one transaction); (c) `exporter` serializes a query or the
   ring on demand.

## The unified record

```
LogRecord(
  ts,             # epoch seconds UTC
  device_id,      # UDID (Engine A) or identifierForVendor (Engine B)
  device_name,
  source,         # device-syslog | device-oslog | agent | crash | sysdiagnose
  level,          # debug|info|notice|warning|error|fault  (folded from all sources)
  process, pid,
  subsystem, category,
  message,
  raw,            # original line / original JSON / crash text (truncated)
  session_id, seq # seq is monotonic per desktop run
)
```

## Security boundary

* **Application‑level auth: intentionally none.** This is a personal LAN tool.
* **Apple's trust model: preserved.** Pairing, Trust, Developer Mode and the
  tunnel are used as Apple intends; nothing is bypassed. Engine A cannot talk to
  a device this computer isn't paired with.
* **Network exposure is minimised:**
  * agent listener enforces a **bind policy** (`lan` default): connections from
    non‑private, non‑loopback IPs are refused;
  * `--host 127.0.0.1` keeps the web UI off the network entirely;
  * no cloud, no outbound calls, no telemetry — everything is local;
  * ports are configurable (`--port`, `--agent-port`).
* **Storage** is a local SQLite file under `data/` (git‑ignored). `--no-store`
  keeps everything in memory.

## Platform dependencies

| Piece | Depends on |
|-------|-----------|
| Engine A | `pymobiledevice3` on `PATH` (or `IDEVICETAIL_PYMD`); a USB pairing record; iOS Developer Mode |
| Engine A firehose on iOS 17.4+ over Wi‑Fi | macOS `remoted`, **or** a USB session for the tunnel |
| Discovery of `_apple-mobdev2._tcp` | device has Wi‑Fi sync on; multicast not blocked by AP isolation |
| Engine B | app built with `IDeviceTailKit`; `NSLocalNetworkUsageDescription` + `NSBonjourServices`; user grants Local Network permission |
| Web UI | any modern browser; served by the same process |

## Why these technology choices

* **Python desktop, single process** — `pymobiledevice3` is Python, `zeroconf`
  and `aiohttp`/`aiosqlite` are mature and cross‑platform, and one asyncio loop
  ties discovery + two engines + WS + storage together without IPC.
* **Shell out to `pymobiledevice3`, don't import it** — its CLI is far more
  stable than its internals across releases, process isolation contains hangs,
  and "restart the child" *is* our reconnect strategy.
* **Length‑prefixed JSON frames** for Engine B — framed (clean partial‑packet
  handling), batchable (bursts), still human‑debuggable. See `PROTOCOL.md` for
  the trade‑off vs CBOR/protobuf.
* **Swift + Network.framework + `OSLogStore`** for the agent — the only
  sandbox‑legal way to read the app's own Unified Log, and `NWConnection`/
  `NWBrowser` give reconnect + Bonjour with no third‑party Swift deps.
* **SQLite** as an optional *side* consumer — history/export without putting a
  database in the hot path.
