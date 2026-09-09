import Foundation

/// One log line on the wire. Field names match the desktop's `agent_payload_to_record`
/// (see docs/PROTOCOL.md). Only `ts` and `message` are strictly required.
public struct LogRecord: Codable, Sendable {
    public var type: String = "log"
    public var ts: Double                 // epoch seconds, UTC
    public var level: String              // debug|info|notice|warning|error|fault
    public var process: String
    public var pid: Int?
    public var subsystem: String
    public var category: String
    public var message: String
    public var thread: UInt64?
    public var file: String?
    public var line: Int?

    public init(
        ts: Double = Date().timeIntervalSince1970,
        level: String = "info",
        process: String = "",
        pid: Int? = nil,
        subsystem: String = "",
        category: String = "",
        message: String,
        thread: UInt64? = nil,
        file: String? = nil,
        line: Int? = nil
    ) {
        self.ts = ts
        self.level = level
        self.process = process
        self.pid = pid
        self.subsystem = subsystem
        self.category = category
        self.message = message
        self.thread = thread
        self.file = file
        self.line = line
    }
}

/// Handshake frame sent once when a connection opens.
public struct HelloFrame: Codable, Sendable {
    public var type = "hello"
    public var protocolVersion = 1
    public var device: DeviceInfo
    public var session: String

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol"
        case device
        case session
    }
}

public struct DeviceInfo: Codable, Sendable {
    public var id: String
    public var name: String
    public var model: String
    public var os: String
    public var app: String
}

/// A batch of records in one frame (efficient for bursts / backlog replay).
public struct BatchFrame: Codable, Sendable {
    public var type = "batch"
    public var logs: [LogRecord]
}

public struct PingFrame: Codable, Sendable {
    public var type = "ping"
    public var t: Double
}
