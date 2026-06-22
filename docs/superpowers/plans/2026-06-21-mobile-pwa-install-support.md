# Mobile PWA Install Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `/m/` so Android and iOS can save the mobile Web UI as a home-screen app icon with stable metadata and verified static assets.

**Architecture:** Keep the current no-build static Web architecture. Add only manifest metadata, iOS head metadata, tests, and docs; do not add a service worker or change backend routes.

**Tech Stack:** FastAPI `StaticFiles`, vanilla HTML/JSON assets, pytest `TestClient`, Ruff.

---

## File Structure

- Modify: `src/openbiliclaw/web/index.html`
  - Adds iOS home-screen title and explicit touch icon metadata.
- Modify: `src/openbiliclaw/web/manifest.json`
  - Adds app identity/scope metadata and Android icon purpose metadata.
- Modify: `tests/test_api_app.py`
  - Adds backend-served mobile install metadata contract tests.
- Modify: `docs/mobile-web-spec.md`
  - Clarifies add-to-home-screen support and no-offline boundary.
- Modify: `docs/modules/extension.md`
  - Clarifies the QR/mobile entry can be saved as a home-screen icon.
- Modify: `docs/changelog.md`
  - Records the user-visible mobile Web installability hardening.

## Task 1: Add Failing Mobile Install Metadata Tests

**Files:**
- Modify: `tests/test_api_app.py`

- [ ] **Step 1: Add a failing `/m/` HTML metadata test**

Add this test near `test_desktop_web_index_cache_busts_static_assets` inside `TestBackendAPI`:

```python
    def test_mobile_web_index_exposes_home_screen_metadata(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/m/")

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/html")
        assert '<link rel="manifest" href="manifest.json">' in response.text
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in response.text
        assert '<meta name="apple-mobile-web-app-title" content="BiliClaw">' in response.text
        assert '<link rel="apple-touch-icon" sizes="180x180" href="icon-192.png">' in response.text
```

- [ ] **Step 2: Add a failing manifest and icon asset contract test**

Add this test in the same class:

```python
    def test_mobile_web_manifest_is_installable_and_assets_resolve(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/m/manifest.json")

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("application/json")
        manifest = response.json()
        assert manifest["id"] == "/m/"
        assert manifest["scope"] == "/m/"
        assert manifest["start_url"] == "/m/"
        assert manifest["display"] == "standalone"
        assert manifest["name"] == "OpenBiliClaw"
        assert manifest["short_name"] == "BiliClaw"
        assert manifest.get("prefer_related_applications") is not True

        icons = manifest["icons"]
        sizes = {icon["sizes"] for icon in icons}
        assert {"192x192", "512x512"}.issubset(sizes)

        for icon in icons:
            assert icon["type"] == "image/png"
            assert icon.get("purpose") == "any maskable"
            icon_response = client.get(f"/m/{icon['src']}")
            assert icon_response.status_code == 200
            assert icon_response.headers.get("content-type", "").startswith("image/png")

        favicon_response = client.get("/favicon.ico")
        assert favicon_response.status_code == 200
        assert favicon_response.headers.get("content-type", "").startswith("image/png")
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
uv run pytest tests/test_api_app.py::TestBackendAPI::test_mobile_web_index_exposes_home_screen_metadata tests/test_api_app.py::TestBackendAPI::test_mobile_web_manifest_is_installable_and_assets_resolve -q
```

Expected: both tests fail because `apple-mobile-web-app-title`, `apple-touch-icon`, `id`, `scope`, and `purpose` are not present yet.

## Task 2: Implement Mobile Install Metadata

**Files:**
- Modify: `src/openbiliclaw/web/index.html`
- Modify: `src/openbiliclaw/web/manifest.json`

- [ ] **Step 1: Add iOS metadata to the mobile HTML head**

Update the `<head>` in `src/openbiliclaw/web/index.html` so the existing Apple metadata block becomes:

```html
  <meta name="theme-color" content="#fffafc">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="BiliClaw">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <title>OpenBiliClaw</title>
  <link rel="manifest" href="manifest.json">
  <link rel="apple-touch-icon" sizes="180x180" href="icon-192.png">
  <link rel="stylesheet" href="css/app.css">
```

- [ ] **Step 2: Add install metadata to the manifest**

Update `src/openbiliclaw/web/manifest.json` to:

```json
{
  "id": "/m/",
  "name": "OpenBiliClaw",
  "short_name": "BiliClaw",
  "description": "AI personalized content recommendations",
  "start_url": "/m/",
  "scope": "/m/",
  "display": "standalone",
  "background_color": "#fffafc",
  "theme_color": "#fb7299",
  "icons": [
    {
      "src": "icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ]
}
```

- [ ] **Step 3: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_api_app.py::TestBackendAPI::test_mobile_web_index_exposes_home_screen_metadata tests/test_api_app.py::TestBackendAPI::test_mobile_web_manifest_is_installable_and_assets_resolve -q
```

Expected: both tests pass.

## Task 3: Update Docs

**Files:**
- Modify: `docs/mobile-web-spec.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

- [ ] **Step 1: Update `docs/mobile-web-spec.md` PWA row and user instructions**

Change the PWA decision row to say:

```markdown
| PWA | 提供 manifest.json + iOS Web Clip 元数据，支持添加到主屏幕（暂不做 service worker / 离线缓存） |
```

In the "手机访问方式" section, add a short note after the URL example:

```markdown
打开 `/m/` 后可在 iOS Safari 通过「分享 → 添加到主屏幕」保存为桌面图标；Android Chrome / Chromium 浏览器可通过菜单里的「安装应用」或「添加到主屏幕」保存。局域网 HTTP 在部分 Android 浏览器上可能只生成快捷方式；完整 PWA 安装提示对 HTTPS 更稳定。
```

- [ ] **Step 2: Update `docs/modules/extension.md` mobile QR wording**

Find the bullet that describes the top mobile QR icon and update it to include:

```markdown
打开后的 `/m/` 页面已带 PWA manifest 与 iOS Web Clip 元数据，可从手机浏览器保存到主屏幕；当前不提供离线缓存，仍需手机能访问运行中的本地后端。
```

- [ ] **Step 3: Update `docs/changelog.md`**

Add one bullet in the current top version block:

```markdown
- **移动 Web 添加到主屏幕补强**：`/m/` manifest 增加 `id` / `scope` / maskable 图标声明，HTML head 增加 iOS Web Clip 标题与 touch icon；新增后端静态资源契约测试，确保手机保存桌面图标时使用稳定名称、图标和启动路径（不引入 service worker / 离线缓存）。
```

## Task 4: Focused Verification

**Files:**
- Verify current working tree.

- [ ] **Step 1: Run focused API metadata tests**

Run:

```bash
uv run pytest tests/test_api_app.py::TestBackendAPI::test_mobile_web_index_exposes_home_screen_metadata tests/test_api_app.py::TestBackendAPI::test_mobile_web_manifest_is_installable_and_assets_resolve -q
```

Expected: 2 passed.

- [ ] **Step 2: Run full related API app test file**

Run:

```bash
uv run pytest tests/test_api_app.py -q
```

Expected: all tests in `tests/test_api_app.py` pass.

- [ ] **Step 3: Run lint**

Run:

```bash
uv run ruff check src/ tests/
```

Expected: no lint errors.

## Task 5: Real Browser Verification

**Files:**
- Verify served `/m/` in a browser.

- [ ] **Step 1: Start local backend for browser validation**

Run:

```bash
uv run openbiliclaw start --host 127.0.0.1 --port 8420
```

Keep it running for browser checks.

- [ ] **Step 2: Open `/m/` in a real browser**

Use Playwright or browser MCP to open:

```text
http://127.0.0.1:8420/m/
```

Verify:

- `document.querySelector('link[rel="manifest"]').href` ends with `/m/manifest.json`.
- `document.querySelector('link[rel="apple-touch-icon"]').href` ends with `/m/icon-192.png`.
- `document.querySelector('meta[name="apple-mobile-web-app-title"]').content` is `BiliClaw`.
- `fetch('/m/manifest.json').then(r => r.json())` returns `id` and `scope` as `/m/`.
- `fetch('/m/icon-192.png')` returns status 200.
- `fetch('/m/icon-512.png')` returns status 200.

- [ ] **Step 3: Mobile viewport smoke check**

Set a phone-sized viewport, for example `390x844`, reload `/m/`, and take a screenshot. Verify the page is not blank and the mobile shell renders.

## Task 6: Comprehensive Verification

**Files:**
- Verify repository health after implementation.

- [ ] **Step 1: Run Python test suite**

Run:

```bash
uv run pytest
```

Expected: test suite passes.

- [ ] **Step 2: Run Python type and lint checks**

Run:

```bash
uv run mypy src/
uv run ruff check src/ tests/
```

Expected: both commands pass.

- [ ] **Step 3: Run extension test suite**

Run:

```bash
npm test
```

from `extension/`.

Expected: extension test suite passes.

- [ ] **Step 4: Review git diff**

Run:

```bash
git diff --stat
git diff -- src/openbiliclaw/web/index.html src/openbiliclaw/web/manifest.json tests/test_api_app.py docs/mobile-web-spec.md docs/modules/extension.md docs/changelog.md
```

Expected: diff only contains planned metadata, tests, and docs changes.
