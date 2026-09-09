# iDeviceTail — wireless iOS/iPadOS log collection

[![CI](https://github.com/Rushabh5000/idevicetail/actions/workflows/ci.yml/badge.svg)](https://github.com/Rushabh5000/idevicetail/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/idevicetail.svg)](https://pypi.org/project/idevicetail/)
[![Python](https://img.shields.io/pypi/pyversions/idevicetail.svg)](https://pypi.org/project/idevicetail/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Collect and view logs from **your own** iPhones/iPads over Wi‑Fi, with **no cable during normal
operation**. One desktop app (Python, cross‑platform) fuses two capture engines into a single
live viewer:

| Engine | What it gets | Cable ever? | iOS setup | Works on |
|--------|--------------|-------------|-----------|----------|
| **A — Device engine** (`pymobiledevice3`) | Real **system logs** (all processes), crash reports, sysdiagnose | **One‑time USB pair + Trust**, then wireless | Enable Wi‑Fi sync + Developer Mode | macOS = full · Windows/Linux = full for iOS ≤ 17.3, partial (syslog only) for iOS 17.4+ over Wi‑Fi |
| **B — Agent engine** (Swift app + SDK) | The **agent app's own logs** + logs of any app that links `IDeviceTailKit` | **Never** (cable only to sideload the app once on Windows) | Install the app, tap Start | Any stock iOS, no pairing, no Developer Mode |

> **Read [`docs/FEASIBILITY.md`](docs/FEASIBILITY.md) first.** iOS does **not** give third‑party
> software the system‑log firehose. Engine A uses Apple's *supported* device‑debug channel
> (lockdown `syslog_relay` / `os_trace_relay`, the same one Console.app and `log stream` use) and
> therefore inherits Apple's pairing + Developer Mode requirements. Engine B is 100% App‑sandbox
> legal but only sees its own process. The tool ships both and merges them.

> 📖 **Full step‑by‑step setup + run guide (phone *and* desktop): [`docs/GUIDE.md`](docs/GUIDE.md).**

## Quick start

**Windows:** double‑click **`start.bat`**. First run makes a venv, installs, launches the
server and opens the web UI. That's the only "command".

**macOS/Linux (or manual):**
```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[device]"                        # from a checkout
#   or:  pip install "idevicetail[device]"        # from PyPI
python -m idevicetail serve                        # opens http://localhost:3017 in your browser
```

> **Use Python 3.10–3.13** (not 3.14 yet — `pymobiledevice3`'s C‑ext deps have no 3.14 wheels).
> The app‑log agent (Engine B) works on any 3.10+.

**Everything else is buttons in the browser** — no more commands:

| In the UI | Does |
|---|---|
| device card → **▶ Start** / **os_trace** | begin/stop a wireless system‑log stream (Engine A) |
| toolbar **Keyword filter + OK**, level/process/subsystem/source pickers | live filtering of a huge stream |
| **⬇ Export view** | save the currently filtered lines |
| **📂 Open file** | open / reveal / download the always‑on "real‑time file" (`data/sessions/realtime-*.log` + `.ndjson`) |
| **🗑 Clear** / **Pause** / **follow** | manage the view |
| device card → **Setup** | one‑time USB provisioning (Pair · Wi‑Fi sync · Developer Mode) without touching a terminal |
| device card → **Crashes** / **Sysdiag** | pull crash reports / trigger a sysdiagnose |

A CLI still exists for scripting (`idevicetail discover | stream | crash-pull | export …`) but you never need it for normal use.

## Repository layout

```
iDeviceTail/
├── docs/            Feasibility report, architecture, protocol, compatibility, setup, troubleshooting, test plan
├── desktop/
│   ├── idevicetail/   Python package: discovery, engines, normalizer, bus, store, aiohttp server, CLI
│   │   └── web/     Static log‑viewer UI (no build step)
│   └── tests/       pytest: parsing, framing, dedup, store
├── ios/
│   ├── IDeviceTailKit/     Swift Package: OSLogStore reader, Bonjour, framed TCP client, reconnect
│   └── IDeviceTailAgent/   SwiftUI app that hosts the SDK (XcodeGen project)
├── scripts/         setup / run / pairing helpers (PowerShell + sh)
├── start.bat / stop.bat / hub.json   project-hub integration
└── pyproject.toml
```

## Platform support

```
macOS:    Full      (Engine A wireless incl. iOS 17.4+ firehose via remoted; Engine B; wireless app install)
Windows:  Partial   (Engine A: full ≤ iOS 17.3 over Wi-Fi, syslog-only for 17.4+ over Wi-Fi; Engine B full)
Linux:    Partial   (same as Windows)
```

See [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) for the capability matrix.

## License

MIT (this project). Engine A shells out to **pymobiledevice3** (GPL‑3.0) as a separate process;
it is an optional dependency you install yourself.
