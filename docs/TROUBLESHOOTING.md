# Troubleshooting

## Device not discovered

| Check | Fix |
|---|---|
| Same subnet? | Desktop and device on the **same Wi‑Fi** / VLAN. Guest networks and "AP/client isolation" block mDNS + TCP — disable isolation or use **Manual host** (agent) / a wired‑then‑wireless pairing (Engine A). |
| Engine A device never appears | Run `python -m idevicetail devices`. Empty ⇒ not paired / Wi‑Fi‑sync off / device locked. Redo `pair` + `wifi-sync` by USB. Also try `python -m pymobiledevice3 bonjour mobdev2` directly. |
| **Paired device shows, then vanishes / says "not reachable now" / logs stop after ~30 s** | The iPhone **locked** and left Wi‑Fi. Set *Settings ▸ Display & Brightness ▸ Auto‑Lock ▸ Never*, and/or keep it **on a charger**, and/or turn on *"Sync over Wi‑Fi"* in the Apple Devices app. Streaming **auto‑resumes** when the phone is reachable again. Quick check: `ping <phone-ip>`. |
| Bonjour on Windows | The Apple **Bonjour** service (from Apple Devices/iTunes) helps but isn't required — `zeroconf` does its own mDNS. Ensure UDP 5353 isn't firewalled. |
| Agent app | Confirm **Local Network** permission was allowed (Settings ▸ Privacy & Security ▸ Local Network ▸ iDeviceTail Agent). |
| Corporate mDNS filtering | Use the agent's **Manual host** field with the desktop IP + `45455`. |

## Pairing failure

* `pair` says *"Please accept the trust dialog"* → unlock the device, tap
  **Trust**, enter passcode, re‑run.
* *"pairing failed / SessionInactive"* → unplug/replug, ensure the device is
  unlocked at the Home screen, close Xcode/Finder/iTunes windows that may hold
  the lockdown session, retry.
* Stale record after an iOS restore → delete the host pairing record
  (`pymobiledevice3 lockdown unpair --udid <UDID>`), pair again.

## Developer Mode disabled / missing

* Missing from Settings → run `python -m idevicetail devmode <UDID>` (or install
  any dev‑signed app via AltStore/Sideloadly/Xcode); the toggle then appears.
* Enabled but firehose still fails → reboot the device; confirm the on‑device
  "Turn On" prompt was accepted.

## Logs unavailable / stream exits immediately

* `syslog` mode: check `pymobiledevice3 syslog live --udid <UDID>` directly; the
  stderr it prints is surfaced in the UI device row and `stream` CLI (`# …`).
* `oslog` mode on iOS 17.4+ from Windows/Linux over Wi‑Fi: expected to fail —
  use `--tunnel` **with the device on USB** for that session, or run `syslog`
  mode wirelessly, or use a Mac.
* iOS ≤ 16: no tunnel flags needed; drop `--tunnel`/`--rsd`.
* "No module named pymobiledevice3" → `pip install "pymobiledevice3>=4.14"` in
  the same venv, or set `IDEVICETAIL_PYMD` to its full path.
* **`pip install pymobiledevice3` fails building `lzfse` / `pylzss`**
  ("Microsoft Visual C++ 14.0 or greater is required"): you're on a Python
  version with no prebuilt wheels for those deps (e.g. 3.14). Use a **3.12 or
  3.13** venv instead:
  `py install 3.12 && py -3.12 -m venv .venv && .venv\Scripts\python -m pip install -e ".[device]"`.
  Engine B (the agent) is unaffected.

## Wi‑Fi isolation / can't reach the desktop

* Agent stuck at *waiting / reconnecting* → the desktop's `--agent-port` is
  firewalled or the network isolates clients. Add the firewall rule
  (`SETUP-DESKTOP.md §3`); test with `nc <desktop-ip> 45455` from another
  machine.

## Firewall (Windows)

```powershell
netsh advfirewall firewall add rule name="idevicetail" dir=in action=allow protocol=TCP localport=3017,45455 profile=private
netsh advfirewall firewall add rule name="idevicetail-mdns" dir=in action=allow protocol=UDP localport=5353 profile=private
```

## Device disconnected / reboots / sleep‑wake

* Engine A auto‑reconnects (exp backoff, capped 30 s); the device row shows
  `connecting → streaming`. Nothing to do.
* Long Wi‑Fi drop: the child process exits, supervisor respawns it when the
  device is reachable again.
* Desktop sleep: on wake, discovery re‑runs and captures resume.

## Signing / provisioning (agent app)

* *"Unable to install — profile expired"* (free Apple ID, 7‑day limit) →
  re‑sign / re‑deploy (Xcode Run again, or AltStore refresh).
* *"Untrusted Developer"* → Settings ▸ General ▸ VPN & Device Management ▸ trust
  your certificate.
* Local Network prompt never showed → delete + reinstall the app, or reset
  *Settings ▸ General ▸ Transfer or Reset iPhone ▸ Reset ▸ Location & Privacy*.

## Unsupported iOS version

* iOS < 15: the agent (`OSLogStore`) won't build/run; Engine A `syslog` still
  works via `pymobiledevice3`.
* Brand‑new iOS (e.g. a fresh .0) before `pymobiledevice3` catches up: `oslog`
  tunnel may break for a few weeks — use `syslog` mode meanwhile and update
  `pymobiledevice3`.

## UI feels slow with huge volume

* The list virtualizes (~visible rows only) and caps memory at 200k lines
  (oldest dropped). Narrow with **process/subsystem/level** filters, or
  **Pause** while you read. "N dropped (slow client)" means your browser tab
  couldn't keep up — capture and storage were unaffected.
