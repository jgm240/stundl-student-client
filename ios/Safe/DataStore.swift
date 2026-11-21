import Foundation

/// Fetches the Parent data (read-only JSON) and keeps the last good copy on the device for offline use.
@MainActor
final class DataStore: ObservableObject {
    @Published private(set) var json: String?
    @Published private(set) var fetchedAt: Date?
    @Published private(set) var offline = false

    private let file: URL
    private let session: URLSession

    init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SSC-SAFE", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        file = dir.appendingPathComponent("parent-data.json")
        let cfg = URLSessionConfiguration.ephemeral            // no cookies, no shared cache, no credentials
        cfg.httpShouldSetCookies = false
        cfg.httpCookieAcceptPolicy = .never
        cfg.urlCredentialStorage = nil
        cfg.timeoutIntervalForRequest = 8
        session = URLSession(configuration: cfg)
        loadCached()
    }

    func loadCached() {
        guard let d = try? Data(contentsOf: file), let s = String(data: d, encoding: .utf8) else { return }
        json = s
        fetchedAt = (try? FileManager.default.attributesOfItem(atPath: file.path))?[.modificationDate] as? Date
    }

    func clear() {
        try? FileManager.default.removeItem(at: file)
        json = nil
        fetchedAt = nil
    }

    /// Sends one GET to the Parent link's data.json - nothing else is ever sent.
    func refresh(_ link: ParentLink) async {
        var req = URLRequest(url: link.dataURL)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.httpShouldHandleCookies = false
        do {
            let (data, resp) = try await session.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200, data.count < 2_000_000,
                  (try? JSONSerialization.jsonObject(with: data)) is [String: Any],
                  let s = String(data: data, encoding: .utf8) else { throw URLError(.badServerResponse) }
            try data.write(to: file, options: .atomic)
            json = s
            fetchedAt = Date()
            offline = false
        } catch {
            offline = true   // keep showing the cached copy
        }
    }
}
