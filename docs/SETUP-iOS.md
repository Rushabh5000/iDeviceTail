# iOS setup

There are two independent things you can do on the device. You need **A** only
if you want the agent app (app‑own logs, zero cable). You need **B** only for
Engine A (whole‑device system logs).

---

## A. Install the agent app (`IDeviceTailAgent`)

The agent streams **only its own process's** `os_log`/`Logger` output — see
`FEASIBILITY.md §1.1`. Use it to debug an app you build, or as a template for
adding `IDeviceTailKit` to your own app.

### Build

```bash
brew install xcodegen            # or add the .xcodeproj yourself
cd ios/IDeviceTailAgent
xcodegen generate
open IDeviceTailAgent.xcodeproj
```

Set your Team under **Signing & Capabilities** (any free Apple ID works;
the build then needs re‑signing every 7 days).

### Install

* **macOS + Xcode:** select your device (USB once to pair, or "Connect via
  network" after that) → Run. After the first install you can install updates
  **wirelessly**.
* **Windows / no Mac:** build an `.ipa` on a Mac/CI, then use **AltStore** or
  **Sideloadly** (needs the device on USB once; afterwards AltStore refreshes
  over Wi‑Fi). Installing a development profile this way also makes the
  Developer Mode toggle appear.
* **TestFlight:** upload once; installs and updates with no cable ever.

### First run

1. Same Wi‑Fi as the desktop.
2. Launch the app → iOS asks for **Local Network** permission → **Allow**
   (required for Bonjour/`NWConnection`; if you miss it, toggle it back on in
   *Settings ▸ Privacy & Security ▸ Local Network*).
3. Leave mode on **Auto (Bonjour)** → tap **Start streaming**. It finds
   `_idevtail-host._tcp` and connects. If discovery is blocked by your AP,
   switch to **Manual host** and type the desktop's IP + `45455`.
4. The device appears in the desktop UI as an **agent** device and logs stream
   immediately. Tap **Emit test logs** to verify.

### Add `IDeviceTailKit` to your own app

```swift
import IDeviceTailKit

// in App init / didFinishLaunching:
LogForwarder.shared.start()                       // auto-discover
// or: LogForwarder.shared.start(host: "192.168.1.50", port: 45455)

// anywhere:
LogForwarder.shared.log(.error, "checkout failed", subsystem: "shop", category: "pay")
```

Also add to your app's **Info.plist**:

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Streams this app's logs to the iDeviceTail desktop on your LAN.</string>
<key>NSBonjourServices</key>
<array><string>_idevtail-host._tcp</string><string>_idevtail._tcp</string></array>
```

SwiftPM: add this repo, product **IDeviceTailKit**.

---

## B. Prepare the device for Engine A (system logs)

One‑time, **USB required once**:

1. Connect by cable, unlock, tap **Trust This Computer** + passcode.
2. On the desktop:
   ```bash
   python -m idevicetail devices                 # copy the UDID
   python -m idevicetail pair <UDID>
   python -m idevicetail wifi-sync <UDID>        # "Connect via network"
   python -m idevicetail devmode <UDID>          # enables Developer Mode; device reboots
   ```
3. After reboot + unlock: *Settings ▸ Privacy & Security ▸ Developer Mode ▸ On*
   → confirm → device reboots again.
4. Unplug. The device now advertises itself over Wi‑Fi; the desktop discovers it
   within ~6 s and **Start** works with no cable.

### Notes

* If *Developer Mode* is missing from Settings, it means step 2's `devmode`
  (or an Xcode/AltStore profile) hasn't run yet — that's what makes it appear.
* `syslog` mode works on Wi‑Fi on every supported iOS. The structured
  **`os_trace`** firehose on **iOS 17.4+** may need a USB tunnel session on
  Windows/Linux (a Mac does it over Wi‑Fi) — see `COMPATIBILITY.md`.
* Keep the device unlocked while a `sysdiagnose` runs (several minutes).
* No jailbreak, no Apple Configurator, no MDM required.
