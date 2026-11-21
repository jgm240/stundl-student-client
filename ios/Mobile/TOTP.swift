import CryptoKit
import Foundation

/// Base32 (RFC 4648) as used for the Untis Mobile secret.
enum Base32 {
    static func decode(_ text: String) -> Data? {
        let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        var bits = 0, value = 0
        var out = Data()
        for ch in text.uppercased() where ch != " " && ch != "=" {
            guard let idx = alphabet.firstIndex(of: ch) else { return nil }
            value = (value << 5) | idx
            bits += 5
            if bits >= 8 {
                out.append(UInt8((value >> (bits - 8)) & 0xFF))
                bits -= 8
                value &= (1 << bits) - 1
            }
        }
        return out
    }
}

/// RFC 6238 time-based one-time password (HMAC-SHA1) - the login code WebUntis expects from "Untis Mobile".
enum TOTP {
    static func code(secret: Data, atMillis ms: Int64, step: Int64 = 30, digits: Int = 6) -> String {
        var counter = UInt64(ms / 1000 / step).bigEndian
        let message = withUnsafeBytes(of: &counter) { Data($0) }
        let mac = Array(HMAC<Insecure.SHA1>.authenticationCode(for: message, using: SymmetricKey(data: secret)))
        let o = Int(mac[mac.count - 1] & 0x0F)
        let bin = (UInt32(mac[o] & 0x7F) << 24) | (UInt32(mac[o + 1]) << 16) | (UInt32(mac[o + 2]) << 8) | UInt32(mac[o + 3])
        var modulus: UInt32 = 1
        for _ in 0..<digits { modulus *= 10 }
        let code = String(bin % modulus)
        return String(repeating: "0", count: max(0, digits - code.count)) + code
    }
}
