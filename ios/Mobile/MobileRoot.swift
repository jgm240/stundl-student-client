import SwiftUI

struct MobileRoot: View {
    @StateObject private var store = MobileStore()
    @Environment(\.scenePhase) private var phase
    @State private var showSettings = false

    var body: some View {
        Group {
            if store.account != nil {
                MainView(store: store, showSettings: $showSettings)
            } else {
                SetupView(store: store)
            }
        }
        .sheet(isPresented: $showSettings) { MobileSettings(store: store) }
        .onChange(of: phase) { newPhase in
            if newPhase == .active { Task { await store.refresh() } }
        }
    }
}

struct MainView: View {
    @ObservedObject var store: MobileStore
    @Binding var showSettings: Bool

    var body: some View {
        ZStack(alignment: .topTrailing) {
            if store.json == nil {
                VStack(spacing: 8) {
                    Text(store.locked ? "Locked" : (store.offline ? "Can't reach WebUntis yet." : "Loading…")).font(.headline)
                    if !store.message.isEmpty { Text(store.message).font(.footnote).foregroundStyle(.secondary) }
                }.padding()
            }
            WebContainer(payload: Payload(json: store.json, offline: store.offline,
                                          fetchedAt: store.fetchedAt?.timeIntervalSince1970 ?? 0, wall: store.wall))
                .id(store.wall)
            VStack(alignment: .trailing, spacing: 8) {
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape.fill").padding(10).background(.ultraThinMaterial, in: Circle())
                }.buttonStyle(.plain)
                if store.locked {
                    Button("Unlock") { Task { await store.refresh() } }.buttonStyle(.borderedProminent)
                }
                if !store.message.isEmpty && store.json != nil {
                    Text(store.message).font(.caption).padding(8).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
                        .frame(maxWidth: 260, alignment: .trailing)
                }
            }.padding(12)
        }
        .task {
            while !Task.isCancelled {
                await store.refresh()
                try? await Task.sleep(nanoseconds: 30 * 60 * 1_000_000_000)
            }
        }
        .onAppear { setKeepAwake(store.wall) }
        .onChange(of: store.wall) { setKeepAwake($0) }
    }
}

struct SetupView: View {
    @ObservedObject var store: MobileStore
    @State private var text = ""
    @State private var error = ""
    @State private var scanning = false

    private func accept(_ raw: String) {
        guard let payload = QRPayload.parse(raw) else {
            error = "That isn't a WebUntis Untis Mobile QR code (it should start with untis://setschool?…)."
            return
        }
        do {
            try store.connect(payload)
            text = ""
            clearClipboard()   // the key must not linger in the clipboard
            Task { await store.refresh() }
        } catch {
            self.error = error.localizedDescription
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("SSC Mobile").font(.largeTitle.bold())
                Text("Shows your WebUntis timetable, homework and exams. It logs in by itself with the Untis Mobile key - no password, no Mac needed.")
                    .foregroundStyle(.secondary)
                Group {
                    Text("1. In WebUntis (on a computer): your name → Profile → Freigaben / Access → Untis Mobile → show the QR code.")
                    Text("2. Scan it here.")
                }.font(.footnote)
                Button("Scan QR code") { scanning = true }.buttonStyle(.borderedProminent)
                Text("…or paste the text of the QR code:").font(.footnote)
                SecureField("untis://setschool?…", text: $text)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                HStack {
                    Button("Paste") { text = clipboardString() ?? text }
                    Button("Connect") { accept(text) }
                }
                if !error.isEmpty { Text(error).foregroundStyle(.red).font(.footnote) }
                Text("The key is stored only in this device's Keychain, protected by Face ID / your passcode. It can't be shown or exported, and it never leaves the device (not in iCloud, not in backups).")
                    .font(.footnote).foregroundStyle(.secondary)
            }.padding(24)
        }
        .sheet(isPresented: $scanning) {
            VStack {
                if QRScannerView.isAvailable {
                    QRScannerView { code in scanning = false; accept(code) }
                } else {
                    Text("Camera scanning isn't available here - paste the QR text instead.").padding()
                }
                Button("Cancel") { scanning = false }.padding()
            }
        }
    }
}

struct MobileSettings: View {
    @ObservedObject var store: MobileStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("View") {
                    Picker("Layout", selection: $store.wall) {
                        Text("Overview").tag(false)
                        Text("Wall display").tag(true)
                    }.pickerStyle(.segmented)
                }
                Section("Account") {
                    if let a = store.account { Text("\(a.user) · \(a.school)") }
                    Text(store.fetchedAt.map { "Last updated " + $0.formatted(date: .abbreviated, time: .shortened) } ?? "Nothing cached yet")
                    if store.offline { Text("Offline - showing the cached copy").foregroundStyle(.orange) }
                    Button("Update now") { Task { await store.refresh() } }
                    Button("Remove account and key", role: .destructive) {
                        store.disconnect()
                        dismiss()
                    }
                }
                Section("About") {
                    Text("SSC Mobile (Stundl Student Client). The Untis Mobile key is kept in the iOS Keychain, this device only, behind Face ID / passcode. There is no way to display or export it. To revoke it, generate a new QR code in WebUntis.")
                        .font(.footnote)
                    Text("Unofficial; not affiliated with Untis GmbH.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("SSC Mobile")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}
