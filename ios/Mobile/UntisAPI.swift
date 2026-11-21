import Foundation

/// The Untis Mobile login: user name + the per-user secret key (from the WebUntis QR code).
struct UntisConfig {
    let server: String
    let school: String
    let user: String
    let key: String
}

enum UntisError: Error, LocalizedError {
    case auth(String)
    case rpc(String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .auth(let m): return "WebUntis refused the login (\(m)). Was the QR code regenerated, or is this device's clock off?"
        case .rpc(let m): return m
        case .badResponse: return "Unexpected answer from WebUntis."
        }
    }
}

/// POST body -> response body. Injected so the client can be tested without a network.
typealias Transport = (URL, Data) async throws -> Data

enum Untis {
    static func rpcURL(server: String, school: String, method: String) -> URL? {
        var c = URLComponents()
        c.scheme = "https"
        c.host = server
        c.path = "/WebUntis/jsonrpc_intern.do"
        c.queryItems = [URLQueryItem(name: "m", value: method), URLQueryItem(name: "school", value: school),
                        URLQueryItem(name: "v", value: "i2.2")]
        return c.url
    }

    static func call(_ post: Transport, _ cfg: UntisConfig, _ method: String, _ params: [String: Any] = [:],
                     nowMillis: Int64 = Int64(Date().timeIntervalSince1970 * 1000)) async throws -> [String: Any] {
        guard let url = rpcURL(server: cfg.server, school: cfg.school, method: method) else { throw UntisError.badResponse }
        guard let secret = Base32.decode(cfg.key) else { throw UntisError.auth("invalid key") }
        var p = params
        p["auth"] = ["user": cfg.user, "otp": TOTP.code(secret: secret, atMillis: nowMillis), "clientTime": nowMillis] as [String: Any]
        let body = try JSONSerialization.data(withJSONObject: ["id": "ssc", "method": method, "jsonrpc": "2.0", "params": [p]] as [String: Any])
        let data = try await post(url, body)
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw UntisError.badResponse }
        if let err = obj["error"] as? [String: Any] {
            let msg = (err["message"] as? String) ?? "error"
            let code = (err["code"] as? NSNumber)?.intValue ?? 0
            if [-8520, -8504, -8500].contains(code) || msg.lowercased().contains("auth") || msg.lowercased().contains("login") {
                throw UntisError.auth(msg)
            }
            throw UntisError.rpc("\(method): \(msg)")
        }
        return obj["result"] as? [String: Any] ?? [:]
    }

    // MARK: conversions to SSC's Parent data format (same shape the bundled Parent page renders)

    private static func int(_ v: Any?) -> Int? { (v as? NSNumber)?.intValue }

    private static func hhmm(_ v: Any?) -> String? {
        guard let n = int(v) else { return nil }
        let s = String(format: "%04d", n)
        return "\(s.prefix(2)):\(s.suffix(2))"
    }

    private static func isoDate(_ v: Any?) -> String? {
        guard let n = int(v) else { return nil }
        let s = String(format: "%08d", n)
        return "\(s.prefix(4))-\(s.dropFirst(4).prefix(2))-\(s.suffix(2))"
    }

    private static func names(_ master: [String: Any], _ kind: String, _ refs: Any?) -> String {
        let table = Dictionary(uniqueKeysWithValues: ((master[kind] as? [[String: Any]]) ?? []).compactMap { x -> (Int, [String: Any])? in
            guard let id = int(x["id"]) else { return nil }
            return (id, x)
        })
        return ((refs as? [[String: Any]]) ?? []).compactMap { r -> String? in
            guard let id = int(r["id"]), let x = table[id] else { return nil }
            return (x["name"] as? String) ?? (x["longName"] as? String)
        }.joined(separator: ", ")
    }

    static func convertTimetable(_ result: [String: Any], master: [String: Any]) -> [[String: Any]] {
        let tt = (result["timetable"] as? [String: Any]) ?? result
        var out: [[String: Any]] = []
        for p in (tt["periods"] as? [[String: Any]]) ?? [] {
            guard let d = isoDate(p["date"]), let s = hhmm(p["startTime"]), let e = hhmm(p["endTime"]) else { continue }
            let state = ((p["cellState"] as? String) ?? "").uppercased()
            out.append(["date": d, "start": "\(d)T\(s)", "end": "\(d)T\(e)",
                        "subject": names(master, "subjects", p["su"]), "teacher": names(master, "teachers", p["te"]),
                        "room": names(master, "rooms", p["ro"]), "cancelled": state == "CANCEL" || state == "CANCELLED",
                        "info": (p["lstext"] as? String) ?? (p["substText"] as? String) ?? "",
                        "topic": (p["lstext"] as? String) ?? "", "substitution": (p["substText"] as? String) ?? ""])
        }
        return out.sorted { ($0["start"] as! String) < ($1["start"] as! String) }
    }

    private static func dayDiff(from today: Date, toISO due: String) -> Int? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        guard let d = f.date(from: due) else { return nil }
        let cal = Calendar.current
        return cal.dateComponents([.day], from: cal.startOfDay(for: today), to: cal.startOfDay(for: d)).day
    }

    /// done | missed | soon | pending - the same statuses SSC uses everywhere.
    static func convertHomework(_ result: [String: Any], today: Date, soonDays: Int = 2) -> [[String: Any]] {
        let lessons = Dictionary(uniqueKeysWithValues: ((result["lessons"] as? [[String: Any]]) ?? []).compactMap { l -> (Int, [String: Any])? in
            guard let id = int(l["id"]) else { return nil }
            return (id, l)
        })
        let raw = (result["homeWorks"] as? [[String: Any]]) ?? (result["homeworks"] as? [[String: Any]]) ?? (result["homework"] as? [[String: Any]]) ?? []
        var out: [[String: Any]] = []
        for h in raw {
            let les = int(h["lessonId"]).flatMap { lessons[$0] } ?? [:]
            guard let due = isoDate(h["dueDate"] ?? h["date"]) else { continue }
            let done = ((h["completed"] as? NSNumber)?.boolValue ?? false) || ((h["done"] as? NSNumber)?.boolValue ?? false)
            let days = dayDiff(from: today, toISO: due)
            let status: String
            if done { status = "done" }
            else if let d = days { status = d < 0 ? "missed" : (d <= soonDays ? "soon" : "pending") }
            else { status = "pending" }
            var item: [String: Any] = ["subject": (h["subject"] as? String) ?? (les["subject"] as? String) ?? "", "due": due,
                                       "text": (h["text"] as? String) ?? "", "remark": (h["remark"] as? String) ?? "",
                                       "done": done, "status": status]
            item["days_left"] = days.map { $0 as Any } ?? NSNull()
            out.append(item)
        }
        return out
    }

    static func convertExams(_ result: [String: Any]) -> [[String: Any]] {
        var out: [[String: Any]] = []
        for e in (result["exams"] as? [[String: Any]]) ?? [] {
            guard let d = isoDate(e["examDate"]) else { continue }
            out.append(["date": d, "start": hhmm(e["startTime"]) ?? "", "end": hhmm(e["endTime"]) ?? "",
                        "subject": (e["subject"] as? String) ?? (e["name"] as? String) ?? "",
                        "text": (e["text"] as? String) ?? (e["name"] as? String) ?? ""])
        }
        return out.sorted { ($0["date"] as! String) < ($1["date"] as! String) }
    }

    /// Everything the app shows, in the same shape as SSC's Parent mode data (so the bundled Parent page renders it).
    static func fetchAll(_ post: Transport, _ cfg: UntisConfig, today: Date = Date(), weeksBack: Int = 1, weeksAhead: Int = 4) async throws -> [String: Any] {
        let ud = try await call(post, cfg, "getUserData2017")          // an auth error surfaces here
        let master = (ud["masterData"] as? [String: Any]) ?? [:]
        let user = (ud["userData"] as? [String: Any]) ?? [:]
        var elem: [String: Any] = ["type": int(user["elemType"]) ?? 5]
        if let id = int(user["elemId"]) { elem["id"] = id }
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = Calendar.current.timeZone
        let monday = cal.date(from: cal.dateComponents([.yearForWeekOfYear, .weekOfYear], from: today)) ?? today
        let compact = DateFormatter()
        compact.locale = Locale(identifier: "en_US_POSIX")
        compact.dateFormat = "yyyyMMdd"
        let iso = DateFormatter()
        iso.locale = Locale(identifier: "en_US_POSIX")
        iso.dateFormat = "yyyy-MM-dd"
        var notes: [String] = []

        var timetable: [String: Any] = [:]
        for w in -weeksBack...weeksAhead {
            guard let start = cal.date(byAdding: .weekOfYear, value: w, to: monday), let end = cal.date(byAdding: .day, value: 6, to: start) else { continue }
            do {
                var p = elem
                p["startDate"] = Int(compact.string(from: start)) ?? 0
                p["endDate"] = Int(compact.string(from: end)) ?? 0
                timetable[iso.string(from: start)] = convertTimetable(try await call(post, cfg, "getTimetable2017", p), master: master)
            } catch UntisError.auth(let m) {
                throw UntisError.auth(m)
            } catch {
                notes.append("timetable \(iso.string(from: start)): \(error.localizedDescription)")
            }
        }
        var span = elem
        span["startDate"] = Int(compact.string(from: today.addingTimeInterval(-30 * 86400))) ?? 0
        span["endDate"] = Int(compact.string(from: today.addingTimeInterval(90 * 86400))) ?? 0
        var homework: [[String: Any]] = []
        var exams: [[String: Any]] = []
        do { homework = convertHomework(try await call(post, cfg, "getHomeWork2017", span), today: today) }
        catch UntisError.auth(let m) { throw UntisError.auth(m) }
        catch { notes.append("homework: \(error.localizedDescription)") }
        do { exams = convertExams(try await call(post, cfg, "getExams2017", span)) }
        catch UntisError.auth(let m) { throw UntisError.auth(m) }
        catch { notes.append("exams: \(error.localizedDescription)") }

        let now = Date().timeIntervalSince1970
        let keep = homework.filter { h in
            let done = (h["status"] as? String) == "done"
            let days = h["days_left"] as? Int
            return !done || days == nil || days! >= -1
        }
        return ["sections": ["today", "timetable", "homework", "exams"], "synced_at": now, "server_time": now,
                "timetable": timetable, "homework": keep, "exams": exams, "plan": [] as [Any], "changes": [] as [Any],
                "source": "direct", "notes": notes]
    }
}
