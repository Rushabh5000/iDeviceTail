# iDeviceTail — Setup & Run Guide

A single walkthrough for getting logs off your own iPhone/iPad **over Wi‑Fi**,
covering both modules:

* **Desktop module** — the Python app you run on your computer (discovery, log
  engines, storage, and the web UI on `http://localhost:3017`).
* **Phone module** — what runs on the device. There are two independent options;
  you can use either or both.

> Before anything else, skim [`FEASIBILITY.md`](FEASIBILITY.md). Short version:
> iOS never gives a third‑party app the system‑wide log firehose. This tool uses
> the two things that *are* possible and merges them.

---

## Contents

1. [How it fits together](#1-how-it-fits-together)
2. [Part A — Desktop module: install & run](#2-part-a--desktop-module-install--run)
3. [Part B — Phone module 1: prepare the device for system logs (Engine A)](#3-part-b--phone-module-1-prepare-the-device-for-system-logs-engine-a)
4. [Part C — Phone module 2: the agent app (Engine B)](#4-part-c--phone-module-2-the-agent-app-engine-b)
5. [Part D — Everyday use](#5-part-d--everyday-use)
6. [Part E — Where files live](#6-part-e--where-files-live)
7. [Part F — CLI reference (optional)](#7-part-f--cli-reference-optional)
8. [Part G — Troubleshooting](#8-part-g--troubleshooting)
9. [Part H — Compatibility quick reference](#9-part-h--compatibility-quick-reference)
10. [Appendix — ports, config, security](#10-appendix--ports-config-security)

---

## 1. How it fits together

```
        ┌───────────────────────── your computer ─────────────────────────┐
        │  idevicetail (one Python process)                                 │
        │                                                                 │
        │   Engine A ── pymobiledevice3 ──►  full system logs, crash,      │
        │     (needs USB pairing once,       sysdiagnose  (all processes)  │
        │      then Wi-Fi)                                                 │
        │                                                                 │
        │   Engine B ── TCP :45455 ◄──────   your app's OWN logs, from     │
        │     (no cable/pair/devmode)        the IDeviceTailAgent app / SDK  │
        │                                                                 │
        │        both feed → one live viewer at  http://localhost:3017     │
        │        + a rolling real-time file on disk (.log + .ndjson)       │
        └─────────────────────────────────────────────────────────────────┘
                         ▲ Wi-Fi (same network)
                 ┌───────┴────────┐
                 │  iPhone / iPad │
                 └────────────────┘
```

| You want… | Use | Cable | On‑device setup |
|---|---|---|---|
| Logs from **the whole device** (SpringBoard, kernel, locationd, other apps…) | **Engine A** | **USB once** to pair, then wireless forever | Trust + Wi‑Fi sync + Developer Mode (all via the web UI's **Setup** button) |
| Logs from **one app you build/own** | **Engine B** (agent app or the SDK) | none¹ | install the app, tap **Start** |

¹ No cable to *run*. On Windows you need USB once to *sideload* the agent app;
on macOS you can install it wirelessly from Xcode or TestFlight.

---

## 2. Part A — Desktop module: install & run

### 2.1 Prerequisites

* **Python 3.10–3.13** on `PATH` (`python --version`). **Avoid 3.14 for Engine A**:
  two of `pymobiledevice3`'s dependencies (`lzfse`, `pylzss`) are C extensions
  with no 3.14 wheels yet, so they need a C compiler to install. If your default
  `python` is 3.14, install 3.12 or 3.13 and build the venv with it:
  ```
  py install 3.12            # Windows "Python Install Manager"; or download from python.org
  py -3.12 -m venv .venv
  .venv\Scripts\python -m pip install -e ".[device]"
  ```
  `start.bat` and `scripts/setup-desktop.ps1` do this pick automatically.
  The app‑log **agent (Engine B)** works fine on any Python 3.10+.
* For **Engine A** (full system logs), the OS USB stack — needed even though
  capture is wireless, because pairing happens once over USB:
  * **Windows:** install **Apple Devices** (Microsoft Store) *or* iTunes from
    apple.com. This provides the Apple Mobile Device driver + Bonjour.
  * **Linux:** `sudo apt install -y usbmuxd libimobiledevice6 libimobiledevice-utils`
  * **macOS:** nothing extra.
* A modern browser.
* Computer and device on the **same Wi‑Fi network** (no "AP/client isolation" —
  see Troubleshooting).

### 2.2 Install & launch

#### Windows — the one‑double‑click way

1. Open `D:\AIProjects\iDeviceTail`.
2. Double‑click **`start.bat`**.
   * First run only: it creates `.venv\`, upgrades pip, and runs
     `pip install -e ".[device]"` (this pulls in `pymobiledevice3` for Engine A —
     ~1–2 min).
   * Then it starts the server and your browser opens
     `http://localhost:3017` automatically.
3. Leave the console window open (that's the server). Double‑click **`stop.bat`**
   to stop it.

#### macOS / Linux (or manual on Windows)

```bash
cd /path/to/iDeviceTail
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[device]"           # or  pip install -e .   for Engine B only (no pymobiledevice3)
python -m idevicetail doctor           # sanity check (Python, deps, pymobiledevice3, ports, firewall)
python -m idevicetail serve            # starts everything and opens the browser
```

### 2.3 Windows firewall (once)

If the device or the agent app can't reach the computer, allow the ports
(run in an **elevated** PowerShell/CMD):

```powershell
netsh advfirewall firewall add rule name="idevicetail" dir=in action=allow protocol=TCP localport=3017,45455 profile=private
netsh advfirewall firewall add rule name="idevicetail-mdns" dir=in action=allow protocol=UDP localport=5353 profile=private
```

### 2.4 What you should see

* Browser at `http://localhost:3017`, header shows a green **live** pill.
* Header right shows **Engine A ✓** if `pymobiledevice3` is installed, or
  *"Engine A: install pymobiledevice3"* if not (Engine B still works).
* Left **Devices** panel populates within ~6 s:
  * A **paired iPhone/iPad** appears with its name, model (e.g. *iPhone XR*),
    serial, iOS version, and a **▶ Start** button.
  * An **agent app** device appears once it connects and streams on its own.
  * A device that's only been seen on Wi‑Fi but isn't paired to *this* computer
    shows a **Setup** button and the hint *"provision over USB once → then
    wireless"*.

---

## 3. Part B — Phone module 1: prepare the device for system logs (Engine A)

Do this once per device. **A USB cable is required for these steps only**; after
that the device is used wirelessly.

1. Connect the iPhone/iPad to the computer **by USB** and unlock it.
2. In the web UI, the device row appears (possibly named by its UDID at first).
   Click its **Setup** button.
3. In the Setup dialog, run the three steps **in order**:

   | Step | Button | What happens on the device |
   |---|---|---|
   | 1 | **Pair & Trust** | A *"Trust This Computer?"* prompt — tap **Trust**, enter passcode. Re‑click if it timed out. |
   | 2 | **Enable Wi‑Fi sync** | Turns on lockdown‑over‑Wi‑Fi ("Connect via network"). No visible prompt. |
   | 3 | **Enable Developer Mode** | The device **reboots**. After unlock, go to *Settings ▸ Privacy & Security ▸ Developer Mode*, turn it **On**, confirm — the device reboots again. |

   The dialog shows the command output for each step.
4. **Unplug the cable.** Within ~6 s the device re‑appears in the list over
   Wi‑Fi, now with its real name, serial, and iOS build. It's found via Bonjour
   (`pymobiledevice3 bonjour mobdev2`), so it works even if Apple's Mobile
   Device Service isn't bridging network devices.

> ⚠️ **Keep the iPhone awake and on Wi‑Fi while collecting.** A **locked** iPhone
> leaves Wi‑Fi after ~30 s — it then vanishes from the list and streaming pauses
> (it auto‑resumes when the phone is reachable again). To keep it up:
> * *Settings ▸ Display & Brightness ▸ Auto‑Lock ▸ Never* while you work, **and/or**
> * keep it **plugged into a charger** (any USB power, not necessarily this PC), **and/or**
> * set up **"Sync over Wi‑Fi"** once in the Apple Devices app / Finder — iOS then
>   holds a persistent link open while locked, on the same network.
> If the device card shows *"not reachable now"*, that's this — wake the phone.

Notes:

* If *Developer Mode* is missing from Settings entirely, that's expected until
  step 3 (or any development profile) runs — that's what makes it appear.
* `syslog` mode works on Wi‑Fi on every supported iOS. The richer **`os_trace`**
  firehose on **iOS 17.4+** may need a one‑off USB session on Windows/Linux (a
  Mac does it over Wi‑Fi) — see [`COMPATIBILITY.md`](COMPATIBILITY.md).
  *(On iOS ≤ 18.x, plain **▶ Start** already gives the full labelled firehose over
  Wi‑Fi — verified on iOS 18.7.9.)*
* No jailbreak, no Apple Configurator, no MDM.

**CLI equivalent** (only if you prefer a terminal — the Setup buttons call
exactly this):

```bash
python -m idevicetail devices                 # copy the UDID
python -m idevicetail pair <UDID>
python -m idevicetail wifi-sync <UDID>
python -m idevicetail devmode <UDID>
```

or run `scripts/provision-device.ps1` (Windows).

---

## 4. Part C — Phone module 2: the agent app (Engine B)

Use this to stream **an app's own logs** (the `IDeviceTailAgent` demo app, or any
app you build that links `IDeviceTailKit`). No pairing, no Developer Mode, no cable
to run.

> **Scope:** `OSLogStore` on iOS only allows `scope: .currentProcessIdentifier`,
> so the agent streams **its own process's** `os_log`/`Logger` output plus
> anything you pass to `LogForwarder.shared.log(...)`. It cannot read other apps
> or the system — that's Engine A's job.

### 4.1 Build the agent app (needs a Mac + Xcode)

```bash
brew install xcodegen
cd ios/IDeviceTailAgent
xcodegen generate            # creates IDeviceTailAgent.xcodeproj
open IDeviceTailAgent.xcodeproj
```

In Xcode: select the target → **Signing & Capabilities** → set your **Team**
(any free Apple ID works; the build then needs re‑signing every 7 days).

### 4.2 Install it on the device

Pick whichever applies:

| Situation | How |
|---|---|
| Mac + Xcode | Select your device in Xcode and **Run**. First install can be over USB; after that, tick *"Connect via network"* and installs are wireless. |
| Windows / no Mac | Build an `.ipa` on a Mac or CI, then install with **AltStore** or **Sideloadly** (device on USB once; AltStore then refreshes over Wi‑Fi). This also makes the Developer Mode toggle appear. |
| Any | **TestFlight** — upload once, install and update with no cable ever. |

### 4.3 First run

1. Put the phone on the **same Wi‑Fi** as the computer.
2. Launch **iDeviceTail Agent**. iOS asks for **Local Network** permission →
   **Allow** (required for Bonjour/`NWConnection`; if you miss it, re‑enable in
   *Settings ▸ Privacy & Security ▸ Local Network*).
3. Leave mode on **Auto (Bonjour)** and tap **Start streaming**. It finds the
   desktop automatically. If your Wi‑Fi blocks discovery, switch to **Manual
   host** and type the computer's IP + port `45455`.
4. In the desktop UI the device appears as an **agent** device and logs stream
   immediately. Tap **Emit test logs** in the app to confirm.

### 4.4 Add logging to your own app (optional)

Add this repo as a Swift Package dependency, product **`IDeviceTailKit`**, then:

```swift
import IDeviceTailKit

// App start (e.g. in App.init or didFinishLaunching):
LogForwarder.shared.start()                       // auto-discover the desktop
// or pin it:
// LogForwarder.shared.start(host: "192.168.1.50", port: 45455)

// anywhere in your code:
LogForwarder.shared.log(.error, "checkout failed", subsystem: "shop", category: "pay")
```

Add to your app's **Info.plist**:

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Streams this app's logs to the iDeviceTail desktop on your local network.</string>
<key>NSBonjourServices</key>
<array>
  <string>_idevtail-host._tcp</string>
  <string>_idevtail._tcp</string>
</array>
```

Everything the agent needs (framed TCP, reconnect with backoff, a bounded
drop‑oldest buffer, `os_log` capture) is inside `IDeviceTailKit` — nothing else to
wire up.

---

## 5. Part D — Everyday use

After the one‑time setup above, normal operation is **all in the browser**:

1. Start the desktop app (Windows: `start.bat`; else `python -m idevicetail serve`).
   The browser opens to `http://localhost:3017`.
2. The device appears in **Devices** on the left.
3. **For a paired device (Engine A):** click the card, then:
   * **▶ Start** — classic `syslog` stream (widest wireless compatibility), or
   * **os_trace** — the structured firehose (subsystem/category/level; may need a
     USB session on iOS 17.4+ from Windows/Linux),
   * **■ Stop** to end it. The toolbar **▶ Start / ■ Stop** also act on the
     currently selected card.
   **For an agent device (Engine B):** nothing to click — it streams on connect.
4. Read / filter the stream:

   | Control | Effect |
   |---|---|
   | **Keyword filter** + **OK** (`regex` toggle) | text match on message / process / subsystem |
   | **level ▾** | minimum severity (`debug+` … `fault`) |
   | **process…** / **subsystem…** | substring filters (autocomplete from what's been seen) |
   | **source ▾** | `device-syslog` / `device-oslog` / `agent` / `crash` / `sysdiagnose` |
   | **Pause** | freeze the view (incoming lines are buffered, not lost) |
   | **follow** | auto‑scroll to newest (auto‑turns off if you scroll up) |
   | **🗑 Clear** | empties the on‑screen view only — files on disk are untouched |
   | click a row | full detail + raw line in a side panel |

5. Get the logs out:

   | Button | Result |
   |---|---|
   | **⬇ Export view** | downloads the **currently filtered** lines as a `.log` |
   | **📂 Open file ▾** → **Open in editor** | opens the always‑on real‑time `.log` in your default editor (desktop machine only) |
   | **📂 Open file ▾** → **Reveal in folder** | opens the file manager at that file |
   | **📂 Open file ▾** → **Download .log / .ndjson** | download the full rolling file (human‑readable / loss‑less) |

6. Extra device actions (paired devices): **Crashes** pulls crash reports off the
   device; **Sysdiag** triggers a sysdiagnose archive (several minutes — keep the
   device unlocked).

### Multiple devices

Every device runs its own capture; Start/Stop and filters are per‑device. Use the
**source** filter or click a card to focus the view on one device.

### Reconnects — nothing to do

Wi‑Fi blips, device reboots, the agent app being killed, or the computer
sleeping are all handled: Engine A respawns its capture with backoff, the agent
reconnects itself, and discovery re‑runs on wake.

---

## 6. Part E — Where files live

Default data directory: **`<repo>/data/`** (override with `--data-dir` or
`IDEVICETAIL_DATA_DIR`). It is git‑ignored; delete it any time.

```
data/
├── idevicetail.sqlite            optional history DB (disable with serve --no-store)
├── sessions/
│   ├── realtime-YYYYMMDD-HHMMSS.log      human-readable, appended live  ← "Open file"
│   └── realtime-YYYYMMDD-HHMMSS.ndjson   one JSON object per line, loss-less
├── crashes/<UDID>/...          crash reports pulled via the Crashes button
└── sysdiagnose/<UDID>/...      sysdiagnose archives
```

A new `realtime-*` pair is started each time you launch `serve`; it rotates at
64 MB (`.1`, `.2`, …).

---

## 7. Part F — CLI reference (optional)

You never need this for normal use, but it's there for scripting:

```bash
python -m idevicetail doctor                       # environment / port / firewall check
python -m idevicetail discover [--secs N]          # list devices via Bonjour + pymobiledevice3
python -m idevicetail devices                      # pymobiledevice3 device list (UDIDs)

python -m idevicetail stream <UDID>                # live syslog to your terminal (wireless)
python -m idevicetail stream <UDID> --oslog --tunnel      # structured firehose via user-space tunnel
python -m idevicetail stream <UDID> --rsd <HOST> <PORT>   # use an existing RSD tunnel (advanced)
python -m idevicetail stream <UDID> --process locationd   # filter at the source

python -m idevicetail crash-pull <UDID> ./data/crashes [--erase]
python -m idevicetail sysdiagnose <UDID> [./data/sysdiagnose]

python -m idevicetail pair <UDID>                  # one-time provisioning (USB)
python -m idevicetail wifi-sync <UDID> [--off]
python -m idevicetail devmode <UDID>

python -m idevicetail export --format ndjson|csv|text [-o FILE] [--device-id ID] [--level warning] [-q text]

python -m idevicetail serve [--host H] [--port 3017] [--agent-port 45455] \
                          [--data-dir DIR] [--no-store] [--no-apple-discovery] \
                          [--bind-policy lan|any|local] [--no-open]
```

---

## 8. Part G — Troubleshooting

| Symptom | Fix |
|---|---|
| **Device never appears** | Same Wi‑Fi? Disable "AP isolation"/guest network. For Engine A run `python -m idevicetail devices` — empty means not paired / Wi‑Fi‑sync off / device locked; redo **Setup** over USB. For the agent, use **Manual host** with the computer's IP. |
| **Header says "Engine A: install pymobiledevice3"** | `pip install "pymobiledevice3>=4.14"` in the project's `.venv` (or run `start.bat`, which does it). Engine B works without it. |
| **`pair` says "accept the trust dialog"** | Unlock the device, tap **Trust**, enter passcode, click **Pair & Trust** again. Close Finder/iTunes/Xcode windows that hold the lockdown session. |
| **Developer Mode toggle missing** | Run **Setup ▸ Enable Developer Mode** (or install any dev‑signed app). It appears after that. |
| **`os_trace` stream exits immediately (iOS 17.4+, Windows/Linux)** | Expected over Wi‑Fi. Use **▶ Start** (`syslog`) wirelessly, or connect USB for that session and use `os_trace`, or use a Mac. |
| **Agent app stuck "waiting / reconnecting"** | Port `45455` firewalled or Wi‑Fi isolates clients. Add the firewall rule (§2.3); test `nc <computer-ip> 45455`. |
| **Agent never asked for Local Network permission** | Delete + reinstall the app, or *Settings ▸ General ▸ Transfer or Reset ▸ Reset ▸ Location & Privacy*. |
| **"Untrusted Developer" on the app** | *Settings ▸ General ▸ VPN & Device Management* → trust your certificate. Free Apple ID builds expire after 7 days — re‑deploy. |
| **"N dropped (slow tab)"** in the status bar | Your browser tab couldn't keep up; capture and the on‑disk file are unaffected. Add filters or **Pause** while reading. |
| **Open file / Reveal does nothing** | Those work only in the browser **on the computer running the server**. From another machine, use **Download .log / .ndjson** instead. |
| **iOS < 15** | The agent app won't run; Engine A `syslog` still works. |

More detail: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## 9. Part H — Compatibility quick reference

| Capability | iOS 15–16 | iOS 17.0–17.3 | iOS 17.4 – 18 / 26 | Cable once? | Dev Mode? |
|---|---|---|---|---|---|
| Discovery | ✅ Wi‑Fi | ✅ | ✅ | pairing needs it | no |
| System logs — `syslog` mode | ✅ Wi‑Fi | ✅ Wi‑Fi | ✅ Wi‑Fi | yes | 16+: recommended |
| System logs — `os_trace` firehose | ✅ Wi‑Fi | ✅ Wi‑Fi (no‑root tunnel) | ⚠️ Wi‑Fi = macOS only; Win/Linux = USB session | yes | yes (16+) |
| Crash reports / sysdiagnose | ✅ Wi‑Fi | ✅ | ✅ | yes | no |
| App's own logs (agent) | ✅ | ✅ | ✅ | **no** (USB once to sideload on Windows) | **no** |

```
Host OS:   macOS = Full   ·   Windows = Partial   ·   Linux = Partial
(Partial = full for iOS ≤ 17.3 and for syslog/crash/sysdiagnose on all versions;
 the os_trace firehose on iOS 17.4+ needs a USB session or a Mac.)
```

Full matrix: [`COMPATIBILITY.md`](COMPATIBILITY.md).

---

## 10. Appendix — ports, config, security

### Ports

| Port | Purpose | Change with |
|---|---|---|
| **3017** | Web UI + REST + WebSocket (this is the address you open) | `serve --port` |
| **45455** | Framed TCP the agent app connects to | `serve --agent-port` |
| 5353/udp | mDNS/Bonjour discovery | (system) |
| 3016 | reserved placeholder in the project port registry; unused by the process | — |

### Environment variables (all optional; CLI flags win)

`IDEVICETAIL_HOST`, `IDEVICETAIL_PORT`, `IDEVICETAIL_AGENT_PORT`, `IDEVICETAIL_DATA_DIR`,
`IDEVICETAIL_STORE=0`, `IDEVICETAIL_BIND_POLICY=lan|any|local`,
`IDEVICETAIL_DISCOVER_APPLE=0`, `IDEVICETAIL_PYMD=<path to pymobiledevice3>`,
`IDEVICETAIL_RING_SIZE=<n>`, `IDEVICETAIL_NO_OS_OPEN=1` (disable the Open‑file OS handler).

### Security

* **No application login by design** — this is a personal LAN tool.
* **Apple's trust model is untouched:** pairing, Trust, Developer Mode and the
  tunnel are used as Apple intends; Engine A cannot reach a device this computer
  isn't paired with.
* The agent listener refuses connections from non‑private / non‑loopback source
  IPs unless started with `--bind-policy any`.
* `serve --host 127.0.0.1` keeps the web UI off the network entirely.
* No cloud, no outbound calls, no telemetry. All data stays in `data/`.
* **Open file / Reveal** endpoints only act on requests coming from the machine
  running the server.

### Stopping / cleaning up

* Stop the server: `Ctrl‑C` in its console (Windows: `stop.bat`).
* Wipe all captured data: delete the `data/` folder.
* Remove a device pairing: `python -m idevicetail` … or
  `pymobiledevice3 lockdown unpair --udid <UDID>`.
