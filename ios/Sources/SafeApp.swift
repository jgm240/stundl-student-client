import SwiftUI

/// Stundl Safe Client (SSC-SAFE): only shows the read-only SSC "Parent mode" page and keeps a copy of its data.
/// It never asks for, stores or sends WebUntis credentials - the only secret it holds is the Parent link.
@main
struct SSCSafeApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
