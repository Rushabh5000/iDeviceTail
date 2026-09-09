# Feasibility report — wireless iOS/iPadOS log collection

*Target: iOS/iPadOS 15–18 and 26. Last verified 2026‑09. Sources at the bottom;
each claim is tagged **[D]** documented/verified, **[C]** community/observed, or
**[R]** reverse‑engineered/unofficial.*

---

## 0. TL;DR answers to the 12 questions

| # | Question | Answer |
|---|----------|--------|
| 1 | What logs can realistically be obtained? | **System logs** (all processes) via Apple's device‑debug channel; **crash reports**; **sysdiagnose** archives; and an app's **own** `os_log` output from inside that app. |
| 2 | Wirelessly? | Yes for all of the above, **after a one‑time USB pairing** (Engine A). The app‑own‑logs path (Engine B) needs no cable at all except to sideload the app once on Windows. |
| 3 | Apple restrictions? | Third‑party code cannot read the system‑wide log firehose directly. It must go through **lockdown services** (`syslog_relay`, `os_trace_relay`) which require a **trusted pairing**; iOS 16+ adds **Developer Mode**; iOS 17+ adds the **RSD/RemoteXPC tunnel** for the structured firehose. |
| 4 | Pairing mandatory? | **Yes** for Engine A. Pairing can only be established over USB (user taps *Trust* + passcode). No supported way to pair purely over Wi‑Fi. |
| 5 | Developer Mode mandatory? | **In practice yes on iOS 16+** for reliable `os_trace_relay` streaming. `syslog_relay` sometimes works without it **[C]**, but assume it's required. Not needed for Engine B. |
| 6 | One‑time cable required? | **Yes**, once, for: pairing, enabling Wi‑Fi sync, and triggering the Developer Mode toggle. Everything after is wireless. (On a Mac the same one‑time cable step applies; Apple Configurator/Xcode can also do it.) |
| 7 | Works on a normal App Store iPhone? | Engine A: yes, no jailbreak, no special account — just Trust + Developer Mode. Engine B: yes, but you must install the agent app (free Apple ID = 7‑day resign, or paid = 1 year). |
| 8 | Must the computer be a Mac? | **No for Engine A's `syslog` mode and Engine B.** **Effectively yes for the full `os_trace` firehose on iOS 17.4+**, because the no‑root tunnel is USB‑bound on Windows/Linux while macOS gets a Wi‑Fi‑capable `remoted` tunnel for free. |
| 9 | Can Windows/Linux participate? | Yes — full featured for iOS ≤ 17.3; `syslog` + crash + sysdiagnose wirelessly for 17.4+; full firewall‑free firehose needs USB tunnel or a Mac. |
| 10 | Continuous operation? | Yes. Engine A = long‑lived child process with supervised reconnect. Engine B = persistent TCP with backoff. |
| 11 | Multiple devices? | Yes — independent capture task per device; the agent listener accepts many apps at once. |
| 12 | What can't be obtained? | Real‑time logs of *other* third‑party apps from inside an app; anything from a non‑paired device wirelessly; private user data; decrypted network traffic; kernel internals beyond what Apple's log surfaces expose. |

---

## 1. What iOS actually exposes

### 1.1 Unified Logging (`os_log`) and `OSLogStore`  **[D]**

`os_log`/`Logger` write to the Unified Logging system. Reading it back
programmatically is done with `OSLogStore`:

* **macOS:** `OSLogStore.local()` → the whole system log. Requires no special
  entitlement for the current user's readable scope.
* **iOS/iPadOS:** the **only** permitted scope for a sandboxed app is
  `OSLogStore(scope: .currentProcessIdentifier)`. That returns **entries created
  by the calling process only**, and only since the current launch. There is no
  supported entitlement a third‑party App‑Store/sideloaded app can get to widen
  this (`com.apple.logging.local-store` / `com.apple.private.logging.admin` are
  Apple‑internal). Apple engineers confirm this on the developer forums.

**Consequence:** an on‑device agent app is fundamentally limited to *its own*
logs. It can never be a "system log viewer". `IDeviceTailKit` is built exactly to
that boundary.

### 1.2 The device‑debug channel: lockdown + relay services  **[D]/[C]/[R]**

The mechanism Console.app, Xcode and `log stream --device` use is **not** a
public framework you can link. It is a set of services exposed by `lockdownd`
over:

* **USB** via `usbmuxd` (Apple Mobile Device Support / `MobileDevice.framework`)
* **Wi‑Fi** on TCP **62078** once the device advertises `_apple-mobdev2._tcp`
  (only when "Wi‑Fi sync" / Xcode "Connect via network" is on **and** a valid
  pairing exists).

Relevant services:

| Service | Gives you | Notes |
|---------|-----------|-------|
| `com.apple.syslog_relay` | Live `syslog`‑level stream (kernel + many daemons, ASL‑style text) | Classic; reachable over plain Wi‑Fi lockdown; widest version support. Less structured (no guaranteed subsystem/category). **[C]** |
| `com.apple.os_trace_relay` | The full `os_log`/Activity Tracing **firehose** with subsystem/category/level, plus PID list & one‑shot dumps | On iOS 17+ the RSD form is `com.apple.os_trace_relay.shim.remote` and generally needs the tunnel. **[C]/[R]** |
| `com.apple.crashreportcopymobile` | Copy `/var/mobile/Library/Logs/CrashReporter` (`.ips`, `.crash`, panics) | AFC‑like file access. **[D]** for the capability, **[R]** for the wire detail. |
| `com.apple.mobile.diagnostics_relay` / `os_trace_relay` sysdiagnose | Trigger + copy a **sysdiagnose** archive | Minutes to produce; needs device unlocked. **[C]** |
| `com.apple.mobile.installation_proxy`, `com.apple.afc`, `com.apple.mobile.house_arrest` | App inventory; media / an app's *own* container files | House‑arrest only reaches containers of **development‑signed** or documents‑sharing apps — **not** arbitrary third‑party app logs. **[D]** |

There is **no supported client library from Apple** for these on Windows/Linux.
The de‑facto implementation is the open‑source **`pymobiledevice3`** (Python,
runs on Windows/Linux/macOS) or **`libimobiledevice`** (C). This project drives
`pymobiledevice3` as a child process.

### 1.3 iOS 17+ changed the transport  **[D]/[C]**

From iOS 17.0, Apple moved developer‑service access to **CoreDevice /
RemoteXPC**. Practical effects:

* The structured firehose (`os_trace_relay`) is reached through a
  **RemoteServiceDiscovery (RSD)** endpoint over a **tunnel** (a virtual IPv6
  interface to the device), not plain port 62078.
* **iOS 17.0–17.3.1:** `pymobiledevice3` can build that tunnel in *user space*
  (pure‑Python TCP stack, **no root**) over **USB or Wi‑Fi**, on all three OSes.
* **iOS 17.4+:** Apple added `CoreDeviceProxy` over lockdown, so the no‑root
  user‑space tunnel works **without extra drivers** — but in practice it is
  **USB‑bound on Windows/Linux**. macOS gets Apple's native `remoted` tunnel
  which **does** work over Wi‑Fi.
* A privileged `tunneld` daemon (root/admin) gives a persistent shared tunnel on
  any OS but is still USB‑oriented for 17.4+.
* `syslog_relay` (the classic text stream) is **still reachable over plain Wi‑Fi
  lockdown** on 17/18/26 without any tunnel **[C]** — which is why this project's
  default wireless mode on Windows is `syslog`, not `oslog`.

### 1.4 Developer Mode (iOS 16+)  **[D]**

* Introduced iOS 16; present on every release since (incl. 26).
* The toggle in *Settings ▸ Privacy & Security ▸ Developer Mode* is **hidden
  until the device has been "prepared for development"** — i.e. connected to
  Xcode once, or had a development‑signed app / provisioning profile installed
  (AltStore, Sideloadly, `pymobiledevice3 amfi enable-developer-mode` all
  trigger it).
* Enabling it **reboots the device** and requires on‑device confirmation after
  unlock.
* Gates the Developer Disk Image mount and most `com.apple.instruments.*` /
  debugserver services. Log relays don't need the DDI, but treat Developer Mode
  as **required** for a dependable `os_trace_relay` stream on 16+.

### 1.5 What is simply not possible on stock iOS

* An app reading another app's or the system's live logs. *(sandbox — 1.1)*
* Pairing / trusting a host purely over Wi‑Fi. *(must be USB + passcode)*
* Any of this on a device where the owner won't enable Developer Mode / tap
  Trust.
* Continuous `sysdiagnose` (it's a heavy minutes‑long archive, not a stream).
* Getting the firehose wirelessly on iOS 17.4+ from Windows/Linux **without**
  either a USB tunnel session or a Mac in the loop.

---

## 2. Approaches evaluated

| Approach | Real logs? | Wireless? | Setup cost | Verdict |
|----------|-----------|-----------|------------|---------|
| **1. Apple device channel via `pymobiledevice3`** | ✅ system + crash + sysdiagnose | ✅ after 1× USB | pair + Wi‑Fi sync + Dev Mode | **Adopted as Engine A.** The only way to get *other processes'* logs. |
| **2. On‑device agent streams its OWN logs** | ⚠️ app‑scoped only | ✅ fully cable‑free | install 1 app | **Adopted as Engine B.** Fills the "no cable ever, no pairing" requirement for app debugging. |
| **3. sysdiagnose / diagnostic pull** | ✅ very rich, historical | ✅ via Engine A | same as #1 | **Adopted as a feature of Engine A**, not a live path — too slow for streaming. |
| **4. Private frameworks / custom lockdown client** | ✅ | ✅ | high, brittle | **Rejected as a build target.** Re‑implementing `pymobiledevice3` adds risk with no capability gain. We reuse it instead. No Apple *private API* is used by our own code. |
| **5. Mac‑only native (`libMobileDevice`, `devicectl`, `remoted`)** | ✅ incl. 17.4+ firehose over Wi‑Fi | ✅ | pair once | **Documented as the "best case" path.** `devicectl` is CoreDevice‑only (iOS 17+), Mac‑only, and its `device console`/log‑stream sub‑commands have been unstable across Xcode 16→26 **[C]** — so even on macOS we prefer `pymobiledevice3` for the log stream and treat `devicectl` as optional. |

### Scoring (weights from the brief)

| Criterion (weight) | Engine A only | Engine B only | **A + B hybrid (chosen)** |
|---|---|---|---|
| Works on stock iOS (30) | 30 | 24 | **30** |
| Wireless operation (20) | 16 | 20 | **19** |
| Ease of setup (15) | 8 | 13 | **12** |
| Reliability (15) | 12 | 13 | **14** |
| Access to useful logs (10) | 10 | 5 | **10** |
| Maintainability (5) | 4 | 5 | **4.5** |
| Cross‑platform (5) | 3 | 5 | **4.5** |
| **Total** | **83** | **85** | **94** |

The hybrid wins: Engine B covers the "zero‑friction, no‑cable" app‑debugging
use, Engine A covers "I need the whole device", and one desktop app + one wire
format unify them.

---

## 3. Sources

- Apple — `OSLogStore`, `OSLogStore.Scope`, `OSLogEntryLog`:
  <https://developer.apple.com/documentation/oslog/oslogstore>
- Apple Developer Forums — "OSLogStore on iOS only allows `.currentProcessIdentifier`":
  <https://developer.apple.com/forums/thread/705868>,
  <https://developer.apple.com/forums/thread/691093>,
  <https://developer.apple.com/forums/thread/658229>
- Apple — Enabling Developer Mode on a device:
  <https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device>
- Apple Platform Security (pairing / lockdown trust model):
  <https://support.apple.com/guide/security/welcome/web>
- Apple — `devicectl` (CoreDevice, Xcode 15+/iOS 17+):
  <https://developer.apple.com/documentation/xcode/>  (Xcode 15 release notes),
  `man devicectl`
- `pymobiledevice3` — repo, feature list, iOS 17+ tunnels & support matrix, protocol layers:
  <https://github.com/doronz88/pymobiledevice3>,
  <https://doronz88.github.io/pymobiledevice3/guides/ios17-tunnels/>,
  <https://github.com/doronz88/pymobiledevice3/blob/master/misc/understanding_idevice_protocol_layers.md>
- `libimobiledevice` — `idevicesyslog`, `idevicecrashreport`:
  <https://libimobiledevice.org/>
- "iOS Lockdown Diagnostic Services" (service enumeration, `os_trace_relay`):
  <https://gist.github.com/ddz/b6879ba86fc7ddc2e26f>
- "Investigating realtime detections on iOS using Unified Logging" — `os_trace_relay`
  behaviour: <https://blog.tofile.dev/2024/10/24/ios-detections.html>
- The Apple Wiki — System Log / lockdown services:
  <https://theapplewiki.com/wiki/System_Log>
- AltStore / Sideloadly (Windows, Wi‑Fi refresh after 1× USB, triggers Dev Mode):
  <https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows>
