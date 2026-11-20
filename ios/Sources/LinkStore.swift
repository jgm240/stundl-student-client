import Foundation
import Security

/// The secret SSC Parent link, e.g. http://192.168.1.20:8766/p/<code>/  (add "wall" for the wall-display view).
struct ParentLink: Equatable {
    let base: URL          // scheme://host:port/p/<code>/
    var wall: Bool

    var dataURL: URL { base.appendingPathComponent("data.json") }
    var stored: String { base.absoluteString + (wall ? "wall" : "") }

    static func parse(_ text: String) -> ParentLink? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let comps = URLComponents(string: t), let scheme = comps.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = comps.host, !host.isEmpty else { return nil }
        let parts = comps.path.split(separator: "/").map(String.init)
        guard parts.count >= 2, parts[0] == "p", parts[1].count >= 20,
              parts[1].range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { return nil }
        // Same rule as the Mac side: plain http only for addresses on your own network.
        guard scheme == "https" || isHomeNetwork(host) else { return nil }
        var b = URLComponents()
        b.scheme = scheme; b.host = host; b.port = comps.port; b.path = "/p/\(parts[1])/"
        guard let url = b.url else { return nil }
        return ParentLink(base: url, wall: parts.count >= 3 && parts[2] == "wall")
    }

    static func isHomeNetwork(_ host: String) -> Bool {
        let h = host.lowercased()
        if h == "localhost" || h.hasSuffix(".local") { return true }
        let n = h.split(separator: ".").compactMap { Int($0) }
        guard n.count == 4, n.allSatisfy({ (0...255).contains($0) }) else { return false }
        return n[0] == 10 || n[0] == 127 || (n[0] == 192 && n[1] == 168) || (n[0] == 172 && (16...31).contains(n[1])) || (n[0] == 169 && n[1] == 254)
    }
}

/// Keeps the link in the Keychain (this device only), not in plain preferences.
enum LinkStore {
    private static let service = "com.stundl.ssc.safe"
    private static let account = "parent-link"

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    static func save(_ link: ParentLink) {
        delete()
        var q = query
        q[kSecValueData as String] = Data(link.stored.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }

    static func load() -> ParentLink? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data,
              let s = String(data: data, encoding: .utf8) else { return nil }
        return ParentLink.parse(s)
    }

    static func delete() { SecItemDelete(query as CFDictionary) }
}
