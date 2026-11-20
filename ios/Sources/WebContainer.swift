import SwiftUI
import WebKit

/// What the page needs: the cached/fresh data, whether it is stale (offline), and the view mode.
struct Payload: Equatable {
    var json: String?
    var offline: Bool
    var fetchedAt: TimeInterval
    var wall: Bool
}

/// Shows the Parent page bundled inside the app (so it works offline). The page never touches the network: the app
/// fetches the data and pushes it in. The web view may not navigate anywhere except the bundled page.
final class WebCoordinator: NSObject, WKNavigationDelegate {
    var last: Payload?

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let p = last { push(p, to: webView) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
    }

    func push(_ p: Payload, to web: WKWebView) {
        guard let json = p.json else { return }
        web.evaluateJavaScript("window.SSC_update && window.SSC_update(\(json), \(p.offline), \(Int(p.fetchedAt * 1000)))", completionHandler: nil)
    }
}

private func makeWebView(wall: Bool, coordinator: WebCoordinator) -> WKWebView {
    let config = WKWebViewConfiguration()
    config.websiteDataStore = .nonPersistent()
    let script = WKUserScript(source: "window.SSC_NATIVE = true; window.SSC_WALL = \(wall);",
                              injectionTime: .atDocumentStart, forMainFrameOnly: true)
    config.userContentController.addUserScript(script)
    let web = WKWebView(frame: .zero, configuration: config)
    web.navigationDelegate = coordinator
    if let url = Bundle.main.url(forResource: "parent", withExtension: "html") {
        web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }
    return web
}

#if canImport(UIKit)
struct WebContainer: UIViewRepresentable {
    let payload: Payload
    func makeCoordinator() -> WebCoordinator { WebCoordinator() }
    func makeUIView(context: Context) -> WKWebView {
        let web = makeWebView(wall: payload.wall, coordinator: context.coordinator)
        web.isOpaque = false
        web.backgroundColor = .clear
        return web
    }
    func updateUIView(_ web: WKWebView, context: Context) {
        if context.coordinator.last != payload {
            context.coordinator.last = payload
            context.coordinator.push(payload, to: web)
        }
    }
}
#else
struct WebContainer: NSViewRepresentable {
    let payload: Payload
    func makeCoordinator() -> WebCoordinator { WebCoordinator() }
    func makeNSView(context: Context) -> WKWebView { makeWebView(wall: payload.wall, coordinator: context.coordinator) }
    func updateNSView(_ web: WKWebView, context: Context) {
        if context.coordinator.last != payload {
            context.coordinator.last = payload
            context.coordinator.push(payload, to: web)
        }
    }
}
#endif
