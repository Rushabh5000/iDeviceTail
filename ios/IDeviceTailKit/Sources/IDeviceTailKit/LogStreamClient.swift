import Foundation
import Network

/// Maintains one framed TCP connection to the desktop and streams `LogRecord`s.
///
/// Reliability behavior:
/// * exponential backoff + jitter reconnect (1s → 30s cap)
/// * bounded outbound buffer; when full the **oldest** records are dropped
///   (a dead link can never grow memory without bound)
/// * on (re)connect: send `hello`, then flush the buffer as `batch` frames
/// * 15s `ping` keepalive; a stalled send path triggers a reconnect
public final class LogStreamClient: @unchecked Sendable {

    public enum State: Equatable { case idle, connecting, connected, waiting }

    public private(set) var state: State = .idle {
        didSet { onState?(state) }
    }
    public var onState: ((State) -> Void)?

    private let device: DeviceInfo
    private let session = UUID().uuidString
    private let queue = DispatchQueue(label: "idevicetail.streamclient")
    private var connection: NWConnection?
    private var endpoint: NWEndpoint?
    private var explicit: (host: String, port: UInt16)?

    private var buffer: [LogRecord] = []
    private let bufferCap = 20_000
    public private(set) var droppedCount = 0
    public private(set) var sentCount = 0

    private var backoff: TimeInterval = 1
    private var pingTimer: DispatchSourceTimer?
    private var flushScheduled = false
    private var recvBuf = Data()

    public init(device: DeviceInfo) {
        self.device = device
    }

    // MARK: control

    /// Point at a specific host/port (manual mode).
    public func connect(host: String, port: UInt16) {
        queue.async {
            self.explicit = (host, port)
            self.endpoint = nil
            self.restart()
        }
    }

    /// Point at a Bonjour-resolved endpoint (auto mode).
    public func connect(endpoint: NWEndpoint) {
        queue.async {
            self.endpoint = endpoint
            self.explicit = nil
            self.restart()
        }
    }

    public func stop() {
        queue.async {
            self.pingTimer?.cancel(); self.pingTimer = nil
            self.connection?.cancel(); self.connection = nil
            self.state = .idle
        }
    }

    /// Enqueue records for delivery (thread-safe).
    public func send(_ records: [LogRecord]) {
        guard !records.isEmpty else { return }
        queue.async {
            self.buffer.append(contentsOf: records)
            if self.buffer.count > self.bufferCap {
                let overflow = self.buffer.count - self.bufferCap
                self.buffer.removeFirst(overflow)
                self.droppedCount += overflow
            }
            self.scheduleFlush()
        }
    }

    public func send(_ record: LogRecord) { send([record]) }

    // MARK: connection lifecycle

    private func restart() {
        pingTimer?.cancel(); pingTimer = nil
        connection?.cancel()
        let conn: NWConnection
        if let e = explicit {
            conn = NWConnection(
                host: .init(e.host),
                port: .init(rawValue: e.port) ?? 45455,
                using: .tcp
            )
        } else if let ep = endpoint {
            conn = NWConnection(to: ep, using: .tcp)
        } else {
            return
        }
        connection = conn
        state = .connecting

        conn.stateUpdateHandler = { [weak self] st in
            guard let self else { return }
            switch st {
            case .ready:
                self.backoff = 1
                self.state = .connected
                self.sendHello()
                self.startReceive()
                self.startPing()
                self.scheduleFlush()
            case .failed, .cancelled:
                self.state = .waiting
                self.scheduleReconnect()
            case .waiting:
                self.state = .waiting
            default:
                break
            }
        }
        conn.start(queue: queue)
    }

    private func scheduleReconnect() {
        pingTimer?.cancel(); pingTimer = nil
        let delay = min(backoff, 30) * Double.random(in: 0.7 ... 1.3)
        backoff = min(backoff * 2, 30)
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.state == .waiting else { return }
            self.restart()
        }
    }

    private func sendHello() {
        let hello = HelloFrame(
            device: device,
            session: session
        )
        if let data = try? FrameCodec.encode(hello) {
            connection?.send(content: data, completion: .contentProcessed { _ in })
        }
    }

    private func startPing() {
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 15, repeating: 15)
        t.setEventHandler { [weak self] in
            guard let self, let conn = self.connection, self.state == .connected else { return }
            if let data = try? FrameCodec.encode(PingFrame(t: Date().timeIntervalSince1970)) {
                conn.send(content: data, completion: .contentProcessed { err in
                    if err != nil { self.queue.async { self.state = .waiting; self.scheduleReconnect() } }
                })
            }
        }
        pingTimer = t
        t.resume()
    }

    private func startReceive() {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, isDone, err in
            guard let self else { return }
            if let data, !data.isEmpty {
                self.recvBuf.append(data)
                _ = FrameCodec.drainObjects(&self.recvBuf)   // welcome/pong — informational
            }
            if isDone || err != nil {
                self.state = .waiting
                self.scheduleReconnect()
            } else {
                self.startReceive()
            }
        }
    }

    // MARK: outbound flush

    private func scheduleFlush() {
        guard !flushScheduled else { return }
        flushScheduled = true
        queue.async { [weak self] in
            self?.flushScheduled = false
            self?.flush()
        }
    }

    private func flush() {
        guard state == .connected, let conn = connection, !buffer.isEmpty else { return }
        // send in chunks so one frame is never enormous
        let chunkSize = 400
        let chunk = Array(buffer.prefix(chunkSize))
        buffer.removeFirst(chunk.count)
        guard let data = try? FrameCodec.encode(BatchFrame(logs: chunk)) else { return }
        conn.send(content: data, completion: .contentProcessed { [weak self] err in
            guard let self else { return }
            self.queue.async {
                if err != nil {
                    // put them back at the front and reconnect
                    self.buffer.insert(contentsOf: chunk, at: 0)
                    self.state = .waiting
                    self.scheduleReconnect()
                } else {
                    self.sentCount += chunk.count
                    if !self.buffer.isEmpty { self.scheduleFlush() }
                }
            }
        })
    }
}
