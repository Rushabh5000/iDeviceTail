import Foundation
import OSLog

/// Reads this process's own Unified Log entries via `OSLogStore`.
///
/// **Hard limit (iOS App Sandbox):** on iOS the only permitted scope is
/// `.currentProcessIdentifier`. That means this reader sees `os_log` / `Logger`
/// output **from this app's process only** — never other apps, never the
/// system. It also cannot see entries from before the current launch. This is
/// Apple's design (see docs/FEASIBILITY.md), not a bug here.
///
/// Strategy: poll incrementally from a saved position on a background queue,
/// convert `OSLogEntryLog` -> `LogRecord`, hand batches to a sink.
@available(iOS 15.0, macCatalyst 15.0, *)
public final class OSLogStoreReader: @unchecked Sendable {

    public typealias Sink = ([LogRecord]) -> Void

    private let queue = DispatchQueue(label: "idevicetail.oslogstore")
    private var timer: DispatchSourceTimer?
    private var lastDate: Date
    private let interval: TimeInterval
    private let sink: Sink
    private let processName = ProcessInfo.processInfo.processName
    private let pid = Int(ProcessInfo.processInfo.processIdentifier)

    public init(backfillSeconds: TimeInterval = 5, pollInterval: TimeInterval = 1.0, sink: @escaping Sink) {
        self.lastDate = Date().addingTimeInterval(-backfillSeconds)
        self.interval = pollInterval
        self.sink = sink
    }

    public func start() {
        queue.async { [weak self] in
            guard let self else { return }
            let t = DispatchSource.makeTimerSource(queue: self.queue)
            t.schedule(deadline: .now(), repeating: self.interval)
            t.setEventHandler { [weak self] in self?.tick() }
            self.timer = t
            t.resume()
        }
    }

    public func stop() {
        queue.async { [weak self] in
            self?.timer?.cancel()
            self?.timer = nil
        }
    }

    private func tick() {
        guard let store = try? OSLogStore(scope: .currentProcessIdentifier) else { return }
        let position = store.position(date: lastDate)
        guard let entries = try? store.getEntries(at: position) else { return }

        var batch: [LogRecord] = []
        var newest = lastDate
        for entry in entries {
            guard entry.date > lastDate else { continue }
            if entry.date > newest { newest = entry.date }
            guard let log = entry as? OSLogEntryLog else { continue }
            batch.append(
                LogRecord(
                    ts: log.date.timeIntervalSince1970,
                    level: Self.levelString(log.level),
                    process: log.process.isEmpty ? processName : log.process,
                    pid: log.processIdentifier == 0 ? pid : Int(log.processIdentifier),
                    subsystem: log.subsystem,
                    category: log.category,
                    message: log.composedMessage,
                    thread: log.threadIdentifier
                )
            )
            if batch.count >= 500 { sink(batch); batch.removeAll(keepingCapacity: true) }
        }
        lastDate = newest
        if !batch.isEmpty { sink(batch) }
    }

    static func levelString(_ l: OSLogEntryLog.Level) -> String {
        switch l {
        case .debug: return "debug"
        case .info: return "info"
        case .notice: return "notice"
        case .error: return "error"
        case .fault: return "fault"
        case .undefined: return "info"
        @unknown default: return "info"
        }
    }
}
