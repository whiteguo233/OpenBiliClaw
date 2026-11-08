import AppKit
import Foundation
import Security
import ServiceManagement

private func text(_ key: String) -> String { NSLocalizedString(key, comment: "") }

@main
final class StoreApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var statusItem: NSStatusItem!
    private let stateLabel = NSTextField(wrappingLabelWithString: "")
    private let addressLabel = NSTextField(labelWithString: "")
    private let loginButton = NSButton(checkboxWithTitle: "", target: nil, action: nil)
    private let openButton = NSButton(title: "", target: nil, action: nil)
    private let serviceButton = NSButton(title: "", target: nil, action: nil)
    private var backend: Process?
    private var lifetimePipe: Pipe?
    private var timer: Timer?
    private var root: URL!
    private var webURL: URL?
    private var terminating = false
    private var stopping = false
    private var arguments = ProcessInfo.processInfo.arguments
    private lazy var bundleSignatureValid: Bool = {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(Bundle.main.bundleURL as CFURL, [], &code) == errSecSuccess,
              let code else { return false }
        let flags = SecCSFlags(rawValue: kSecCSCheckNestedCode | kSecCSStrictValidate)
        return SecStaticCodeCheckValidity(code, flags, nil) == errSecSuccess
    }()

    static func main() {
        let app = NSApplication.shared
        let delegate = StoreApp()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }

    private func argument(_ flag: String) -> String? {
        guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else { return nil }
        return arguments[index + 1]
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            root = try FileManager.default.url(for: .applicationSupportDirectory,
                in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("OpenBiliClaw", isDirectory: true)
            // QA profiles can only be children of this app's own container.
            if let name = argument("--qa-profile") {
                guard name.range(of: "^[A-Za-z0-9_-]{1,64}$", options: .regularExpression) != nil else {
                    throw NSError(domain: "OpenBiliClaw.QA", code: 1,
                        userInfo: [NSLocalizedDescriptionKey: "Invalid QA profile name"])
                }
                root.appendPathComponent("Testing/" + name, isDirectory: true)
            }
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            configureWindow()
            configureMenu()
            if arguments.contains("--qa-native-selftest"), argument("--qa-profile") != nil {
                let probe = Process()
                probe.executableURL = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/openbiliclaw-tailnet-helper")
                probe.arguments = ["--self-test"]
                probe.standardOutput = FileHandle.standardOutput
                probe.standardError = FileHandle.standardError
                try probe.run()
                probe.waitUntilExit()
                FileHandle.standardOutput.write(Data("OBC_QA_TAILNET_EXIT=\(probe.terminationStatus)\n".utf8))
            }
            startBackend()
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
            if let seconds = argument("--quit-after").flatMap(Double.init), seconds > 0 {
                Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { _ in NSApp.terminate(nil) }
            }
            if !arguments.contains("--headless") { showWindow() }
            if arguments.contains("--qa-ui-state"), argument("--qa-profile") != nil {
                Timer.scheduledTimer(withTimeInterval: 6, repeats: true) { [weak self] _ in
                    guard let self = self, let view = self.window.contentView else { return }
                    view.layoutSubtreeIfNeeded()
                    let buttons: [NSButton] = [self.openButton, self.serviceButton, self.loginButton]
                    let controls = buttons.map { button in
                        ["title": button.title, "enabled": button.isEnabled,
                         "inside_window": view.bounds.contains(view.convert(button.bounds, from: button)),
                         "has_action": button.action != nil] as [String: Any]
                    }
                    if let data = try? JSONSerialization.data(withJSONObject: ["controls": controls]),
                       let json = String(data: data, encoding: .utf8) {
                        FileHandle.standardOutput.write(Data(("OBC_QA_UI_STATE=" + json + "\n").utf8))
                    }
                }
            }
        } catch { showError(error) }
    }

    private func configureWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 450),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "OpenBiliClaw"
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        let title = NSTextField(labelWithString: text("Your Mac, your backend"))
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        let intro = NSTextField(wrappingLabelWithString: text("Connect your browser extension and phone to this Mac. Closing this window keeps the backend in the menu bar; Quit stops its services."))
        intro.textColor = .secondaryLabelColor
        stateLabel.font = .systemFont(ofSize: 14, weight: .medium)
        stateLabel.setAccessibilityIdentifier("backendStatus")
        addressLabel.isSelectable = true
        addressLabel.font = .monospacedSystemFont(ofSize: 13, weight: .regular)
        addressLabel.setAccessibilityIdentifier("backendAddress")
        openButton.title = text("Open Web & connection QR")
        openButton.target = self
        openButton.action = #selector(openWeb)
        openButton.bezelStyle = .rounded
        openButton.keyEquivalent = "\r"
        openButton.image = NSImage(systemSymbolName: "arrow.up.forward.app", accessibilityDescription: nil)
        openButton.imagePosition = .imageLeading
        openButton.setAccessibilityIdentifier("openWeb")
        serviceButton.target = self
        serviceButton.action = #selector(toggleService)
        serviceButton.bezelStyle = .rounded
        let actions = NSStackView(views: [openButton, serviceButton])
        actions.spacing = 10
        loginButton.title = text("Start at login")
        loginButton.target = self
        loginButton.action = #selector(toggleLogin)
        loginButton.setAccessibilityIdentifier("startAtLogin")
        let loginNote = NSTextField(wrappingLabelWithString: text("Optional. macOS controls login approval in System Settings → General → Login Items."))
        loginNote.font = .systemFont(ofSize: 11)
        loginNote.textColor = .secondaryLabelColor
        let logs = NSButton(title: text("Show logs"), target: self, action: #selector(showLogs))
        logs.bezelStyle = .rounded
        for view in [title, intro, stateLabel, addressLabel, actions, loginButton, loginNote, logs] { stack.addArrangedSubview(view) }
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 24),
        ])
    }

    private func configureMenu() {
        let menu = NSMenu()
        menu.addItem(withTitle: text("Show backend"), action: #selector(showWindow), keyEquivalent: "")
        menu.addItem(withTitle: text("Open Web & connection QR"), action: #selector(openWeb), keyEquivalent: "")
        menu.addItem(withTitle: text("Show logs"), action: #selector(showLogs), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: text("Quit OpenBiliClaw"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        for item in menu.items where item.action != #selector(NSApplication.terminate(_:)) { item.target = self }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "server.rack", accessibilityDescription: "OpenBiliClaw")
        statusItem.menu = menu
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = menu.copy() as? NSMenu
        mainMenu.addItem(appItem)
        NSApp.mainMenu = mainMenu
    }

    private func startBackend() {
        guard backend?.isRunning != true else { return }
        guard bundleSignatureValid else {
            stateLabel.stringValue = text("Application signature verification failed.")
            return
        }
        stopping = false
        webURL = nil
        stateLabel.stringValue = text("Starting backend…")
        serviceButton.title = text("Stop backend")
        openButton.isEnabled = false
        let runtime = root.appendingPathComponent("store-runtime.json")
        try? FileManager.default.removeItem(at: runtime)
        let child = Process()
        child.executableURL = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/OpenBiliClawBackend")
        child.currentDirectoryURL = root
        var env = ProcessInfo.processInfo.environment
        for key in ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "OPENBILICLAW_SELFTEST",
                    "OPENBILICLAW_SPONSORED_RUNTIME"] { env.removeValue(forKey: key) }
        env["HOME"] = NSHomeDirectory()
        env["OPENBILICLAW_PROJECT_ROOT"] = root.path
        env["OLLAMA_MODELS"] = root.appendingPathComponent("data/ollama-models").path
        if argument("--qa-profile") != nil { env["OPENBILICLAW_STORE_QA_STDIO"] = "1" }
        // QA uses isolated listeners, never the user's existing backend ports.
        if let port = argument("--qa-port").flatMap(Int.init), (1024...65534).contains(port) {
            env["OPENBILICLAW_PORT"] = String(port)
            env["OPENBILICLAW_IMAGE_SERVICE_PORT"] = String(port + 1)
        }
        child.environment = env
        let pipe = Pipe()
        child.standardInput = pipe
        child.standardOutput = argument("--qa-profile") == nil ? FileHandle.nullDevice : FileHandle.standardOutput
        child.standardError = argument("--qa-profile") == nil ? FileHandle.nullDevice : FileHandle.standardError
        child.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self, self.backend === process else { return }
                self.backend = nil
                self.lifetimePipe = nil
                self.webURL = nil
                self.openButton.isEnabled = false
                self.serviceButton.title = text("Start backend")
                self.stateLabel.stringValue = self.stopping || self.terminating ? text("Backend stopped") : text("Backend exited. Open logs for details, then retry.")
                if self.terminating { NSApp.reply(toApplicationShouldTerminate: true) }
            }
        }
        do {
            try child.run()
            // The host must not retain the read end, or EOF detection can stall.
            try? pipe.fileHandleForReading.close()
            lifetimePipe = pipe
            backend = child
        } catch { stateLabel.stringValue = error.localizedDescription; serviceButton.title = text("Start backend") }
    }

    private func stopBackend() {
        stopping = true
        stateLabel.stringValue = text("Stopping services…")
        openButton.isEnabled = false
        try? lifetimePipe?.fileHandleForWriting.close()
        // EOF triggers Python's graceful shutdown, including owned native services.
        let child = backend
        DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
            if child?.isRunning == true { child?.terminate() }
        }
    }

    private func refresh() {
        loginButton.state = SMAppService.mainApp.status == .enabled ? .on : .off
        guard let child = backend, child.isRunning, !stopping else { return }
        let path = root.appendingPathComponent("store-runtime.json")
        guard let bytes = try? Data(contentsOf: path),
              let state = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any],
              state["pid"] as? Int32 == child.processIdentifier,
              let port = state["port"] as? Int, (1...65535).contains(port) else { return }
        let host = state["host"] as? String ?? "0.0.0.0"
        var address = URLComponents()
        address.scheme = "http"
        let localHost = host == "0.0.0.0" ? "127.0.0.1" : (host == "::" ? "::1" : host)
        address.host = localHost.contains(":") && !localHost.hasPrefix("[") ? "[\(localHost)]" : localHost
        address.port = port
        guard let url = address.url else { return }
        var request = URLRequest(url: url.appendingPathComponent("api/ping"))
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self = self, self.backend === child, !self.stopping else { return }
                let healthy = (response as? HTTPURLResponse)?.statusCode == 200
                self.webURL = healthy ? url : nil
                self.openButton.isEnabled = healthy
                self.stateLabel.stringValue = healthy ? text("Backend running · ready to connect") : text("Waiting for backend…")
                self.addressLabel.stringValue = url.absoluteString
            }
        }.resume()
    }

    @objc private func showWindow() { window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc private func openWeb() { if let url = webURL { NSWorkspace.shared.open(url.appendingPathComponent("web/")) } }
    @objc private func showLogs() { NSWorkspace.shared.open(root.appendingPathComponent("logs", isDirectory: true)) }
    @objc private func toggleService() { backend?.isRunning == true ? stopBackend() : startBackend() }
    @objc private func toggleLogin() {
        let enable = loginButton.state == .on
        Task { @MainActor in
            do {
                if enable { try SMAppService.mainApp.register() }
                else { try await SMAppService.mainApp.unregister() }
                if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
            } catch { showError(error) }
            loginButton.state = SMAppService.mainApp.status == .enabled ? .on : .off
        }
    }
    private func showError(_ error: Error) { let alert = NSAlert(error: error); alert.runModal() }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        timer?.invalidate()
        guard backend?.isRunning == true else { return .terminateNow }
        terminating = true
        stopBackend()
        return .terminateLater
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}
