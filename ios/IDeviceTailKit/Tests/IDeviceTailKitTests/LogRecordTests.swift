import XCTest
@testable import IDeviceTailKit

final class LogRecordTests: XCTestCase {

    func testHelloEncodesProtocolKey() throws {
        let hello = HelloFrame(
            device: DeviceInfo(id: "id", name: "iPhone", model: "iPhone16,1", os: "iOS 18.5", app: "com.x"),
            session: "s1"
        )
        let payload = try FrameCodec.encode(hello).suffix(from: 4)
        let obj = try JSONSerialization.jsonObject(with: payload) as! [String: Any]
        XCTAssertEqual(obj["protocol"] as? Int, 1)          // not "protocolVersion"
        XCTAssertEqual((obj["device"] as? [String: Any])?["model"] as? String, "iPhone16,1")
        XCTAssertEqual(obj["session"] as? String, "s1")
    }

    func testRoundTripThroughJSON() throws {
        let rec = LogRecord(
            ts: 12345.678, level: "notice", process: "P", pid: 9,
            subsystem: "a.b", category: "c", message: "m", thread: 42, file: "F.swift", line: 7
        )
        let data = try FrameCodec.encode(rec)
        let payload = data.suffix(from: 4)
        let back = try JSONDecoder().decode(LogRecord.self, from: payload)
        XCTAssertEqual(back.ts, 12345.678, accuracy: 0.0001)
        XCTAssertEqual(back.subsystem, "a.b")
        XCTAssertEqual(back.thread, 42)
        XCTAssertEqual(back.line, 7)
    }

    func testLevelStringMapping() {
        if #available(iOS 15.0, macCatalyst 15.0, macOS 12.0, *) {
            XCTAssertEqual(OSLogStoreReader.levelString(.fault), "fault")
            XCTAssertEqual(OSLogStoreReader.levelString(.debug), "debug")
            XCTAssertEqual(OSLogStoreReader.levelString(.undefined), "info")
        }
    }
}
