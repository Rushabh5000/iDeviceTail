# Test plan

## Automated (desktop) — `pytest desktop/tests`

| File | Covers |
|---|---|
| `test_normalize.py` | syslog line variants (asl + ISO timestamps), `[subsystem:category]` label extraction, blank → None, **garbage line is kept not dropped**, agent payload (ms‑timestamp normalisation, bad ts → now, file/line/thread annotation), crash `.ips` JSON header + legacy `.crash`. |
| `test_framing.py` | length‑prefixed hello+log+batch; **split across packets** (1‑byte writes); NDJSON mode + ping/pong; **oversized frame rejected without killing the listener**; fresh connection still works afterwards. |
| `test_bus_and_store.py` | ring buffer eviction + monotonic `seq`; **backpressure drops oldest for a slow subscriber** and counts it; SQLite round‑trip + level filter + text search; malformed batch doesn't raise. |
| `test_manager_and_devicelist.py` | `pymobiledevice3 usbmux list` JSON **and** table‑fallback parsing + dedupe by UDID; Bonjour sighting merges into a paired device **by IP**; `start_capture` rejects an agent‑only device. |
| `test_server_smoke.py` | app builds; `/api/state`; **WS snapshot → live**: a published record arrives on the browser socket; unknown‑device start → 400; `/api/export?format=ndjson`. |

Run: `pip install -e ".[dev]" && pytest -q`  (28 tests, ~10 s, no device needed).

## Automated (iOS) — `swift test` in `ios/IDeviceTailKit`

| File | Covers |
|---|---|
| `FrameCodecTests.swift` | encode is length‑prefixed & round‑trips; `drainObjects` handles a split header + multiple frames in one buffer; oversized length is discarded (no infinite loop). |
| `LogRecordTests.swift` | `hello` encodes the key as `protocol` (not `protocolVersion`); full `LogRecord` JSON round‑trip; `OSLogEntryLog.Level` → string mapping. |

## Manual matrix

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| M1 | Discovery | `serve`; put a provisioned device on Wi‑Fi | device row appears ≤ 10 s with UDID + iOS version |
| M2 | Engine A connect (syslog) | click **Start syslog** | lines appear ≤ 3 s; timestamp/process/level/message columns populated |
| M3 | Engine A firehose | **os_trace** on iOS ≤ 17.3 (or macOS / USB tunnel on 17.4+) | subsystem/category populated for `os_log` entries |
| M4 | Engine B | run agent app, **Start streaming**, **Emit test logs** | 4 records show `source=agent`, correct levels, `file:line` in message |
| M5 | Reconnect — Wi‑Fi blip | disable desktop Wi‑Fi 20 s, re‑enable | device → `connecting` → `streaming`; no crash; stream resumes |
| M6 | Reconnect — device reboot | reboot the iPhone | capture task survives; resumes after unlock (Engine A may need Dev‑Mode reconfirm) |
| M7 | Reconnect — agent app backgrounded/killed | swipe‑kill the agent, relaunch | new `hello`, device flips offline→streaming; `droppedCount` sane |
| M8 | Computer sleep/wake | sleep the PC 2 min | on wake, discovery re‑runs, captures resume |
| M9 | Malformed data | `printf '\x00\x00\x01\x00' | nc <pc> 45455` ; also send non‑JSON NDJSON line | listener stays up; other clients unaffected |
| M10 | High volume | device under load (e.g. `os_log` in a tight loop) or agent burst 10k lines | UI stays responsive (virtualized); "N dropped" may show; SQLite row count matches published within a small delta |
| M11 | Multiple devices | 1 provisioned iPhone (Engine A) + 1 agent app + 1 more device | 3 rows, independent Start/Stop, interleaved stream, per‑device filter works |
| M12 | Long run | leave M11 running 8 h | memory flat (ring cap), DB grows linearly, no descriptor leak (`handle`/`lsof`) |
| M13 | Crash pull | crash the agent app (force unwrap nil) → **Crashes** on its Engine‑A row (if paired) or `crash-pull` CLI | `.ips` pulled; a `source=crash` record with process + reason appears |
| M14 | Export | apply filters, **Export** | downloaded `.log` matches the visible rows; `--format csv/ndjson` via CLI parse‑check |
| M15 | Bind policy | `serve --bind-policy lan`; connect agent from a non‑private IP (VPN) | connection refused, logged; `--bind-policy any` allows it |
| M16 | Firewall cold start | fresh Windows, no rule | `doctor` prints the rule; after adding it, M1–M4 pass |

## Regression checklist before a release

- [ ] `pytest -q` green on Windows + macOS + Linux (py3.10 and current)
- [ ] `swift test` green (if a Mac is available)
- [ ] `python -m idevicetail doctor` clean
- [ ] M1, M2, M4, M5, M10, M11 pass on real hardware
- [ ] `pymobiledevice3` pinned range still valid (`syslog live` output format unchanged — see `normalize.parse_syslog_line`)
