import Foundation
import Network
import OSLog
import UIKit

/// One-call entry point for an app that wants to stream its logs to the
/// iDeviceTail desktop.
///
/// ```swift
/// // Auto-discover the desktop on the LAN:
/// LogForwarder.shared.start()
///
/// // …or pin it:
/// LogForwarder.shared.start(host: "192.168.1.50", port: 45455)
///
/// // Explicit events (in addition to captured os_log output):
/// LogForwarder.shared.log(.error, "checkout failed", subsystem: "shop", category: "pay")
/// ```
///
/// What it streams: this process's own Unified Log entries (via
/// `OSLogStoreReader`) plus anything you pass to `log(_:_:)`. It cannot see
/// other apps or the system — that is an iOS sandbox limit, documented in
/// docs/FEASIBILITY.md.
public final class LogForwarder: @unchecked Sendable {

    public static let shared = LogForwarder()

    public enum Level: String { case debug, info, notice, warning, error, fault }

    private let client: LogStreamClient
    private var reader: Any?     // OSLogStoreReader (availability-gated)
    private var browser: HostBrowser?
    private let device: DeviceInfo
    private var started = false
    private let lock = NSLock()

    public var onState: ((LogStreamClient.State) -> Void)? {
        get { client.onState }
        set { client.onState = newValue }
    }
    public var stats: (sent: Int, dropped: Int, state: LogStreamClient.State) {
        (client.sentCount, client.droppedCount, client.state)
    }

    private init() {
        let dev = UIDevice.current
        self.device = DeviceInfo(
            id: dev.identifierForVendor?.uuidString ?? UUID().uuidString,
            name: dev.name,
            model: Self.hardwareModel(),
            os: "\(dev.systemName) \(dev.systemVersion)",
            app: Bundle.main.bundleIdentifier ?? "unknown"
        )
        self.client = LogStreamClient(device: device)
    }

    // MARK: start / stop

    /// Auto-discover the desktop via Bonjour and stream.
    public func start(captureOSLog: Bool = true, backfillSeconds: TimeInterval = 5) {
        lock.lock(); defer { lock.unlock() }
        guard !started else { return }
        started = true

        let b = HostBrowser { [weak self] endpoint in
            self?.client.connect(endpoint: endpoint)
        }
        b.start()
        self.browser = b
        beginCapture(captureOSLog: captureOSLog, backfillSeconds: backfillSeconds)
    }

    /// Stream to a fixed host/port (no Bonjour).
    public func start(host: String, port: UInt16, captureOSLog: Bool = true, backfillSeconds: TimeInterval = 5) {
        lock.lock(); defer { lock.unlock() }
        guard !started else { return }
        started = true
        client.connect(host: host, port: port)
        beginCapture(captureOSLog: captureOSLog, backfillSeconds: backfillSeconds)
    }

    public func stop() {
        lock.lock(); defer { lock.unlock() }
        started = false
        browser?.stop(); browser = nil
        if #available(iOS 15.0, macCatalyst 15.0, *) {
            (reader as? OSLogStoreReader)?.stop()
        }
        reader = nil
        client.stop()
    }

    private func beginCapture(captureOSLog: Bool, backfillSeconds: TimeInterval) {
        guard captureOSLog else { return }
        if #available(iOS 15.0, macCatalyst 15.0, *) {
            let r = OSLogStoreReader(backfillSeconds: backfillSeconds) { [weak self] batch in
                self?.client.send(batch)
            }
            r.start()
            self.reader = r
        }
    }

    // MARK: explicit logging

    public func log(
        _ level: Level,
        _ message: String,
        subsystem: String = "",
        category: String = "",
        file: String = #fileID,
        line: Int = #line
    ) {
        client.send(
            LogRecord(
                level: level.rawValue,
                process: device.app,
                pid: Int(ProcessInfo.processInfo.processIdentifier),
                subsystem: subsystem,
                category: category,
                message: message,
                file: file,
                line: line
            )
        )
    }

    // MARK: helpers

    static func hardwareModel() -> String {
        var sysinfo = utsname()
        uname(&sysinfo)
        let mirror = Mirror(reflecting: sysinfo.machine)
        let id = mirror.children.reduce(into: "") { acc, e in
            if let v = e.value as? Int8, v != 0 { acc.append(Character(UnicodeScalar(UInt8(v)))) }
        }
        return id.isEmpty ? "iOS device" : id
    }
}
