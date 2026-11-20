import SwiftUI
import WidgetKit

// Reads widget.json from this extension's own sandbox container (Application Support). The Python side
// (`ssc.py sync` / `widget`) writes it there, so no App Group or ID setup is needed.

struct Lesson: Decodable, Identifiable {
    var id: String { start + subject }
    let start: String, end: String, subject: String, room: String, cancelled: Bool
}
struct Payload: Decodable {
    let title: String
    let alerts: [String]
    let homework: [String]
    let lessons: [Lesson]
}
struct Entry: TimelineEntry { let date: Date; let payload: Payload? }

func loadPayload() -> Payload? {
    guard let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first,
          let data = try? Data(contentsOf: dir.appendingPathComponent("widget.json")) else { return nil }
    return try? JSONDecoder().decode(Payload.self, from: data)
}

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: Date(), payload: nil) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
        completion(Entry(date: Date(), payload: loadPayload()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        let now = Date()
        completion(Timeline(entries: [Entry(date: now, payload: loadPayload())],
                            policy: .after(now.addingTimeInterval(15 * 60))))
    }
}

struct WidgetView: View {
    let entry: Entry
    @Environment(\.widgetFamily) var family

    func time(_ s: String) -> String { String(s.dropFirst(11).prefix(5)) }

    var body: some View {
        if let p = entry.payload {
            VStack(alignment: .leading, spacing: 4) {
                Text(p.title).font(.headline)
                ForEach(p.lessons.prefix(family == .systemSmall ? 3 : 6)) { l in
                    HStack {
                        Text(time(l.start)).monospacedDigit().foregroundStyle(.secondary)
                        Text(l.subject).strikethrough(l.cancelled)
                        Spacer()
                        Text(l.room).foregroundStyle(.secondary)
                    }.font(.caption)
                }
                if family != .systemSmall, let a = p.alerts.first {
                    Text(a).font(.caption2).foregroundStyle(.red)
                } else if family != .systemSmall, let h = p.homework.first {
                    Text("📚 " + h).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        } else {
            Text("No data yet.\nRun: ssc.py widget").font(.caption)
        }
    }
}

@main
struct SSCWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "SSCWidget", provider: Provider()) { entry in
            WidgetView(entry: entry).containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("SSC")
        .description("Next lessons, alerts and homework.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}
