# OpenBiliClaw — Safari macOS port

This directory holds the Xcode project that wraps the OpenBiliClaw Web
Extension as a native macOS app, so it can be loaded into Safari.

```
safari/
├── project.yml                       # XcodeGen spec — generates the .xcodeproj
├── App/                              # macOS host app target
│   ├── AppDelegate.swift
│   ├── ViewController.swift
│   ├── Info.plist
│   ├── OpenBiliClaw.entitlements
│   ├── Base.lproj/Main.storyboard
│   └── Assets.xcassets/
└── Extension/                        # Safari Web Extension target
    ├── SafariWebExtensionHandler.swift
    ├── Info.plist
    ├── OpenBiliClaw_Extension.entitlements
    └── Resources/                    # ← populated by package-safari.mjs
```

## Prerequisites

- macOS 14+ with Xcode 15.3+ (Safari 17.4+ supports MV3 with MAIN world
  content scripts, which the xiaohongshu sniffer needs).
- Node.js 22+ (matches the rest of the repo).
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`.

## One-time setup

```sh
# 1. Generate the Xcode project from project.yml
cd safari
xcodegen generate
cd ..

# 2. Build the extension bundle and copy it into the Xcode project
cd extension
npm install
npm run package:safari
```

`package:safari` runs `build:safari` (which writes `dist-safari/`) and then
copies its contents into `safari/Extension/Resources/`.

## Run in Safari

1. Open `safari/OpenBiliClawSafari.xcodeproj` in Xcode.
2. Select the **OpenBiliClaw** scheme (the host app), then **Product > Run**.
3. The host app window opens. Click **在 Safari 中启用…** — Xcode will
   take you to Safari's Extensions preferences.
4. In Safari: tick the checkbox next to **OpenBiliClaw**.
   - If the extension does not appear, enable Safari's developer menu
     (Safari > Settings > Advanced > Show Develop menu in menu bar) and
     then Develop > **Allow Unsigned Extensions**. This setting resets
     every Safari restart during development.
5. The OpenBiliClaw toolbar button (the 阿B icon) appears in Safari's
   toolbar. Click it to open the compact launcher popup, then click
   **在新标签页中打开** to load the full UI.

## Iterating

After making changes to `extension/src/**`:

```sh
cd extension
npm run package:safari
```

Then in Xcode hit **Product > Run** again. A post-build script copies
the new contents of `safari/Extension/Resources/` into the .appex
bundle at build time, so you don't need to re-run `xcodegen generate`
between iterations.

## Notes on Safari behavior

- **Side panel:** Safari has no `sidePanel` or `sidebar_action` API, so
  the Chrome side panel UX becomes a small toolbar popup with an "Open
  in new tab" button (see `extension/popup/popup-launcher.html`). The
  full UI (`popup/popup.html`) opens in a regular tab via
  `chrome.tabs.create`. The background-side fallback in
  `src/background/notifications.ts` already opens a tab when no panel
  API is available, so notification-driven opens work too.
- **MAIN world content scripts:** Supported on Safari 17.4+. The
  xiaohongshu token sniffer and state bridge run unchanged.
- **`cookies` permission:** Safari honors it, but Intelligent Tracking
  Prevention can drop third-party cookies aggressively. If cookie sync
  flakes out, that's the most likely cause.
- **Notifications:** First use prompts the user via macOS Notification
  Center. There's nothing to configure in `Info.plist` for this — the
  WebExtension permission handles it.
- **Native messaging:** Not used today. `SafariWebExtensionHandler.swift`
  is a stub principal class required by Safari.
- **Signing:** The project signs ad-hoc by default
  (`CODE_SIGN_IDENTITY = "-"`, `CODE_SIGN_STYLE = Manual`) so it builds
  locally without an Apple Developer account. This is enough for the
  Allow Unsigned Extensions toggle. For distribution you'll need a
  Developer ID and notarization — switch the settings block in
  `project.yml` to `CODE_SIGN_STYLE: Automatic` with your
  `DEVELOPMENT_TEAM`, then re-run `xcodegen generate`.

## Troubleshooting

- **Extension doesn't appear in Safari's Extensions list**: usually one
  of three things —
    1. You ran Product > Run before `npm run package:safari`. The
       build fails loudly now (the post-build script checks for
       manifest.json in `Extension/Resources/`), but if you skipped
       the error, just run `npm run package:safari` and build again.
    2. Allow Unsigned Extensions resets every time you quit Safari.
       Re-toggle it under Develop > Developer Settings.
    3. Safari hasn't re-scanned. Quit Safari fully (⌘Q) and reopen.
- **Build fails with "manifest.json is missing"**: that's the
  post-build script's pre-flight check. Run `npm run package:safari`
  from the `extension/` directory.
- **"OpenBiliClaw Extension.appex" code signing fails**: you've
  probably switched away from the default ad-hoc identity but Xcode
  can't find your team. Either revert to ad-hoc (`CODE_SIGN_IDENTITY:
  "-"` in `project.yml`) or set a `DEVELOPMENT_TEAM` and re-run
  xcodegen.
