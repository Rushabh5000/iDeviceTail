# Compatibility matrix

`Dev Mode` = iOS Developer Mode required. `Cable` = one‑time USB step required
(everything after is wireless). `Pair` = trusted pairing required.

## Capability × iOS version

| Capability | iOS 15 | iOS 16 | iOS 17.0–17.3 | iOS 17.4 – 18.x / 26 | Dev Mode | Cable (once) | Pair |
|---|---|---|---|---|---|---|---|
| **Device discovery** (Bonjour `_apple-mobdev2`) | ✅ | ✅ | ✅ | ✅ | no | no¹ | yes (to advertise) |
| **Real‑time system logs — `syslog` mode** (`syslog_relay`, text) | ✅ Wi‑Fi | ✅ Wi‑Fi | ✅ Wi‑Fi | ✅ Wi‑Fi | 16+: recommended | yes | yes |
| **Real‑time system logs — `oslog` mode** (`os_trace_relay`, structured firehose) | ✅ Wi‑Fi | ✅ Wi‑Fi (Dev Mode) | ✅ Wi‑Fi via user‑space tunnel (no root) | ⚠️ Wi‑Fi = macOS only; Win/Linux = USB tunnel session | yes (16+) | yes | yes |
| **Crash reports** (`crashreportcopymobile`) | ✅ Wi‑Fi | ✅ | ✅ | ✅ | no² | yes | yes |
| **Sysdiagnose** (trigger + pull) | ✅ Wi‑Fi | ✅ | ✅ | ✅ | no² | yes | yes |
| **App's own logs — agent** (`OSLogStore` + `IDeviceTailKit`) | ✅ | ✅ | ✅ | ✅ | **no** | **no³** | **no** |
| **Multiple devices at once** | ✅ | ✅ | ✅ | ✅ | — | — | — |
| **Auto‑reconnect** | ✅ | ✅ | ✅ | ✅ | — | — | — |

¹ Discovery itself needs no cable, but the device only advertises once it has a
pairing + Wi‑Fi sync, which needed a cable once.
² Not gated by Developer Mode in practice, but you still need the pairing that
required a cable.
³ No cable to *run*. On **Windows** you need a cable once to sideload the agent
app (AltStore/Sideloadly); on **macOS** you can install it wirelessly from Xcode
after the initial pairing, or via TestFlight with no cable at all.

## Capability × host OS

| | macOS | Windows | Linux |
|---|---|---|---|
| Engine A — `syslog` mode over Wi‑Fi | ✅ | ✅ | ✅ |
| Engine A — `oslog` firehose over Wi‑Fi (iOS ≤ 17.3) | ✅ | ✅ (user‑space tunnel) | ✅ (user‑space tunnel) |
| Engine A — `oslog` firehose over Wi‑Fi (iOS 17.4+) | ✅ (`remoted`) | ⚠️ USB tunnel session only | ⚠️ USB tunnel session only |
| Engine A — crash / sysdiagnose over Wi‑Fi | ✅ | ✅ | ✅ |
| Engine B — agent | ✅ | ✅ | ✅ |
| Wireless **install** of the agent app | ✅ (Xcode/TestFlight) | ❌ (USB once via AltStore/Sideloadly) | ❌ (USB once) |
| One‑time provisioning (pair / Wi‑Fi sync / Dev Mode) | ✅ (Xcode / Apple Configurator / `pymobiledevice3`) | ✅ (`pymobiledevice3` + Apple Devices/iTunes for the Apple driver) | ✅ (`pymobiledevice3` + `usbmuxd`) |

### Classification

```
macOS:    Full
Windows:  Partial  — full for iOS ≤ 17.3 and for syslog/crash/sysdiagnose on all versions;
                     the structured firehose on iOS 17.4+ needs a USB tunnel session or a Mac.
Linux:    Partial  — same as Windows.
```

## Supervised / MDM devices

Not required. A **supervised** device can have Developer‑Mode and pairing
behaviour managed by MDM and can skip some prompts, but this tool does not
depend on supervision and does not implement an MDM.
