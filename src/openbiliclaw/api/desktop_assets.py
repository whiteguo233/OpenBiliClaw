"""Serve the desktop bootstrap as one ordered, precompressed static asset."""

from __future__ import annotations

import gzip
import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.responses import Response

if TYPE_CHECKING:
    from pathlib import Path

# Source files remain independent for the mobile UI, extension, and development
# fixtures. This manifest must match the desktop HTML's dependency order.
DESKTOP_SCRIPTS = (
    "/web/assets/js/mobile-qr.js",
    "/shared/mobile-connection.js",
    "/web/assets/js/pending-actions.js",
    "/web/assets/js/saved-sync-core.js",
    "/shared/dialogue-confirmation.js",
    "/shared/agent-chat.js",
    "/shared/source-status.js",
    "/shared/i18n.js",
    "/shared/director-client.js",
    "/shared/jev-settings.js",
    "/web/assets/js/chat-agent-core.js",
    "/web/assets/js/fish-settings.js",
    "/web/assets/js/app.js",
)
_STYLESHEETS = (
    "/web/assets/css/app.css",
    "/web/assets/css/classic.css",
    "/shared/jev-settings.css",
)
_SCRIPT_TAG = re.compile(
    r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>\s*</script>', re.IGNORECASE
)
_BUNDLE_URL = "/web/assets/desktop-bundle.js"


@dataclass(frozen=True)
class DesktopBuild:
    """One immutable page and its matching script representations."""

    version: str
    html: str
    javascript: bytes
    gzip_javascript: bytes


def _encoding(accept_encoding: str) -> str | None:
    """Negotiate gzip without overriding explicit zero-quality preferences."""
    qualities: dict[str, float] = {}
    for item in accept_encoding.lower().split(","):
        coding, *parameters = item.strip().split(";")
        coding = coding.strip()
        if not coding:
            continue
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.strip().partition("=")
            if separator and name.strip() == "q":
                try:
                    parsed = float(value)
                    quality = parsed if 0 <= parsed <= 1 else 0.0
                except ValueError:
                    quality = 0.0
        qualities[coding] = quality
    gzip_quality = qualities.get("gzip", qualities.get("*", 0.0))
    identity_quality = qualities.get("identity", 0.0 if qualities.get("*") == 0 else 1.0)
    if gzip_quality > 0 and gzip_quality >= identity_quality:
        return "gzip"
    if identity_quality > 0:
        return "identity"
    return None


class DesktopAssets:
    """Cache at most two complete builds; only fixed package paths are read."""

    def __init__(self, web_root: Path) -> None:
        self._root = web_root
        self._lock = threading.RLock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._current: DesktopBuild | None = None
        self._builds: OrderedDict[str, DesktopBuild] = OrderedDict()

    def _path(self, url: str) -> Path:
        # This helper receives only constants from the manifest, never a URL or
        # filename supplied by a request.
        if url.startswith("/web/"):
            return self._root / "desktop" / url.removeprefix("/web/")
        return self._root / "shared" / url.removeprefix("/shared/")

    def current(self) -> DesktopBuild:
        """Reuse a build until any packaged input changes on disk."""
        with self._lock:
            urls = ("/web/index.html", *DESKTOP_SCRIPTS, *_STYLESHEETS)
            paths = [(url, self._path(url)) for url in urls]
            signature = []
            for url, path in paths:
                stat = path.stat()
                signature.append((url, stat.st_mtime_ns, stat.st_size))
            if self._current is not None and tuple(signature) == self._signature:
                return self._current

            inputs = {url: path.read_bytes() for url, path in paths}
            html = inputs["/web/index.html"].decode("utf-8")
            if tuple(_SCRIPT_TAG.findall(html)) != DESKTOP_SCRIPTS:
                raise ValueError("desktop script manifest does not match index.html order")
            digest = hashlib.sha256()
            for url, content in inputs.items():
                digest.update(url.encode("utf-8") + b"\0")
                digest.update(content + b"\0")
            version = digest.hexdigest()[:20]
            retained = self._builds.get(version)
            if retained is not None:
                self._builds.move_to_end(version)
                self._signature = tuple(signature)
                self._current = retained
                return retained
            javascript = b"\n".join(
                b";\n/* " + url.encode("utf-8") + b" */\n" + inputs[url] + b"\n;"
                for url in DESKTOP_SCRIPTS
            )
            tags = iter(
                [f'<script src="{_BUNDLE_URL}?v={version}" defer></script>']
                + [""] * (len(DESKTOP_SCRIPTS) - 1)
            )
            html = _SCRIPT_TAG.sub(lambda _match: next(tags), html)
            for url in _STYLESHEETS:
                html = html.replace(f'href="{url}"', f'href="{url}?v={version}"')
            build = DesktopBuild(
                version=version,
                html=html,
                javascript=javascript,
                gzip_javascript=gzip.compress(javascript, compresslevel=6, mtime=0),
            )
            self._builds[version] = build
            while len(self._builds) > 2:
                self._builds.popitem(last=False)
            self._signature = tuple(signature)
            self._current = build
            return build

    def response(
        self, version: str | None, *, accept_encoding: str, if_none_match: str = ""
    ) -> Response:
        """Return the exact retained version, never new bytes under an old URL."""
        with self._lock:
            current = self.current()
            build = self._builds.get(version) if version is not None else current
        if build is None:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        encoding = _encoding(accept_encoding)
        if encoding is None:
            return Response(
                status_code=406,
                headers={"Cache-Control": "no-store", "Vary": "Accept-Encoding"},
            )
        etag = f'"{build.version}-{encoding}"'
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable" if version else "no-cache",
            "ETag": etag,
            "Vary": "Accept-Encoding",
            "X-Content-Type-Options": "nosniff",
            # Explicit identity is necessary: the outer GZipMiddleware checks
            # for the word gzip, ignoring q=0, unless an encoding is present.
            "Content-Encoding": encoding,
        }
        if any(
            value.strip().removeprefix("W/") in {etag, "*"} for value in if_none_match.split(",")
        ):
            return Response(status_code=304, headers=headers)
        return Response(
            build.gzip_javascript if encoding == "gzip" else build.javascript,
            media_type="application/javascript",
            headers=headers,
        )
