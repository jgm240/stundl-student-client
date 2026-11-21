import SwiftUI

/// SSC Mobile: logs in to WebUntis by itself with the Untis Mobile key. No Mac, no password, no browser.
@main
struct SSCMobileApp: App {
    var body: some Scene {
        WindowGroup { MobileRoot() }
    }
}
