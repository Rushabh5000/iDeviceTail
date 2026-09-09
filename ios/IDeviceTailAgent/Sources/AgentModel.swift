import Combine
import Foundation
import OSLog
import SwiftUI
import IDeviceTailKit

@MainActor
final class AgentModel: ObservableObject {
    enum Mode: String, CaseIterable, Identifiable { case auto = "Auto (Bonjour)", manual = "Manual host"; var id: String { rawValue } }

    @Published var running = false
    @Published var connection: LogStreamClient.State = .idle
    @Published var mode: Mode = .auto
    @Published var host = ""
    @Published var port = "45455"
    @Published var sent = 0
    @Published var dropped = 0

    private let log = Logger(subsystem: "com.example.idevicetail.agent", category: "demo")
    private var ticker: AnyCancellable?

    init() {
        LogForwarder.shared.onState = { [weak self] state in
            Task { @MainActor in self?.connection = state }
        }
    }

    func toggle() {
        running ? stop() : start()
    }

    func start() {
        switch mode {
        case .auto:
            LogForwarder.shared.start()
        case .manual:
            let p = UInt16(port) ?? 45455
            LogForwarder.shared.start(host: host.trimmingCharacters(in: .whitespaces), port: p)
        }
        running = true
        log.notice("iDeviceTail agent started (\(self.mode.rawValue, privacy: .public))")
        ticker = Timer.publish(every: 1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.refresh() }
    }

    func stop() {
        LogForwarder.shared.stop()
        running = false
        connection = .idle
        ticker?.cancel(); ticker = nil
        log.notice("iDeviceTail agent stopped")
    }

    func emitTestLogs() {
        log.debug("debug ping \(Int.random(in: 0...999))")
        log.info("info: user tapped the test button")
        log.error("error: simulated failure code \(Int.random(in: 400...599))")
        LogForwarder.shared.log(.warning, "explicit warning via LogForwarder.log()", subsystem: "demo", category: "manual")
    }

    private func refresh() {
        let s = LogForwarder.shared.stats
        sent = s.sent
        dropped = s.dropped
        connection = s.state
    }
}
