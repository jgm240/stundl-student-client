import SwiftUI

struct ContentView: View {
    @StateObject private var store = DataStore()
    @State private var link: ParentLink? = LinkStore.load()
    @State private var showSettings = false

    var body: some View {
        Group {
            if let link {
                SafeView(link: link, store: store, showSettings: $showSettings)
            } else {
                SetupView { new in
                    LinkStore.save(new)
                    link = new
                }
            }
        }
        .sheet(isPresented: $showSettings) {
            SettingsView(link: $link, store: store)
        }
    }
}

struct SafeView: View {
    let link: ParentLink
    @ObservedObject var store: DataStore
    @Binding var showSettings: Bool

    var body: some View {
        ZStack(alignment: .topTrailing) {
            if store.json == nil {
                VStack(spacing: 8) {
                    Text(store.offline ? "Can't reach your Mac yet." : "Loading…").font(.headline)
                    Text("Make sure you're on your home Wi-Fi and Parent mode is turned on.").font(.footnote).foregroundStyle(.secondary)
                }.padding()
            }
            WebContainer(payload: Payload(json: store.json, offline: store.offline,
                                          fetchedAt: store.fetchedAt?.timeIntervalSince1970 ?? 0, wall: link.wall))
                .id(link.wall)
            Button { showSettings = true } label: {
                Image(systemName: "gearshape.fill").padding(10).background(.ultraThinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .padding(12)
        }
        .task(id: link) {
            while !Task.isCancelled {
                await store.refresh(link)
                try? await Task.sleep(nanoseconds: 60_000_000_000)
            }
        }
        .onAppear { setKeepAwake(link.wall) }
        .onChange(of: link.wall) { setKeepAwake($0) }
    }
}

struct SetupView: View {
    let onConnect: (ParentLink) -> Void
    @State private var text = ""
    @State private var error = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Stundl Safe Client").font(.largeTitle.bold())
            Text("SSC-SAFE shows the read-only Parent page from your Mac and keeps a copy of the last data on this device. It never asks for or sends WebUntis credentials - the only thing you enter is the secret Parent link.")
                .foregroundStyle(.secondary)
            Text("On your Mac: SSC → Settings → Parent mode → Copy link, then paste it here.").font(.footnote)
            TextField("http://192.168.…:8766/p/…", text: $text)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
            HStack {
                Button("Paste") { text = clipboardString() ?? text }
                Button("Connect") {
                    if let l = ParentLink.parse(text) { error = ""; onConnect(l) }
                    else { error = "That isn't an SSC Parent link (it should look like http://192.168.…:8766/p/<code>/ on your home network)." }
                }.buttonStyle(.borderedProminent)
            }
            if !error.isEmpty { Text(error).foregroundStyle(.red).font(.footnote) }
            Spacer()
        }
        .padding(24)
    }
}

struct SettingsView: View {
    @Binding var link: ParentLink?
    @ObservedObject var store: DataStore
    @Environment(\.dismiss) private var dismiss

    private var wallBinding: Binding<Bool> {
        Binding(get: { link?.wall ?? false },
                set: { new in
                    if var l = link { l.wall = new; LinkStore.save(l); link = l }
                })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("View") {
                    Picker("Layout", selection: wallBinding) {
                        Text("Overview").tag(false)
                        Text("Wall display").tag(true)
                    }
                    .pickerStyle(.segmented)
                }
                Section("Data") {
                    Text(store.fetchedAt.map { "Last updated " + $0.formatted(date: .abbreviated, time: .shortened) } ?? "Nothing cached yet")
                    if store.offline { Text("Offline - showing the cached copy").foregroundStyle(.orange) }
                    Button("Refresh now") { if let l = link { Task { await store.refresh(l) } } }
                    Button("Remove link and cached data", role: .destructive) {
                        LinkStore.delete(); store.clear(); link = nil; dismiss()
                    }
                }
                Section("About") {
                    Text("Stundl Safe Client (SSC-SAFE). It only receives the read-only Parent page data from your own Mac. No credentials are sent, and nothing leaves your home network.")
                        .font(.footnote)
                    Text("Unofficial; not affiliated with Untis GmbH or Microsoft.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("SSC-SAFE")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}
