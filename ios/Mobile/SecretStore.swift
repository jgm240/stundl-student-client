import Foundation
import LocalAuthentication
import Security

/// Who and where (not secret). The secret key is stored separately, see SecretStore.
struct UntisAccount: Codable, Equatable {
    let server: String
    let school: String
    let user: String
}

/// What the WebUntis QR code carries: untis://setschool?url=<server>&school=<name>&user=<user>&key=<secret>&schoolNumber=<n>
struct QRPayload {
    let account: UntisAccount
    let key: String

    static func parse(_ text: String) -> QRPayload? {
        guard let c = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)), c.scheme == "untis" else { return nil }
        var q: [String: String] = [:]
        for item in c.queryItems ?? [] {
            if let v = item.value { q[item.name] = v }
        }
        let server = (q["url"] ?? "").replacingOccurrences(of: "https://", with: "").replacingOccurrences(of: "http://", with: "")
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !server.isEmpty, let school = q["school"], !school.isEmpty, let user = q["user"], !user.isEmpty,
              let key = q["key"], Base32.decode(key) != nil else { return nil }
        return QRPayload(account: UntisAccount(server: server, school: school, user: user), key: key)
    }
}

enum LoadResult {
    case key(String)
    case missing
    case cancelled
    case failed(OSStatus)
}

/// The Untis Mobile key never touches a file, a preference, a log or the screen.
///
/// It lives only in the iOS Keychain with the strictest protection iOS offers for an item an app must be able to use:
///  - `WhenPasscodeSetThisDeviceOnly`: stays on this device (never in iCloud Keychain, never in any backup, never moved
///    to another device) and is deleted by iOS if the device passcode is removed;
///  - access control `userPresence`: every read needs Face ID / Touch ID / the device passcode;
///  - the app has no export or "show key" function.
/// To compute a login code the app must hold the key in memory for a moment; it is dropped right after each update.
enum SecretStore {
    private static let service = "com.stundl.ssc.mobile"
    private static let account = "untis-key"

    enum Failure: Error, LocalizedError {
        case noPasscode
        case keychain(OSStatus)
        var errorDescription: String? {
            switch self {
            case .noPasscode: return "Set a passcode on this device first - the key can only be stored where a passcode protects it."
            case .keychain(let s): return "The Keychain refused to store the key (error \(s))."
            }
        }
    }

    private static var base: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func save(key: String) throws {
        delete()
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .userPresence, &error) else {
            throw Failure.noPasscode
        }
        var q = base
        q[kSecAttrAccessControl as String] = access
        q[kSecAttrSynchronizable as String] = false
        q[kSecValueData as String] = Data(key.utf8)
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw Failure.keychain(status) }
    }

    /// Reads the key into memory (this shows the Face ID / passcode prompt).
    static func load(reason: String) -> LoadResult {
        let ctx = LAContext()
        ctx.localizedReason = reason
        var q = base
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        q[kSecUseAuthenticationContext as String] = ctx
        var out: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        switch status {
        case errSecSuccess:
            if let d = out as? Data, let s = String(data: d, encoding: .utf8) { return .key(s) }
            return .failed(status)
        case errSecItemNotFound: return .missing
        case errSecUserCanceled, errSecAuthFailed: return .cancelled
        default: return .failed(status)
        }
    }

    static func exists() -> Bool {
        let ctx = LAContext()
        ctx.interactionNotAllowed = true   // just check, never prompt
        var q = base
        q[kSecUseAuthenticationContext as String] = ctx
        let status = SecItemCopyMatching(q as CFDictionary, nil)
        return status == errSecSuccess || status == errSecInteractionNotAllowed
    }

    static func delete() {
        SecItemDelete(base as CFDictionary)
    }
}
