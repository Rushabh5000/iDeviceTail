import Foundation

/// Length-prefixed framing: `UInt32` big-endian length, then that many bytes of
/// UTF-8 JSON. Mirrors `desktop/idevicetail/engine_agent.py`. A parallel NDJSON
/// mode exists on the desktop for debugging; the app always uses length-prefix.
public enum FrameCodec {

    public static let maxFrame = 8 * 1024 * 1024

    public static func encode<T: Encodable>(_ value: T) throws -> Data {
        let body = try jsonEncoder.encode(value)
        var out = Data(capacity: body.count + 4)
        var len = UInt32(body.count).bigEndian
        withUnsafeBytes(of: &len) { out.append(contentsOf: $0) }
        out.append(body)
        return out
    }

    /// Pull complete frames out of an accumulating buffer. Returns decoded JSON
    /// objects (as `[String: Any]`) and mutates `buffer` to drop what it consumed.
    public static func drainObjects(_ buffer: inout Data) -> [[String: Any]] {
        var objs: [[String: Any]] = []
        while buffer.count >= 4 {
            let len = buffer.prefix(4).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
            let total = 4 + Int(len)
            if Int(len) > maxFrame { buffer.removeAll(keepingCapacity: false); break }
            if buffer.count < total { break }
            let payload = buffer.subdata(in: 4 ..< total)
            buffer.removeSubrange(0 ..< total)
            if let obj = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                objs.append(obj)
            }
        }
        return objs
    }

    static let jsonEncoder: JSONEncoder = {
        let e = JSONEncoder()
        e.outputFormatting = [.withoutEscapingSlashes]
        return e
    }()
}
