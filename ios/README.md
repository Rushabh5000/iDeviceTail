# iOS components

| Path | What |
|---|---|
| `IDeviceTailKit/` | Swift Package. `LogForwarder` (facade), `OSLogStoreReader` (this‑process Unified Log), `LogStreamClient` (framed TCP + reconnect + backpressure), `HostBrowser` (Bonjour), `FrameCodec`, `LogRecord`. No third‑party deps — `Network` + `OSLog` only. iOS 15+. |
| `IDeviceTailAgent/` | SwiftUI app that hosts the Kit. Start/Stop, connection state, sent/dropped counters, Auto (Bonjour) or Manual host, "Emit test logs". XcodeGen project. |

## Scope (read `../docs/FEASIBILITY.md`)

`OSLogStore` on iOS only permits `scope: .currentProcessIdentifier`. The Kit
therefore streams **the host app's own `os_log`/`Logger` output** plus anything
passed to `LogForwarder.shared.log(...)`. It **cannot** read other apps or the
system — that is Engine A's job (desktop + `pymobiledevice3`).

## Build

```bash
brew install xcodegen
cd IDeviceTailAgent
xcodegen generate
open IDeviceTailAgent.xcodeproj      # set your signing Team, then Run
cd ..            # repo root
swift test       # Kit unit tests (needs a Mac)
```

Install paths (no cable during use): Xcode wireless install, TestFlight, or
AltStore/Sideloadly (USB once on Windows). See `../docs/SETUP-iOS.md`.

## Use the Kit in your own app

Add it in Xcode ▸ *Package Dependencies*, or in your own `Package.swift`:

```swift
.package(url: "https://github.com/Rushabh5000/iDeviceTail.git", from: "0.1.0")
// then: .product(name: "IDeviceTailKit", package: "iDeviceTail")
```

(The repo‑root `Package.swift` re‑exports the sources under `ios/IDeviceTailKit/`.)

```swift
import IDeviceTailKit
LogForwarder.shared.start()                                  // auto-discover desktop
LogForwarder.shared.log(.error, "payment failed", subsystem: "pay", category: "stripe")
```

Add `NSLocalNetworkUsageDescription` and `NSBonjourServices`
(`_idevtail-host._tcp`, `_idevtail._tcp`) to your Info.plist.
