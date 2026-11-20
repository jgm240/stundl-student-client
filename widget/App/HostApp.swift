import SwiftUI

// Minimal host app: a widget extension has to ship inside an app. Launch it once to register the widget.
@main
struct HostApp: App {
    var body: some Scene {
        WindowGroup {
            Text("SSC widget installed.\nAdd it from the desktop's widget gallery.")
                .multilineTextAlignment(.center).padding(40)
        }
    }
}
