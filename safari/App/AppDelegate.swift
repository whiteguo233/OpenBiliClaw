//
//  AppDelegate.swift
//  OpenBiliClaw
//
//  Host app for the OpenBiliClaw Safari Web Extension.
//
//  The host app's only job is to (a) be installable on macOS so its
//  embedded extension shows up in Safari's Extensions list, and (b)
//  give the user a one-tap way to enable it.
//

import Cocoa

@main
class AppDelegate: NSObject, NSApplicationDelegate {

    @IBOutlet var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // No-op. The window is wired up via Main.storyboard.
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    // MARK: - About panel

    /// Wired from the "About OpenBiliClaw" menu item in Main.storyboard.
    ///
    /// The host app's `CFBundleShortVersionString` is what Apple cares
    /// about, but the *useful* version for users is the extension's
    /// `manifest.json` version — that's what shipping JS code reports.
    /// They should match in normal releases, but the extension version
    /// is bumped much more often and can drift, so we surface both:
    ///
    ///   "OpenBiliClaw
    ///    扩展 v0.3.44 · 宿主 v0.3.44"
    ///
    /// Falls back gracefully if the manifest can't be located (e.g. the
    /// .appex hasn't been built yet, or the layout changes).
    @IBAction func showAboutPanel(_ sender: Any?) {
        let app = NSApplication.shared
        var options: [NSApplication.AboutPanelOptionKey: Any] = [:]

        let extensionVersion = readEmbeddedExtensionVersion()
        let hostVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"

        // ApplicationVersion is the bold line under the app name. We
        // surface the extension version there since that's what users
        // actually interact with.
        if let ev = extensionVersion {
            options[.applicationVersion] = "扩展 v\(ev)"
            // .version is the small parenthesized "build" string.
            options[.version] = "宿主 v\(hostVersion)"
            if ev != hostVersion {
                // Visible mismatch warning — usually means someone bumped
                // extension/manifest.json without re-running xcodegen.
                options[.version] = "宿主 v\(hostVersion) ⚠️ 版本不一致"
            }
        } else {
            // Couldn't find the embedded manifest — fall back to just
            // the host version so the panel still works.
            options[.applicationVersion] = "v\(hostVersion)"
        }

        options[.credits] = aboutCredits()

        app.orderFrontStandardAboutPanel(options: options)
        // Standard about panel is brought up behind the main window on
        // first invocation under some macOS versions; force it forward.
        app.activate(ignoringOtherApps: true)
    }

    /// Locate the embedded WebExtension's `manifest.json` and pull
    /// the `version` field out. The .appex lives at
    /// `Contents/PlugIns/OpenBiliClaw Extension.appex` inside the host
    /// bundle, and its resources are flattened directly into
    /// `Contents/Resources/manifest.json` (see project.yml's post-build
    /// script).
    private func readEmbeddedExtensionVersion() -> String? {
        guard let pluginsURL = Bundle.main.builtInPlugInsURL else { return nil }
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(at: pluginsURL,
                                                       includingPropertiesForKeys: nil) else {
            return nil
        }
        // We only ship one .appex but match defensively in case Apple
        // adds another extension target down the line.
        for url in entries where url.pathExtension == "appex" {
            let manifestURL = url
                .appendingPathComponent("Contents")
                .appendingPathComponent("Resources")
                .appendingPathComponent("manifest.json")
            guard let data = try? Data(contentsOf: manifestURL),
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let version = json["version"] as? String else {
                continue
            }
            return version
        }
        return nil
    }

    private func aboutCredits() -> NSAttributedString {
        // NSAboutPanel renders .credits as RTF. We hand it a small
        // attributed string so we don't need a separate Credits.rtf in
        // the bundle.
        let body = """
        跨平台内容发现 AI Agent
        行为采集 · 画像 · 智能推荐

        Source: github.com/whiteguo233/OpenBiliClaw
        License: see LICENSE in the repository
        """
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.secondaryLabelColor,
        ]
        return NSAttributedString(string: body, attributes: attrs)
    }
}
