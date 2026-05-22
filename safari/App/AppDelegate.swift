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
}
