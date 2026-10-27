"""Capture current OpenBiliClaw UI with deterministic local demo data."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page, Route

if __package__:
    from scripts.chrome_webstore_demo import DemoServer
else:
    from chrome_webstore_demo import DemoServer

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "extension"
EXPECTED = (
    "desktop-recommend.png",
    "desktop-settings.png",
    "mobile-recommend.png",
    "extension-recommend.png",
)
DOC_EXPECTED = (
    "desktop-home.png",
    "desktop-profile.png",
    "desktop-cards.png",
    "mobile-recommend.png",
    "mobile-profile.png",
    "mobile-chat.png",
    "screenshot-recommend.png",
    "screenshot-profile-portrait.png",
    "screenshot-profile-traits.png",
    "screenshot-profile-values.png",
    "screenshot-profile-style.png",
    "screenshot-chat.png",
    "screenshot-recommend-feedback.png",
    "screenshot-interest-probe.png",
)


def _extension_browser_executable() -> str:
    candidates = sorted(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium-*/chrome-mac-x64/"
            "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        reverse=True,
    )
    candidates.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    fallback = shutil.which("google-chrome") or shutil.which("chromium")
    if fallback:
        return fallback
    raise FileNotFoundError("Chrome/Chromium executable not found")


def _install_loopback_guard(context: BrowserContext, blocked: list[str]) -> None:
    def guard(route: Route) -> None:
        parsed = urlsplit(route.request.url)
        if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
        }:
            blocked.append(route.request.url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    context.route("**/*", guard)


def _prepare_page(page: Page) -> None:
    page.add_init_script(
        """
        localStorage.setItem("obc.theme", "light");
        localStorage.setItem("obc.noticeDismissed", "1");
        localStorage.setItem("openbiliclaw.webui.mobileQrSeen", "1");
        window.WebSocket = class DemoWebSocket {
          static OPEN = 1;
          constructor() { this.readyState = 1; }
          addEventListener() {}
          removeEventListener() {}
          send() {}
          close() { this.readyState = 3; }
        };
        """
    )


def _wait_for_covers(page: Page, selector: str, minimum: int) -> None:
    page.wait_for_function(
        """({selector, minimum}) => {
          const images = [...document.querySelectorAll(selector)];
          return images.length >= minimum
            && images.every((image) => image.complete && image.naturalWidth > 0);
        }""",
        arg={"selector": selector, "minimum": minimum},
        timeout=15_000,
    )


def _load_lazy_covers(page: Page, selector: str, minimum: int) -> None:
    page.wait_for_function(
        "({selector, minimum}) => document.querySelectorAll(selector).length >= minimum",
        arg={"selector": selector, "minimum": minimum},
        timeout=15_000,
    )
    images = page.locator(selector)
    for index in range(minimum):
        images.nth(index).scroll_into_view_if_needed()
    page.wait_for_function(
        "({selector, minimum}) => { const images = [...document.querySelectorAll(selector)].slice(0, minimum); return images.length >= minimum && images.every((image) => image.complete && image.naturalWidth > 0); }",
        arg={"selector": selector, "minimum": minimum},
        timeout=15_000,
    )
    page.evaluate("window.scrollTo(0, 0)")


def _capture_web(
    origin: str,
    output_dir: Path,
    blocked: list[str],
    docs_output_dir: Path | None = None,
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        _install_loopback_guard(context, blocked)
        page = context.new_page()
        _prepare_page(page)
        page.goto(f"{origin}/web/", wait_until="domcontentloaded")
        page.wait_for_function(
            "document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)').length >= 3",
            timeout=15_000,
        )
        _wait_for_covers(page, "#videoGrid .video-card .cover img", 3)
        _wait_for_covers(page, "#delightThumb img", 1)
        page.screenshot(path=output_dir / "desktop-recommend.png")

        page.locator("#settingsBtn").click()
        page.locator('[data-settings-tab="sources"]').click()
        page.locator('[data-settings-panel="sources"]:not([hidden])').wait_for(state="visible")
        page.wait_for_function(
            "document.querySelector('[data-source-status=reddit] .src-detail')?.textContent.includes('Reddit')"
        )
        page.screenshot(path=output_dir / "desktop-settings.png")
        context.close()
        browser.close()

        mobile = playwright.chromium.launch(channel="chrome", headless=True)
        mobile_context = mobile.new_context(
            viewport={"width": 430, "height": 900},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
        )
        _install_loopback_guard(mobile_context, blocked)
        mobile_page = mobile_context.new_page()
        _prepare_page(mobile_page)
        mobile_page.goto(f"{origin}/m/", wait_until="domcontentloaded")
        mobile_page.wait_for_function(
            "document.querySelectorAll('#app .card').length >= 1",
            timeout=15_000,
        )
        _load_lazy_covers(mobile_page, "#app .card-cover-frame img.card-cover", 2)
        mobile_page.screenshot(path=output_dir / "mobile-recommend.png")
        mobile_context.close()
        mobile.close()

        if docs_output_dir is not None:
            docs_output_dir.mkdir(parents=True, exist_ok=True)
            docs_browser = playwright.chromium.launch(channel="chrome", headless=True)
            docs_context = docs_browser.new_context(
                viewport={"width": 1600, "height": 1000},
                device_scale_factor=1,
            )
            _install_loopback_guard(docs_context, blocked)
            docs_page = docs_context.new_page()
            _prepare_page(docs_page)
            docs_page.goto(f"{origin}/web/", wait_until="domcontentloaded")
            docs_page.wait_for_function(
                "document.querySelectorAll('#videoGrid .video-card:not(.is-skeleton)').length >= 3",
                timeout=15_000,
            )
            _wait_for_covers(docs_page, "#videoGrid .video-card .cover img", 3)
            _wait_for_covers(docs_page, "#delightThumb img", 1)
            docs_page.screenshot(path=docs_output_dir / "desktop-home.png")

            docs_page.locator("#videoGrid").scroll_into_view_if_needed()
            docs_page.screenshot(path=docs_output_dir / "desktop-cards.png")

            docs_page.locator("#profileBtn").click()
            docs_page.locator("#profilePage:not([hidden])").wait_for(state="visible")
            docs_page.wait_for_function(
                "document.querySelectorAll('#profileDetails .profile-item').length >= 1",
                timeout=15_000,
            )
            docs_page.screenshot(path=docs_output_dir / "desktop-profile.png")
            docs_context.close()
            docs_browser.close()

            docs_mobile = playwright.chromium.launch(channel="chrome", headless=True)
            docs_mobile_context = docs_mobile.new_context(
                viewport={"width": 640, "height": 1385},
                device_scale_factor=1,
                is_mobile=True,
                has_touch=True,
            )
            _install_loopback_guard(docs_mobile_context, blocked)
            docs_mobile_page = docs_mobile_context.new_page()
            _prepare_page(docs_mobile_page)
            docs_mobile_page.goto(f"{origin}/m/", wait_until="domcontentloaded")
            docs_mobile_page.wait_for_function(
                "document.querySelectorAll('#app .card').length >= 1",
                timeout=15_000,
            )
            _load_lazy_covers(docs_mobile_page, "#app .card-cover-frame img.card-cover", 2)
            docs_mobile_page.screenshot(path=docs_output_dir / "mobile-recommend.png")

            docs_mobile_page.get_by_role("tab", name="画像").click()
            docs_mobile_page.locator("#view-profile.active .profile-section").first.wait_for(
                state="visible",
                timeout=15_000,
            )
            docs_mobile_page.screenshot(path=docs_output_dir / "mobile-profile.png")

            docs_mobile_page.get_by_role("tab", name="对话").click()
            docs_mobile_page.locator("#view-chat.active .chat-shell").wait_for(
                state="visible",
                timeout=15_000,
            )
            docs_mobile_page.screenshot(path=docs_output_dir / "mobile-chat.png")
            docs_mobile_context.close()
            docs_mobile.close()


def _capture_extension(
    origin: str,
    output_dir: Path,
    blocked: list[str],
    docs_output_dir: Path | None = None,
) -> None:
    from playwright.sync_api import sync_playwright

    service_worker = EXTENSION_ROOT / "dist/background/service-worker.js"
    popup = EXTENSION_ROOT / "popup/popup.html"
    if not service_worker.exists() or not popup.exists():
        raise FileNotFoundError("extension build missing; run `cd extension && npm run build`")

    parsed = urlsplit(origin)
    if parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise RuntimeError(f"refusing non-demo backend origin: {origin}")

    with (
        tempfile.TemporaryDirectory(prefix="obc-store-capture-") as profile_dir,
        sync_playwright() as playwright,
    ):
        context = playwright.chromium.launch_persistent_context(
            profile_dir,
            executable_path=_extension_browser_executable(),
            headless=False,
            args=[
                f"--disable-extensions-except={EXTENSION_ROOT}",
                f"--load-extension={EXTENSION_ROOT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=560,940",
            ],
            viewport={"width": 520, "height": 860},
        )
        _install_loopback_guard(context, blocked)
        workers = [
            worker
            for worker in context.service_workers
            if "/dist/background/service-worker.js" in worker.url
        ]
        worker = workers[0] if workers else context.wait_for_event("serviceworker", timeout=15_000)
        extension_id = worker.evaluate("chrome.runtime.id")
        worker.evaluate(
            """async ({host, port}) => {
              await chrome.storage.local.set({
                popup_backend_endpoint: {scheme: "http", host, port}
              });
            }""",
            {"host": parsed.hostname, "port": parsed.port},
        )
        page = context.new_page()
        page.set_viewport_size({"width": 520, "height": 860})
        page.goto(
            f"chrome-extension://{extension_id}/popup/popup.html",
            wait_until="domcontentloaded",
        )
        page.locator("#recommendationList .recommendation-card").first.wait_for(
            state="visible",
            timeout=20_000,
        )
        _load_lazy_covers(page, "#recommendationList .recommendation-cover img", 2)
        page.screenshot(path=output_dir / "extension-recommend.png")

        if docs_output_dir is not None:
            docs_output_dir.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({"width": 520, "height": 1089})
            page.locator("#tabRecommend").click()
            page.locator("#recommendationList .recommendation-card").first.wait_for(state="visible")
            page.screenshot(path=docs_output_dir / "screenshot-recommend.png")
            page.screenshot(path=docs_output_dir / "screenshot-recommend-feedback.png")

            page.locator("#tabProfile").click()
            page.locator("#profileCard:not([hidden])").wait_for(state="visible", timeout=15_000)
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=docs_output_dir / "screenshot-profile-portrait.png")

            page.locator("#profileTraits").scroll_into_view_if_needed()
            page.screenshot(path=docs_output_dir / "screenshot-profile-traits.png")

            page.locator("#profileValues").scroll_into_view_if_needed()
            page.screenshot(path=docs_output_dir / "screenshot-profile-values.png")

            page.locator("#profileStyle").scroll_into_view_if_needed()
            page.screenshot(path=docs_output_dir / "screenshot-profile-style.png")

            page.locator("#profileSpeculativeInterests").scroll_into_view_if_needed()
            page.screenshot(path=docs_output_dir / "screenshot-interest-probe.png")

            page.locator("#tabChat").click()
            page.locator("#viewChat:not([hidden]) .chat-shell").wait_for(
                state="visible",
                timeout=15_000,
            )
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=docs_output_dir / "screenshot-chat.png")
        context.close()


def capture(output_dir: Path, docs_output_dir: Path | None = None) -> list[Path]:
    if docs_output_dir is not None:
        raise ValueError(
            "demo fixture captures must not replace README or GitHub Pages screenshots; "
            "capture those from a real running OpenBiliClaw profile"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = set(EXPECTED)
    for stale in output_dir.glob("*.png"):
        if stale.name not in expected_names:
            stale.unlink()
    blocked: list[str] = []
    with DemoServer() as origin:
        _capture_web(origin, output_dir, blocked, docs_output_dir)
        _capture_extension(origin, output_dir, blocked, docs_output_dir)
    outputs = [output_dir / name for name in EXPECTED]
    if docs_output_dir is not None:
        outputs.extend(docs_output_dir / name for name in DOC_EXPECTED)
    missing = [str(path) for path in outputs if not path.exists()]
    if missing:
        raise RuntimeError(f"capture did not produce expected files: {missing}")
    if any(urlsplit(url).hostname in {"127.0.0.1", "localhost"} for url in blocked):
        raise RuntimeError("loopback request was unexpectedly blocked")
    print(f"Captured {len(outputs)} sanitized UI images; blocked {len(blocked)} external requests.")
    for path in outputs:
        print(path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs/images/chrome-web-store/source",
    )
    args = parser.parse_args()
    capture(args.output_dir.resolve())


if __name__ == "__main__":
    main()
