// Self-check for the SSC Mobile logic (no iOS needed): swiftc ios/Mobile/TOTP.swift ios/Mobile/UntisAPI.swift ios/Tools/main.swift -o /tmp/ssc-selfcheck
import Foundation

var failures = 0
func check(_ name: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "PASS " : "FAIL ") + name + (detail.isEmpty ? "" : "  " + detail))
    if !ok { failures += 1 }
}

// --- TOTP against the RFC 6238 vectors (secret "12345678901234567890", SHA-1, 6 digits)
let secret = Base32.decode("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")!
check("RFC6238 T=59", TOTP.code(secret: secret, atMillis: 59_000) == "287082")
check("RFC6238 T=1111111109", TOTP.code(secret: secret, atMillis: 1_111_111_109_000) == "081804")
check("RFC6238 T=1234567890", TOTP.code(secret: secret, atMillis: 1_234_567_890_000) == "005924")
check("base32 lowercase/spaces", Base32.decode("gezd gnbv gy3t qojq gezd gnbv gy3t qojq") == secret)
check("base32 rejects junk", Base32.decode("not base32!") == nil)

// --- fake WebUntis (same shapes as the Python tests)
var cal = Calendar.current
let today = cal.date(from: DateComponents(year: 2026, month: 11, day: 20))!     // a Friday
let monday = cal.date(byAdding: .day, value: -4, to: today)!
func ymd(_ d: Date) -> Int { let c = cal.dateComponents([.year, .month, .day], from: d); return c.year! * 10000 + c.month! * 100 + c.day! }
func plus(_ n: Int) -> Int { ymd(cal.date(byAdding: .day, value: n, to: today)!) }
var authOK = true
var failMethod = ""
var methods = Set<String>()

let post: Transport = { url, body in
    let q = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
    let m = q.first { $0.name == "m" }!.value!
    methods.insert(m)
    let req = try JSONSerialization.jsonObject(with: body) as! [String: Any]
    let p = (req["params"] as! [[String: Any]])[0]
    let auth = p["auth"] as! [String: Any]
    precondition(auth["user"] as? String == "max" && (auth["otp"] as? String)?.count == 6)
    func reply(_ r: [String: Any]) -> Data { try! JSONSerialization.data(withJSONObject: ["result": r]) }
    if !authOK { return try! JSONSerialization.data(withJSONObject: ["error": ["code": -8520, "message": "bad credentials"]]) }
    if failMethod == m { return try! JSONSerialization.data(withJSONObject: ["error": ["code": -32601, "message": "Method not found"]]) }
    switch m {
    case "getUserData2017":
        return reply(["userData": ["elemId": 42, "elemType": 5],
                      "masterData": ["subjects": [["id": 1, "name": "M"], ["id": 2, "name": "D"]], "teachers": [["id": 7, "name": "AB"]], "rooms": [["id": 9, "name": "R1"]]]])
    case "getTimetable2017":
        if (p["startDate"] as! Int) != ymd(monday) { return reply(["timetable": ["periods": [] as [Any]]]) }
        return reply(["timetable": ["periods": [
            ["date": plus(0), "startTime": 800, "endTime": 850, "su": [["id": 1]], "te": [["id": 7]], "ro": [["id": 9]], "cellState": "STANDARD"],
            ["date": plus(0), "startTime": 900, "endTime": 950, "su": [["id": 2]], "te": [["id": 7]], "ro": [["id": 9]], "cellState": "CANCEL"],
            ["broken": true]]]])
    case "getHomeWork2017":
        return reply(["lessons": [["id": 5, "subject": "D"]], "homeWorks": [
            ["id": 1, "lessonId": 5, "dueDate": plus(-1), "text": "Essay", "completed": false],
            ["id": 2, "lessonId": 5, "dueDate": plus(1), "text": "Vokabeln", "completed": false],
            ["id": 3, "lessonId": 5, "dueDate": plus(9), "text": "Referat", "completed": false],
            ["id": 5, "lessonId": 5, "dueDate": plus(-40), "text": "uralt", "completed": true]]])
    case "getExams2017":
        return reply(["exams": [["id": 7, "examDate": plus(5), "startTime": 800, "endTime": 900, "subject": "Mathe", "name": "SA"]]])
    default: return reply([:])
    }
}
let cfg = UntisConfig(server: "mese.webuntis.com", school: "my-school", user: "max", key: "GEZDGNBVGY3TQOJQ")

let sem = DispatchSemaphore(value: 0)
Task {
    do {
        let data = try await Untis.fetchAll(post, cfg, today: today)
        let tt = (data["timetable"] as! [String: Any])
        let iso = DateFormatter(); iso.locale = Locale(identifier: "en_US_POSIX"); iso.dateFormat = "yyyy-MM-dd"
        let week = tt[iso.string(from: monday)] as! [[String: Any]]
        check("6 weeks fetched", tt.count == 6, "\(tt.count)")
        check("lessons (broken period skipped, cancelled flagged)", week.count == 2 && (week[0]["subject"] as? String) == "M" && (week[1]["cancelled"] as? Bool) == true && (week[0]["teacher"] as? String) == "AB" && (week[0]["room"] as? String) == "R1")
        let hw = Dictionary(uniqueKeysWithValues: (data["homework"] as! [[String: Any]]).map { ($0["text"] as! String, $0["status"] as! String) })
        check("homework statuses", hw == ["Essay": "missed", "Vokabeln": "soon", "Referat": "pending"], "\(hw)")
        check("exams", ((data["exams"] as! [[String: Any]])[0]["subject"] as? String) == "Mathe")
        check("same shape as the Parent data", (data["sections"] as! [String]).contains("homework") && data["source"] as? String == "direct" && (data["synced_at"] as? Double) != nil)
        check("a Parent-page JSON can be produced", (try? JSONSerialization.data(withJSONObject: data)) != nil)
        failMethod = "getHomeWork2017"
        let partial = try await Untis.fetchAll(post, cfg, today: today)
        check("one failing part doesn't break the rest", (partial["homework"] as! [Any]).isEmpty && !(partial["exams"] as! [Any]).isEmpty && !((partial["notes"] as! [String]).isEmpty))
        failMethod = ""; authOK = false
        do { _ = try await Untis.fetchAll(post, cfg, today: today); check("wrong key -> auth error", false) }
        catch UntisError.auth { check("wrong key -> auth error", true) }
        catch { check("wrong key -> auth error", false, "\(error)") }
        check("all four WebUntis methods used", methods == ["getUserData2017", "getTimetable2017", "getHomeWork2017", "getExams2017"])
    } catch { check("unexpected error", false, "\(error)") }
    sem.signal()
}
sem.wait()
print(failures == 0 ? "ALL CHECKS PASSED" : "\(failures) CHECK(S) FAILED")
exit(failures == 0 ? 0 : 1)
