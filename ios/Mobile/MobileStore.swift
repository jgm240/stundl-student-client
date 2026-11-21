import Foundation

/// Account (not secret), cached data (not secret) and the update logic. The key is only ever read from the Keychain
/// for the duration of one update.
@MainActor
final class MobileStore: ObservableObject {
    @Published private(set) var account: UntisAccount?
    @Published private(set) var json: String?
    @Published private(set) var fetchedAt: Date?
    @Published private(set) var offline = false
    @Published private(set) var locked = false
    @Published private(set) var message = ""
    @Published var wall: Bool {
        didSet { UserDefaults.standard.set(wall, forKey: "ssc.wall") }
    }

    private let cacheFile: URL
    private let session: URLSession
    private var updating = false

    init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SSC-Mobile", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        cacheFile = dir.appendingPathComponent("data.json")
        let cfg = URLSessionConfiguration.ephemeral          // no cookies, no shared cache, no stored credentials
        cfg.httpShouldSetCookies = false
        cfg.httpCookieAcceptPolicy = .never
        cfg.urlCredentialStorage = nil
        cfg.timeoutIntervalForRequest = 20
        session = URLSession(configuration: cfg)
        wall = UserDefaults.standard.bool(forKey: "ssc.wall")
        if let d = UserDefaults.standard.data(forKey: "ssc.account"), let a = try? JSONDecoder().decode(UntisAccount.self, from: d), SecretStore.exists() {
            account = a
        }
        if let d = try? Data(contentsOf: cacheFile), let s = String(data: d, encoding: .utf8) {
            json = s
            fetchedAt = (try? FileManager.default.attributesOfItem(atPath: cacheFile.path))?[.modificationDate] as? Date
        }
    }

    func connect(_ payload: QRPayload) throws {
        try SecretStore.save(key: payload.key)
        account = payload.account
        UserDefaults.standard.set(try? JSONEncoder().encode(payload.account), forKey: "ssc.account")
        message = ""
    }

    func disconnect() {
        SecretStore.delete()
        UserDefaults.standard.removeObject(forKey: "ssc.account")
        try? FileManager.default.removeItem(at: cacheFile)
        account = nil
        json = nil
        fetchedAt = nil
        offline = false
        locked = false
    }

    private static func transport(_ session: URLSession) -> Transport {
        return { url, body in
            var r = URLRequest(url: url)
            r.httpMethod = "POST"
            r.httpBody = body
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpShouldHandleCookies = false
            let (data, resp) = try await session.data(for: r)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            return data
        }
    }

    func refresh() async {
        guard let account, !updating else { return }
        updating = true
        defer { updating = false }
        // 1. unlock the key (Face ID / passcode) - off the main thread
        let unlocked = await Task.detached { SecretStore.load(reason: "Unlock your WebUntis key to update your timetable") }.value
        let key: String
        switch unlocked {
        case .key(let k): key = k
        case .cancelled: locked = true; return
        case .missing: message = "The key is gone (device passcode removed?). Scan the WebUntis QR code again."; return
        case .failed(let s): message = "Couldn't read the key (error \(s))."; return
        }
        locked = false
        // 2. fetch; the key only lives in this scope
        let cfg = UntisConfig(server: account.server, school: account.school, user: account.user, key: key)
        do {
            let dict = try await Untis.fetchAll(Self.transport(session), cfg)
            let data = try JSONSerialization.data(withJSONObject: dict)
            try data.write(to: cacheFile, options: [.atomic, .completeFileProtection])
            json = String(data: data, encoding: .utf8)
            fetchedAt = Date()
            offline = false
            message = ""
        } catch UntisError.auth(let m) {
            message = UntisError.auth(m).localizedDescription
        } catch {
            offline = true    // keep showing the cached copy
        }
    }
}
