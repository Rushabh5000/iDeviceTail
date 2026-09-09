import XCTest
@testable import IDeviceTailKit

final class FrameCodecTests: XCTestCase {

    func testEncodeIsLengthPrefixed() throws {
        let rec = LogRecord(ts: 1_700_000_000, level: "error", message: "hello")
        let data = try FrameCodec.encode(rec)
        let len = data.prefix(4).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
        XCTAssertEqual(Int(len), data.count - 4)

        let obj = try JSONSerialization.jsonObject(with: data.suffix(from: 4)) as! [String: Any]
        XCTAssertEqual(obj["message"] as? String, "hello")
        XCTAssertEqual(obj["level"] as? String, "error")
        XCTAssertEqual(obj["type"] as? String, "log")
    }

    func testDrainHandlesSplitAndMultipleFrames() throws {
        let a = try FrameCodec.encode(LogRecord(message: "one"))
        let b = try FrameCodec.encode(BatchFrame(logs: [LogRecord(message: "two"), LogRecord(message: "three")]))
        var buf = Data()

        buf.append(a.prefix(3))
        XCTAssertTrue(FrameCodec.drainObjects(&buf).isEmpty)   // partial header

        buf.append(a.suffix(from: 3))
        buf.append(b)
        let objs = FrameCodec.drainObjects(&buf)
        XCTAssertEqual(objs.count, 2)
        XCTAssertEqual(objs[0]["message"] as? String, "one")
        XCTAssertEqual((objs[1]["logs"] as? [[String: Any]])?.count, 2)
        XCTAssertEqual(buf.count, 0)
    }

    func testOversizedFrameIsDiscardedNotCrashing() {
        var buf = Data()
        var len = UInt32(99_000_000).bigEndian
        withUnsafeBytes(of: &len) { buf.append(contentsOf: $0) }
        buf.append(Data(repeating: 0, count: 10))
        XCTAssertTrue(FrameCodec.drainObjects(&buf).isEmpty)
        XCTAssertEqual(buf.count, 0)   // buffer reset, no infinite loop
    }
}
