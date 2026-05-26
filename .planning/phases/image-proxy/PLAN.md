---
phase: image-proxy
title: "Image Proxy Endpoint"
wave: 1
depends_on: []
files_modified:
  - src/openbiliclaw/api/app.py
  - src/openbiliclaw/web/js/view-models.js
  - src/openbiliclaw/web/js/views/recommend.js
  - src/openbiliclaw/web/js/views/chat.js
  - src/openbiliclaw/web/css/app.css
  - extension/popup/popup-helpers.js
  - extension/popup/popup.js
  - extension/tests/popup-helpers.test.ts
  - tests/test_api_image_proxy.py
  - tests/test_mobile_web_view_models.py
  - docs/changelog.md
  - docs/modules/runtime.md
  - docs/modules/extension.md
  - docs/architecture.md
  - docs/spec.md
  - README.md
  - README_EN.md
autonomous: true
---

# Plan: Image Proxy Endpoint

> **For implementer:** execute test-first. Do not start by copying the backend snippet into production; write the focused tests in each task, confirm they fail, then implement the smallest passing code.

<objective>
Add a server-side image proxy (`GET /api/image-proxy`) that fetches cover images from whitelisted CDN domains, validates redirects/content/size safely, and serves them to both mobile web and extension side panel. Update all recommendation/delight/message cover images to route through the proxy and show stable placeholders on failure.
</objective>

<architecture>
The backend validates the requested URL before any network call, follows redirects manually with validation on every hop, streams upstream bytes into a bounded `SpooledTemporaryFile`, then returns that spool with `StreamingResponse`. This is intentionally not a pass-through stream: the bounded spool is required so the endpoint can return a clean 413 before sending response headers when `Content-Length` is missing or dishonest.

Mobile web can use the relative `/api/image-proxy?...` URL. The extension UI runs from a `chrome-extension://` origin, so it must build an absolute proxy URL from the configured backend origin.
</architecture>

## Task 1: Backend image proxy tests

<read_first>
- `tests/test_api_app.py` for `create_app()` route-test fixtures and config isolation.
- `src/openbiliclaw/api/app.py` for existing route layout and imports.
</read_first>

<action>
Create `tests/test_api_image_proxy.py`.

Use the same config-isolation fixture style as `tests/test_api_app.py`. The tests should instantiate `create_app(soul_engine=FakeSoulEngine())` or the existing minimal pattern needed to avoid building the full runtime.

Add a tiny fake `httpx.AsyncClient` so tests do not hit the network. The fake must support:
- `async with httpx.AsyncClient(...) as client`
- `client.build_request(method, url, headers=...)`
- `await client.send(request, stream=True)`
- returning a fake response with `status_code`, `headers`, `aiter_bytes()`, `aclose()`, `is_redirect`

Required tests:

```python
def test_bilibili_image_success(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/bfs/archive/demo.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg", "content-length": "4"},
        chunks=[b"demo"],
    )
    resp = client.get(
        "/api/image-proxy",
        params={"url": "https://i1.hdslb.com/bfs/archive/demo.jpg"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == b"demo"


def test_xhscdn_image_success(client, fake_httpx):
    fake_httpx.add(
        "https://sns-webpic-qc.xhscdn.com/demo.jpg",
        status_code=200,
        headers={"content-type": "image/webp"},
        chunks=[b"webp"],
    )
    resp = client.get(
        "/api/image-proxy",
        params={"url": "https://sns-webpic-qc.xhscdn.com/demo.jpg"},
    )
    assert resp.status_code == 200
    assert resp.content == b"webp"


@pytest.mark.parametrize(
    "url, expected_status",
    [
        ("https://example.com/image.jpg", 403),
        ("https://evilhdslb.com/image.jpg", 403),
        ("ftp://i1.hdslb.com/image.jpg", 400),
        ("not-a-url", 400),
        ("https://user:pass@i1.hdslb.com/image.jpg", 400),
    ],
)
def test_url_validation(client, url, expected_status):
    resp = client.get("/api/image-proxy", params={"url": url})
    assert resp.status_code == expected_status


def test_redirect_to_non_whitelisted_domain_rejected(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/redirect.jpg",
        status_code=302,
        headers={"location": "https://example.com/image.jpg"},
        chunks=[],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/redirect.jpg"})
    assert resp.status_code == 403


def test_redirect_loop_returns_502(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/a.jpg",
        status_code=302,
        headers={"location": "https://i1.hdslb.com/a.jpg"},
        chunks=[],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/a.jpg"})
    assert resp.status_code == 502


def test_non_image_content_type_rejected(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/page",
        status_code=200,
        headers={"content-type": "text/html"},
        chunks=[b"<html>"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/page"})
    assert resp.status_code == 400


def test_content_length_over_limit_rejected_before_body(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/large.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg", "content-length": str(10 * 1024 * 1024 + 1)},
        chunks=[b"should-not-read"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/large.jpg"})
    assert resp.status_code == 413
    assert fake_httpx.responses["https://i1.hdslb.com/large.jpg"].read_count == 0


def test_actual_body_over_limit_rejected_without_content_length(client, fake_httpx):
    fake_httpx.add(
        "https://i1.hdslb.com/large-stream.jpg",
        status_code=200,
        headers={"content-type": "image/jpeg"},
        chunks=[b"x" * (10 * 1024 * 1024), b"x"],
    )
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/large-stream.jpg"})
    assert resp.status_code == 413


def test_timeout_returns_504(client, fake_httpx):
    fake_httpx.timeout_urls.add("https://i1.hdslb.com/slow.jpg")
    resp = client.get("/api/image-proxy", params={"url": "https://i1.hdslb.com/slow.jpg"})
    assert resp.status_code == 504
```

Run:

```bash
pytest tests/test_api_image_proxy.py -v
```

Expected: FAIL because `/api/image-proxy` does not exist yet.
</action>

<acceptance_criteria>
- New test file exists.
- Tests cover success, xhscdn, malformed URL, whitelist boundary, redirect, non-image, size via header, size via actual chunks, timeout.
- Initial run fails before implementation.
</acceptance_criteria>

## Task 2: Backend image proxy implementation

<read_first>
- `src/openbiliclaw/api/app.py:1-100` for imports/constants.
- `src/openbiliclaw/api/app.py:656` for health route placement.
</read_first>

<action>
In `src/openbiliclaw/api/app.py`:

1. Add imports:

```python
import tempfile
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, BinaryIO, cast

import httpx
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
```

The file already imports `Callable`, `TYPE_CHECKING`, `Any`, and `cast`; combine the imports instead of duplicating them.

2. Add constants near `_SOURCE_SHARE_ORDER`:

```python
_IMAGE_PROXY_ALLOWED_SUFFIXES = (
    "hdslb.com",
    "xhscdn.com",
    "pstatp.com",
    "douyinpic.com",
    "douyinvod.com",
    "ytimg.com",
    "ggpht.com",
)
_IMAGE_PROXY_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_PROXY_SPOOL_MEMORY_BYTES = 1024 * 1024
_IMAGE_PROXY_TIMEOUT_SECONDS = 10.0
_IMAGE_PROXY_MAX_REDIRECTS = 3
_IMAGE_PROXY_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_IMAGE_PROXY_UPSTREAM_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    ),
}
```

3. Add helpers before `create_app()`:

```python
def _is_image_proxy_host_allowed(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _IMAGE_PROXY_ALLOWED_SUFFIXES)


def _parse_image_proxy_url(raw_url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(raw_url)
    except httpx.InvalidURL as exc:
        raise HTTPException(status_code=400, detail="Invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if parsed.userinfo:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if not _is_image_proxy_host_allowed(parsed.host):
        raise HTTPException(status_code=403, detail="Domain not in whitelist")
    return parsed


def _validate_image_proxy_content_headers(headers: httpx.Headers) -> str:
    content_type = headers.get("content-type", "").strip()
    if not content_type.lower().startswith("image/"):
        raise HTTPException(status_code=400, detail="Not an image")
    content_length = headers.get("content-length")
    if content_length:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Invalid upstream content length") from exc
        if size > _IMAGE_PROXY_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Image too large")
    return content_type


def _iter_spooled_file(file_obj: BinaryIO) -> Iterator[bytes]:
    try:
        while True:
            chunk = file_obj.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        file_obj.close()
```

4. Add async helpers inside or outside `create_app()`:

```python
async def _send_image_proxy_request(client: httpx.AsyncClient, url: httpx.URL) -> httpx.Response:
    current = url
    seen: set[str] = set()
    for _ in range(_IMAGE_PROXY_MAX_REDIRECTS + 1):
        current = _parse_image_proxy_url(str(current))
        current_key = str(current)
        if current_key in seen:
            raise HTTPException(status_code=502, detail="Redirect loop")
        seen.add(current_key)
        request = client.build_request("GET", current_key, headers=_IMAGE_PROXY_UPSTREAM_HEADERS)
        response = await client.send(request, stream=True)
        if response.status_code in _IMAGE_PROXY_REDIRECT_STATUSES:
            location = response.headers.get("location", "").strip()
            await response.aclose()
            if not location:
                raise HTTPException(status_code=502, detail="Invalid redirect")
            current = current.join(location)
            continue
        return response
    raise HTTPException(status_code=502, detail="Too many redirects")


async def _read_image_proxy_body(response: httpx.Response) -> BinaryIO:
    spool = tempfile.SpooledTemporaryFile(
        max_size=_IMAGE_PROXY_SPOOL_MEMORY_BYTES,
        mode="w+b",
    )
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _IMAGE_PROXY_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Image too large")
            spool.write(chunk)
        spool.seek(0)
        return spool
    except Exception:
        spool.close()
        raise
```

5. Add route after `/api/health`, before cookie routes:

```python
@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(..., description="URL-encoded image URL to proxy")) -> StreamingResponse:
    """Proxy whitelisted remote cover images through the local backend."""
    parsed = _parse_image_proxy_url(url)
    try:
        async with httpx.AsyncClient(timeout=_IMAGE_PROXY_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await _send_image_proxy_request(client, parsed)
            try:
                if response.status_code < 200 or response.status_code >= 300:
                    raise HTTPException(status_code=502, detail="Upstream request failed")
                content_type = _validate_image_proxy_content_headers(response.headers)
                spool = await _read_image_proxy_body(response)
            finally:
                await response.aclose()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream timeout") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Upstream request failed") from exc

    return StreamingResponse(
        _iter_spooled_file(spool),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

6. Run:

```bash
pytest tests/test_api_image_proxy.py -v
ruff check src/openbiliclaw/api/app.py tests/test_api_image_proxy.py
```

Expected: PASS.
</action>

<acceptance_criteria>
- No `client.get(...).content` proxy implementation remains.
- `follow_redirects=False` is used.
- Host validation uses domain-boundary matching.
- Tests from Task 1 pass.
</acceptance_criteria>

## Task 3: Mobile web view-model tests and implementation

<read_first>
- `src/openbiliclaw/web/js/view-models.js`
- `tests/test_mobile_web_view_models.py`
</read_first>

<action>
1. Update `tests/test_mobile_web_view_models.py` first:

```javascript
assert.equal(
  normalizeCoverUrl("https://sns-webpic-qc.xhscdn.com/demo.jpg"),
  "https://sns-webpic-qc.xhscdn.com/demo.jpg",
);
assert.deepEqual(
  getCoverImageAttrs("https://i1.hdslb.com/bfs/archive/demo.jpg"),
  { src: "/api/image-proxy?url=https%3A%2F%2Fi1.hdslb.com%2Fbfs%2Farchive%2Fdemo.jpg" },
);
assert.equal(getCoverImageAttrs("not-a-url"), null);
```

Run:

```bash
pytest tests/test_mobile_web_view_models.py -v
```

Expected: FAIL before implementation.

2. In `src/openbiliclaw/web/js/view-models.js`:
- Remove the `.xhscdn.com` block from `normalizeCoverUrl()`.
- Keep `http://` to `https://` normalization.
- Change `getCoverImageAttrs()`:

```javascript
export function getCoverImageAttrs(value) {
  const src = normalizeCoverUrl(value);
  if (!src) return null;
  return { src: `/api/image-proxy?url=${encodeURIComponent(src)}` };
}
```

3. Run:

```bash
pytest tests/test_mobile_web_view_models.py -v
```

Expected: PASS.
</action>

<acceptance_criteria>
- `normalizeCoverUrl("https://sns-webpic-qc.xhscdn.com/demo.jpg")` returns the URL.
- `getCoverImageAttrs()` returns only `src`, no `referrerPolicy`.
- Mobile web view-model tests pass.
</acceptance_criteria>

## Task 4: Mobile web templates and fallback CSS

<read_first>
- `src/openbiliclaw/web/js/views/recommend.js`
- `src/openbiliclaw/web/js/views/chat.js`
- `src/openbiliclaw/web/css/app.css`
</read_first>

<action>
1. In `recommend.js` delight tray image:

```javascript
const coverHtml = cover
  ? `<span class="delight-thumb"><img src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-fallback');this.remove()"></span>`
  : `<span class="delight-thumb is-fallback">\u2728</span>`;
```

2. In `recommend.js` recommendation card image, use a wrapper:

```javascript
const coverHtml = cover
  ? `<div class="card-cover-frame"><img class="card-cover" src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-error');this.remove()"></div>`
  : `<div class="card-cover-frame is-error"></div>`;
```

3. In `chat.js`, replace the inline image with a wrapper:

```javascript
${cover ? `<div class="message-cover-frame"><img src="${esc(cover.src)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('is-error');this.remove()"></div>` : `<div class="message-cover-frame is-error"></div>`}
```

4. In `app.css`, move the fixed ratio to the wrapper, not the `<img>`:

```css
.card-cover-frame {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: var(--surface-soft);
  overflow: hidden;
  display: block;
}

.card-cover {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.card-cover-frame.is-error,
.message-cover-frame.is-error {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  background: var(--surface-soft);
  font-size: 13px;
}

.card-cover-frame.is-error::after,
.message-cover-frame.is-error::after {
  content: "封面加载失败";
}

.message-cover-frame {
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  margin-bottom: 6px;
  background: var(--surface-soft);
  overflow: hidden;
}

.message-cover-frame img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
```

5. Add/extend static tests if the repo already has mobile web template static tests. If not, add assertions to `tests/test_mobile_web_view_models.py` via file reads or create a new narrow static test:
- `recommend.js` does not contain `referrerpolicy="${cover.referrerPolicy}"`.
- `recommend.js` does not contain `onerror="this.remove()"` for `.card-cover`.
- `recommend.js` contains `card-cover-frame`.
- `chat.js` contains `message-cover-frame`.
- `app.css` contains `.card-cover-frame.is-error`.

Run:

```bash
pytest tests/test_mobile_web_view_models.py -v
ruff check src/openbiliclaw/web/js/view-models.js
```
</action>

<acceptance_criteria>
- Mobile web templates no longer reference `cover.referrerPolicy`.
- Failed card and chat images leave wrapper placeholders.
- Placeholder is on wrapper elements, not `<img>::after`.
</acceptance_criteria>

## Task 5: Extension proxy URLs and fallback preservation

<read_first>
- `extension/popup/popup-helpers.js`
- `extension/popup/popup.js`
- `extension/popup/popup-backend-config.js`
- `extension/tests/popup-helpers.test.ts`
</read_first>

<action>
1. In `extension/popup/popup-helpers.js`, export a pure proxy path helper:

```javascript
export function buildImageProxyPath(value) {
  const src = normalizeCoverUrl(value);
  if (!src) return "";
  try {
    new URL(src);
  } catch {
    return "";
  }
  return `/api/image-proxy?url=${encodeURIComponent(src)}`;
}
```

2. In `extension/tests/popup-helpers.test.ts`, add tests:

```javascript
assert.equal(
  buildImageProxyPath("https://i1.hdslb.com/bfs/archive/demo.jpg"),
  "/api/image-proxy?url=https%3A%2F%2Fi1.hdslb.com%2Fbfs%2Farchive%2Fdemo.jpg",
);
assert.equal(
  buildImageProxyPath("https://sns-webpic-qc.xhscdn.com/demo.jpg"),
  "/api/image-proxy?url=https%3A%2F%2Fsns-webpic-qc.xhscdn.com%2Fdemo.jpg",
);
assert.equal(buildImageProxyPath("not-a-url"), "");
```

3. In `extension/popup/popup.js` imports:

```javascript
import {
  ...
  buildImageProxyPath,
} from "./popup-helpers.js";
import {
  getBackendEndpointConfig,
  getBackendOrigin,
  ...
} from "./popup-backend-config.js";
```

4. Add a small helper near other shared UI helpers:

```javascript
async function setProxyImageSrc(image, coverUrl) {
  const path = buildImageProxyPath(coverUrl);
  if (!path) return false;
  const origin = await getBackendOrigin();
  image.src = `${origin}${path}`;
  return true;
}
```

5. Replace the four direct extension image assignments:
- message delight thumb
- delight banner thumb
- expanded delight reason float cover
- recommendation cover

Change each from:

```javascript
image.src = item.cover_url;
image.referrerPolicy = "no-referrer";
```

To:

```javascript
void setProxyImageSrc(image, item.cover_url);
```

Keep existing `error` listeners that remove the image and add `is-fallback`; extension already has wrapper-based fallback CSS in `popup.html`.

6. Add static assertions in the extension test suite:
- `extension/popup/popup.js` does not contain `referrerPolicy = "no-referrer"`.
- `extension/popup/popup.js` contains `setProxyImageSrc`.
- `extension/popup/popup.js` contains `/api/image-proxy` only through helper usage, not hardcoded direct CDN rewrites.

Run the existing extension tests:

```bash
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

Also run the full extension suite before final handoff:

```bash
cd extension && npm test
```
</action>

<acceptance_criteria>
- Extension cover images use configured backend origin + `/api/image-proxy`.
- Extension no longer sets `referrerPolicy` for cover images.
- Existing wrapper fallback behavior remains.
- Extension helper/static tests pass.
</acceptance_criteria>

## Task 6: Documentation updates

<read_first>
- `docs/modules/runtime.md`
- `docs/modules/extension.md`
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`
- `docs/changelog.md`
</read_first>

<action>
Update docs because this adds a new backend API endpoint and changes image-loading data flow.

Required edits:

1. `docs/modules/runtime.md`
   - Add `/api/image-proxy` under public API/runtime API surface.
   - Document whitelist, max 10MB, redirect validation, cache header, and local-only security assumptions.

2. `docs/modules/extension.md`
   - Update side panel description to say recommendation/delight/message cover images are loaded through backend image proxy using configured backend origin.

3. `docs/architecture.md`
   - Add one sentence in local API / extension data flow: cover images flow `UI -> /api/image-proxy -> whitelisted CDN -> bounded spool -> UI`.

4. `docs/spec.md`
   - Update system architecture / API section with the image proxy endpoint if the diagram lists local API responsibilities.

5. `README.md` and `README_EN.md`
   - Update the top architecture/API description only if the current README diagram or feature text mentions local API/static UI surfaces. Keep the change short.

6. `docs/changelog.md`
   - Add a top bullet in the current version block: backend image proxy for mobile web + extension cover images, with whitelist/size guard/fallback placeholders.

Run:

```bash
ruff format src/ tests/
ruff check src/ tests/
pytest tests/test_api_image_proxy.py tests/test_mobile_web_view_models.py -v
```

Record extension test command separately.
</action>

<acceptance_criteria>
- Runtime and extension module docs mention the image proxy.
- Changelog includes the user-visible image loading fix.
- Architecture docs reflect the new UI -> backend -> CDN data flow.
</acceptance_criteria>

## Verification

<must_haves>
1. `GET /api/image-proxy?url=<bilibili_url>` returns image data with correct content type and cache headers.
2. Non-whitelisted domains return 403.
3. Malformed URL, non-HTTP(S), empty host and userinfo return 400.
4. Redirects are manually validated; redirect to non-whitelist returns 403.
5. Non-image upstream content returns 400.
6. `Content-Length > 10MB` and actual body > 10MB return 413.
7. Timeout returns 504.
8. Mobile web and extension cover images use the proxy.
9. Failed image loads show wrapper placeholders instead of collapsing layout.
10. Docs are updated with the new API/data flow.
</must_haves>

<verify_commands>
```bash
# Backend image proxy tests
pytest tests/test_api_image_proxy.py -v

# Mobile web helper/static tests
pytest tests/test_mobile_web_view_models.py -v

# Full Python suite with timeout
pytest --timeout=30

# Lint/format
ruff format src/ tests/
ruff check src/ tests/

# Targeted extension helper tests
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts

# Full extension suite
cd extension && npm test
```
</verify_commands>

## Implementation Notes

- Do not use `httpx.AsyncClient.get()` for the proxy implementation; it eagerly loads the response body.
- Do not rely on `<img>::after` for placeholders. Use wrapper elements.
- Do not use bare `hostname.endswith("hdslb.com")`; enforce the dot boundary.
- Do not use `follow_redirects=True`.
- Do not skip docs. This phase changes API surface and cross-module data flow.
