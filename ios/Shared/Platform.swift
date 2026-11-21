import Foundation
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

func clipboardString() -> String? {
    #if canImport(UIKit)
    return UIPasteboard.general.string
    #else
    return NSPasteboard.general.string(forType: .string)
    #endif
}

/// Wall-display use: keep the screen on.
func setKeepAwake(_ on: Bool) {
    #if canImport(UIKit)
    UIApplication.shared.isIdleTimerDisabled = on
    #endif
}

/// Don't leave secrets in the clipboard after pasting them.
func clearClipboard() {
    #if canImport(UIKit)
    UIPasteboard.general.string = ""
    #else
    NSPasteboard.general.clearContents()
    #endif
}
