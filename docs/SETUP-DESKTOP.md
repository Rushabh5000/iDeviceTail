# Desktop setup

## 1. Prerequisites

* **Python 3.10+** on `PATH`.
* For **Engine A** (real system logs): `pymobiledevice3`, plus the OS USB stack:
  * **Windows:** install **Apple Devices** (Microsoft Store) *or* iTunes from
    apple.com — this provides the Apple Mobile Device USB driver and Bonjour.
  * **Linux:** `usbmuxd` + `libimobiledevice` packages (`sudo apt install usbmuxd`).
  * **macOS:** nothing extra (ships with the device stack).
* A browser for the UI.

## 2. Install

```bash
git clone <this repo> && cd iDeviceTail
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[device]"     # or  pip install -e .   to skip Engine A
python -m idevicetail doctor
```

`doctor` verifies Python, the three core deps, `pymobiledevice3`, that ports
3017 / 45455 are free, and prints the Windows firewall command.

## 3. Run

```bash
python -m idevicetail serve
```

* Web UI + REST + `/ws`:  `http://localhost:3017`
* Agent ingest (iOS → desktop):  TCP `0.0.0.0:45455`
* mDNS: browses `_apple-mobdev2._tcp` / `_idevtail._tcp`, publishes
  `_idevtail-host._tcp`.

Useful flags:

| Flag | Effect |
|---|---|
| `--host 127.0.0.1` | keep the UI off the LAN (agents can still reach `--agent-port`) |
| `--port 3017` / `--agent-port 45455` | change ports |
| `--no-store` | pure in‑memory, no SQLite |
| `--data-dir PATH` | where the DB / crashes / exports go (default `./data`) |
| `--bind-policy lan\|any\|local` | who may connect to the agent listener (default `lan` = private IPs only) |
| `--no-apple-discovery` | don't browse for `_apple-mobdev2` (agent‑only setups) |

Windows firewall (first run, once):

```powershell
netsh advfirewall firewall add rule name="idevicetail" dir=in action=allow protocol=TCP localport=3017,45455 profile=private
```

## 4. One‑time device provisioning (Engine A only) — from the web UI

Connect the iPhone/iPad **by USB once**, unlock it, open `http://localhost:3017`,
click the device's **Setup** button, and run the three steps in order:

1. **Pair & Trust** → tap *Trust* + passcode on the device
2. **Enable Wi‑Fi sync** ("Connect via network")
3. **Enable Developer Mode** → device reboots → confirm under
   *Settings ▸ Privacy & Security ▸ Developer Mode*

Unplug the cable. The device now appears over Wi‑Fi within ~6 s.

*(CLI equivalents, if you prefer: `idevicetail devices | pair <UDID> | wifi-sync
<UDID> | devmode <UDID>`, or `scripts/provision-device.ps1`. The Setup buttons
call exactly these.)*

## 5. Start collecting — all in the browser

* Click a device → **▶ Start** (classic `syslog`, widest wireless compat) or
  **os_trace** (structured firehose). Agent devices stream by themselves.
* **Keyword filter + OK**, plus level / process / subsystem / source pickers —
  live filtering; **Pause** / **follow** / **🗑 Clear** manage the view.
* **⬇ Export view** saves the filtered lines. **📂 Open file** opens / reveals /
  downloads the always‑on real‑time file written to
  `data/sessions/realtime-<timestamp>.log` (human) and `.ndjson` (loss‑less).
* **Crashes** pulls crash reports; **Sysdiag** triggers a sysdiagnose
  (minutes — keep the device unlocked).

Scripting hooks remain available (`idevicetail stream/crash-pull/sysdiagnose/export`)
but are not needed for normal use.

## 6. project-hub

`start.bat` / `stop.bat` clear ports 3016/3017/45455 and launch/stop `serve`.
`hub.json` registers the app (ports 3016 frontend placeholder + 3017 backend,
per `D:\AIProjects\CLAUDE.md`). The single process serves everything on 3017.
