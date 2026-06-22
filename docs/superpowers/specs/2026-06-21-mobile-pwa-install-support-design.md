# Mobile PWA Install Support Design

## Goal

Make the existing mobile Web UI at `/m/` reliably saveable as a phone home-screen app icon on Android and iOS.

The work should harden the current manifest and iOS metadata so users who open `http://<LAN-IP>:8420/m/` can add OpenBiliClaw to their home screen with the expected name, icon, launch URL, and standalone presentation where the browser supports it.

## Current Behavior

- The mobile SPA lives in `src/openbiliclaw/web/` and is mounted by FastAPI at `/m`.
- `/m/` returns `index.html`.
- `/m/manifest.json` is reachable and contains:
  - `name`
  - `short_name`
  - `description`
  - `start_url: "/m/"`
  - `display: "standalone"`
  - `background_color`
  - `theme_color`
  - 192x192 and 512x512 PNG icons
- `/m/icon-192.png`, `/m/icon-512.png`, and `/favicon.ico` are reachable.
- `index.html` already links the web app manifest and includes:
  - `viewport-fit=cover`
  - `theme-color`
  - `apple-mobile-web-app-capable`
  - `apple-mobile-web-app-status-bar-style`
- There is no mobile service worker and no offline cache behavior.
- `docs/mobile-web-spec.md` explicitly says PWA support is manifest-only and does not include offline caching.

## Target Behavior

- Android Chrome and Chromium browsers can identify `/m/` as an installable app-like surface based on complete manifest metadata.
- iOS Safari has explicit Web Clip metadata, including a stable touch icon and home-screen title.
- Launching from the saved icon opens `/m/`, not an incidental nested route or asset URL.
- Installed or saved instances use a bounded app scope under `/m/`.
- The existing no-build static asset model remains intact.
- The mobile Web UI continues to require the backend to be reachable on the phone; this change does not make the app work offline.

## Non-Goals

- Do not add offline caching.
- Do not add push notifications.
- Do not add background sync.
- Do not add a custom in-app install prompt.
- Do not introduce a frontend build step.
- Do not change authentication, LAN exposure, QR generation, or API behavior.
- Do not make `/web` or `/setup` installable in this pass.

## Approach Options

### Option A: Metadata Hardening Only

Add missing install metadata while preserving the current static architecture:

- Add `id` and `scope` to `src/openbiliclaw/web/manifest.json`.
- Add `purpose: "any maskable"` or separate maskable icon entries for Android adaptive icons.
- Add `apple-mobile-web-app-title` to `src/openbiliclaw/web/index.html`.
- Add explicit `apple-touch-icon` links pointing at an existing PNG icon.
- Add regression tests that request `/m/`, `/m/manifest.json`, and icon files through `TestClient`.
- Update the mobile Web documentation and changelog.

This is the recommended option. It directly addresses home-screen icon behavior without creating cache invalidation or service worker lifecycle risk.

### Option B: Minimal Pass-Through Service Worker

Register a small service worker under `/m/` with install, activate, and network pass-through fetch handling.

This may improve full PWA install prompt behavior in some Chromium paths, but it adds service worker update semantics to a local-first app whose static files are currently served directly. A pass-through worker also provides little user value because OpenBiliClaw depends on a live local backend for data.

### Option C: Full Offline Shell

Add a real service worker cache for the mobile shell, CSS, JS, manifest, and icons.

This would make the UI shell load offline, but most useful screens would still fail without the backend. It also needs explicit cache versioning and update UX, which is too much scope for the current goal.

## Recommended Design

Implement Option A.

The design treats home-screen support as a metadata contract:

- The manifest describes the installed app identity, launch URL, scope, colors, display mode, and Android icons.
- The HTML head describes iOS-specific app title, status bar behavior, and touch icon.
- The backend continues to serve all static files from `StaticFiles(directory=web_dir, html=True)`.
- Tests prove the install metadata is present and that referenced assets resolve from the mounted `/m/` path.

## Manifest Contract

Update `src/openbiliclaw/web/manifest.json` to include:

```json
{
  "id": "/m/",
  "scope": "/m/",
  "start_url": "/m/",
  "display": "standalone",
  "name": "OpenBiliClaw",
  "short_name": "BiliClaw"
}
```

Keep the existing 192x192 and 512x512 icons. Add `purpose` to icon objects:

```json
{
  "src": "icon-192.png",
  "sizes": "192x192",
  "type": "image/png",
  "purpose": "any maskable"
}
```

The implementation may reuse the existing square icons for `maskable` in this pass. If later visual testing shows Android crops the logo poorly, create separate padded maskable icons in a dedicated icon pass.

Do not set `prefer_related_applications: true`.

## HTML Head Contract

Update `src/openbiliclaw/web/index.html` with explicit iOS metadata:

```html
<meta name="apple-mobile-web-app-title" content="BiliClaw">
<link rel="apple-touch-icon" sizes="180x180" href="icon-192.png">
```

`icon-192.png` is intentionally reused because it is already present, reachable, and larger than the common 180x180 iPhone touch icon size. A dedicated 180x180 icon can be added later if visual polish requires it.

Keep the existing:

```html
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="manifest" href="manifest.json">
```

## Backend Contract

No backend routing change is required.

Existing behavior must remain true:

- `/m/` returns the mobile HTML shell.
- `/m/manifest.json` returns JSON.
- `/m/icon-192.png` returns `image/png`.
- `/m/icon-512.png` returns `image/png`.
- `/favicon.ico` returns the 192px PNG.

If tests reveal package builds omit `src/openbiliclaw/web/`, fix packaging separately. The current PyInstaller spec already includes `openbiliclaw/web`.

## User-Facing Behavior

### iOS

The supported path is Safari:

1. Open `http://<LAN-IP>:8420/m/`.
2. Share.
3. Add to Home Screen.
4. Keep `Open as Web App` enabled when the iOS version shows that control.

Expected result: the home-screen icon uses the OpenBiliClaw touch icon, the label is `BiliClaw`, and the app opens `/m/`.

### Android

The supported path is Chrome or another Chromium browser:

1. Open `http://<LAN-IP>:8420/m/`.
2. Browser menu.
3. Install app or Add to Home Screen, depending on the browser and security context.

Expected result: the shortcut/app uses the manifest name, icon, launch URL, and standalone display metadata where the browser accepts it.

LAN HTTP may limit the experience to a shortcut-like install on some Android browsers. Full install prompts are more reliable over HTTPS or trusted localhost/loopback.

## Documentation Updates

Update these docs with the implementation:

- `docs/mobile-web-spec.md`: clarify the supported add-to-home-screen behavior and the no-offline-cache boundary.
- `docs/modules/extension.md`: update the mobile QR entry section if wording currently implies only browser-tab use.
- `docs/changelog.md`: add a short entry in the current version block.

No architecture diagram update is required because the module boundary, data flow, and backend mounts do not change.

## Test Plan

Add focused tests under `tests/test_api_app.py` or a new narrow mobile web metadata test:

- `GET /m/` returns 200 and includes:
  - `<link rel="manifest" href="manifest.json">`
  - `apple-mobile-web-app-capable`
  - `apple-mobile-web-app-title`
  - `apple-touch-icon`
- `GET /m/manifest.json` returns 200 JSON and includes:
  - `id: "/m/"`
  - `scope: "/m/"`
  - `start_url: "/m/"`
  - `display: "standalone"`
  - `name` or `short_name`
  - icon entries for 192x192 and 512x512
  - no `prefer_related_applications: true`
- For every icon referenced by the manifest, `GET /m/<src>` returns 200 and `image/png`.
- `GET /favicon.ico` returns 200 and `image/png`.

Recommended verification commands:

```bash
pytest tests/test_api_app.py -q
ruff check src/ tests/
```

If the implementation only touches HTML, JSON, tests, and docs, `mypy src/` is not required unless Python route tests are changed.

## Risks

- Android may still show `Add to Home Screen` rather than `Install app` when accessed over plain LAN HTTP.
- Reusing the 192px icon as an iOS touch icon may be acceptable but not pixel-perfect.
- Declaring an existing icon as `maskable` may crop poorly on some Android launchers if the graphic has insufficient padding.
- iOS behavior differs by version; recent Safari versions make web app mode easier, but older versions rely more heavily on Apple-specific metadata.

## Acceptance Criteria

- `/m/` contains explicit iOS home-screen metadata.
- `/m/manifest.json` contains `id`, `scope`, complete install fields, and usable Android icon metadata.
- All manifest and touch icon assets resolve through the `/m/` mount.
- Existing mobile Web routing and API behavior are unchanged.
- Tests cover the metadata and static asset contract.
- Documentation states that this supports add-to-home-screen but does not add offline support.

## References

- Chrome manifest installability requirements: https://developer.chrome.com/docs/lighthouse/pwa/installable-manifest
- Chrome install criteria update: https://developer.chrome.com/blog/update-install-criteria
- MDN Web App Manifest reference: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest
- Apple Safari Web Clip configuration: https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html
- Apple iPhone Add to Home Screen flow: https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios
- WebKit Safari 26 web app behavior: https://webkit.org/blog/17333/webkit-features-in-safari-26-0/
