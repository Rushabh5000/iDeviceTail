# Wire protocol — iOS agent ⇄ desktop (Engine B)

Transport: **TCP**. The agent connects *out* to the desktop
(`_idevtail-host._tcp`, default port **45455**). One connection per app
process. The desktop also accepts many connections at once.

## Framing

Two framings are accepted; the desktop auto‑detects from the first byte.

### A. Length‑prefixed (what `IDeviceTailKit` uses)

```
+--------------------+-------------------------------+
| uint32 big-endian  |  N bytes UTF-8 JSON object    |
|   N = body length  |                               |
+--------------------+-------------------------------+
```

* Max body: **8 MiB** (larger → connection dropped).
* A frame is exactly one JSON object.

### B. NDJSON (debug / `nc` / scripts)

One JSON object per `\n`. Detected when the first byte is `{`.

**Why length‑prefixed JSON and not CBOR/protobuf?**
Framing removes all partial‑packet ambiguity; JSON stays inspectable with
`tcpdump`/`nc` during bring‑up; `batch` frames amortise overhead so a burst of
10k lines costs ~25 frames. CBOR would cut ~30–40 % bytes but log traffic from a
single app is rarely the bottleneck, and losing "just read it" debuggability is
not worth it here. If you later need it, swap `FrameCodec` + `engine_agent._frames`
to CBOR — the envelopes below don't change.

## Messages

Every message is a JSON object with a `type`.

### `hello` — first frame, required

```json
{
  "type": "hello",
  "protocol": 1,
  "session": "6f0e…-uuid",
  "device": {
    "id": "identifierForVendor UUID",
    "name": "Jane's iPhone",
    "model": "iPhone16,1",
    "os": "iOS 18.5",
    "app": "com.example.myapp"
  }
}
```

Desktop replies:

```json
{ "type": "welcome", "session": "6f0e…", "server_time": 1757612345.12 }
```

### `log` — one record

```json
{
  "type": "log",
  "ts": 1757612345.123,          // epoch seconds (ms also accepted & normalised)
  "level": "error",               // debug|info|notice|warning|error|fault (default/warn/crit aliases ok)
  "process": "MyApp",
  "pid": 913,
  "subsystem": "com.example.net",
  "category": "http",
  "message": "GET /v1/orders -> 500",
  "thread": 7,                    // optional
  "file": "OrdersClient.swift",   // optional
  "line": 142                     // optional
}
```

Only `ts` and `message` are strictly required; everything else defaults.

### `batch` — many records in one frame (preferred for bursts / backlog)

```json
{ "type": "batch", "logs": [ { …log fields, no "type"… }, … ] }
```

### `ping` / `pong` — keepalive (agent every 15 s)

```json
{ "type": "ping", "t": 1757612345.0 }      →   { "type": "pong", "t": 1757612345.0 }
```

### `bye` — optional graceful close

```json
{ "type": "bye" }
```

## Reliability semantics

* **Ordering:** per‑connection, in order. Across reconnects, `batch` replay of
  the agent's buffer may re‑send the tail; the desktop de‑dupes only by content
  at the UI layer (best‑effort) — treat delivery as *at‑least‑once*.
* **Backpressure:** the agent holds a bounded ring (20k records); on overflow it
  drops **oldest** and reports a running `droppedCount` (shown in the app and,
  for the desktop→browser hop, in the UI status bar).
* **Reconnect:** exponential backoff 1 s→30 s with ±30 % jitter, both directions.
* **Bad frames:** a frame that fails JSON parse is skipped; an oversized length
  prefix drops the connection (agent reconnects).
* **Bind policy:** desktop refuses agent connections from non‑private/non‑loopback
  source IPs unless started with `--bind-policy any`.

## Discovery TXT records

`_idevtail-host._tcp` (desktop): `v=1`, `host=<hostname>`, port = agent port.
`_idevtail._tcp` (agent beacon, optional): `id`, `name`, `model`, `os`, `v`.
