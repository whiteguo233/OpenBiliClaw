//
//  ViewController.swift
//  OpenBiliClaw
//
//  Shown when the user launches the host app. Tells them how to enable
//  the extension in Safari and gives them a button that jumps straight
//  to Safari's Extensions preferences.
//

import Cocoa
import SafariServices

class ViewController: NSViewController {

    private let extensionBundleIdentifier = "io.github.whiteguo233.openbiliclaw.extension"

    @IBOutlet weak var statusLabel: NSTextField?

    override func viewDidLoad() {
        super.viewDidLoad()
        refreshExtensionState()
    }

    @IBAction func openSafariPreferences(_ sender: Any?) {
        SFSafariApplication.showPreferencesForExtension(
            withIdentifier: extensionBundleIdentifier
        ) { [weak self] error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.statusLabel?.stringValue =
                        "无法打开 Safari 偏好设置：\(error.localizedDescription)"
                }
            }
        }
    }

    @IBAction func refresh(_ sender: Any?) {
        refreshExtensionState()
    }

    private func refreshExtensionState() {
        SFSafariExtensionManager.getStateOfSafariExtension(
            withIdentifier: extensionBundleIdentifier
        ) { [weak self] state, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if let error = error {
                    self.statusLabel?.stringValue =
                        "状态未知：\(error.localizedDescription)"
                    return
                }
                if let state = state, state.isEnabled {
                    self.statusLabel?.stringValue = "OpenBiliClaw 已在 Safari 中启用 ✅"
                } else {
                    self.statusLabel?.stringValue =
                        "尚未在 Safari 中启用 — 点击下方按钮前往启用。"
                }
            }
        }
    }
}
