"""Tests for the cover-image disk cache key primitives and cleanup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw.runtime.image_cache import (
    CoverFetchError,
    cleanup_image_cache,
    fetch_cover_bytes,
    get_or_fetch_cover_bytes,
    image_cache_extension,
    image_cache_key,
    is_allowed_image_url,
    is_cover_cached,
    is_refetchable,
    normalize_cache_url,
    prefetch_cover,
    save_image_bytes,
    select_prefetch_targets,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

BILI = "https://i1.hdslb.com/bfs/archive/abc.jpg"
BILI_PROTO_RELATIVE = "//i2.hdslb.com/bfs/archive/def.jpg"
BILI_HTTP = "http://i2.hdslb.com/bfs/archive/def.jpg"
XHS = (
    "https://sns-webpic-qc.xhscdn.com/202605310127/"
    "08ce340d7be55d7a8e30db2a22c173a3/spectrum/note!nc_n_webp_prv_1"
)
XHS_ROTATED = (
    "https://sns-webpic-qc.xhscdn.com/202606010130/"
    "ffffffffffffffffffffffffffffffff/spectrum/note!nc_n_webp_prv_1"
)
WEIBO = "https://wx1.sinaimg.cn/large/demo.jpg"


# ── Key primitives ────────────────────────────────────────────────


def test_normalize_protocol_relative_and_http_become_https() -> None:
    assert normalize_cache_url(BILI_PROTO_RELATIVE) == "https://i2.hdslb.com/bfs/archive/def.jpg"
    assert normalize_cache_url(BILI_HTTP) == "https://i2.hdslb.com/bfs/archive/def.jpg"


def test_xhs_token_stripped_so_rotation_maps_to_one_key() -> None:
    # The rotating {timestamp}/{token} prefix differs but the path is identical,
    # so both URLs must hash to the same cache key.
    assert image_cache_key(XHS) == image_cache_key(XHS_ROTATED)
    assert "xhscdn.com/spectrum/note" in normalize_cache_url(XHS)


def test_protocol_relative_and_http_share_one_cache_key() -> None:
    assert image_cache_key(BILI_PROTO_RELATIVE) == image_cache_key(BILI_HTTP)


def test_is_refetchable_only_false_for_token_urls() -> None:
    assert is_refetchable(BILI) is True
    assert is_refetchable(BILI_PROTO_RELATIVE) is True
    # A bare xhscdn URL without the token prefix is still re-fetchable.
    assert is_refetchable("https://sns-img.xhscdn.com/static/logo.png") is True
    # The signed/token form is not — the cache is its only durable copy.
    assert is_refetchable(XHS) is False


def test_image_cache_extension_maps_content_type() -> None:
    assert image_cache_extension("image/webp") == "webp"
    assert image_cache_extension("image/jpeg; charset=binary") == "jpeg"
    assert image_cache_extension("application/octet-stream") == "jpg"


# ── Cleanup ───────────────────────────────────────────────────────


class _FakeDB:
    def __init__(self, rows: list[tuple[str, str, bool]]) -> None:
        self._rows = rows

    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]:
        return list(self._rows)


def _write_cover(cache_dir: Path, url: str, *, size: int = 1024, age_days: float = 0.0) -> Path:
    path = cache_dir / f"{image_cache_key(url)}.jpg"
    path.write_bytes(b"x" * size)
    if age_days:
        old = path.stat().st_mtime - age_days * 86400
        os.utime(path, (old, old))
    return path


def test_consumed_unsaved_refetchable_is_evicted(tmp_path: Path) -> None:
    f = _write_cover(tmp_path, BILI)
    db = _FakeDB([(BILI, "shown", False)])
    result = cleanup_image_cache(database=db, cache_dir=tmp_path)
    assert not f.exists()
    assert result.removed == 1
    assert result.removed_consumed == 1
    assert result.freed_bytes == 1024


def test_saved_cover_is_kept(tmp_path: Path) -> None:
    f = _write_cover(tmp_path, BILI)
    db = _FakeDB([(BILI, "shown", True)])  # in favorites / watch-later
    result = cleanup_image_cache(database=db, cache_dir=tmp_path)
    assert f.exists()
    assert result.removed == 0


def test_pending_cover_is_kept(tmp_path: Path) -> None:
    for status in ("fresh", "suppressed"):
        f = _write_cover(tmp_path, BILI)
        db = _FakeDB([(BILI, status, False)])
        result = cleanup_image_cache(database=db, cache_dir=tmp_path)
        assert f.exists(), status
        assert result.removed == 0


def test_unrefetchable_xhs_protected_by_default(tmp_path: Path) -> None:
    f = _write_cover(tmp_path, XHS)
    db = _FakeDB([(XHS, "shown", False)])
    result = cleanup_image_cache(database=db, cache_dir=tmp_path)
    assert f.exists()
    assert result.removed == 0
    assert result.protected_unrefetchable == 1


def test_unrefetchable_xhs_evicted_when_protection_disabled(tmp_path: Path) -> None:
    f = _write_cover(tmp_path, XHS)
    db = _FakeDB([(XHS, "shown", False)])
    result = cleanup_image_cache(database=db, cache_dir=tmp_path, protect_unrefetchable=False)
    assert not f.exists()
    assert result.removed_consumed == 1


def test_aged_orphan_removed_young_orphan_kept(tmp_path: Path) -> None:
    old = _write_cover(tmp_path, BILI, age_days=40)
    young = _write_cover(tmp_path, XHS, age_days=1)
    db = _FakeDB([])  # neither url is referenced by any content row
    result = cleanup_image_cache(database=db, cache_dir=tmp_path, max_age_days=30)
    assert not old.exists()
    assert young.exists()
    assert result.removed_aged_orphans == 1


def test_referenced_needed_cover_kept_even_when_old(tmp_path: Path) -> None:
    # A favorited cover whose file is ancient must NOT be aged out.
    f = _write_cover(tmp_path, BILI, age_days=400)
    db = _FakeDB([(BILI, "shown", True)])
    result = cleanup_image_cache(database=db, cache_dir=tmp_path, max_age_days=30)
    assert f.exists()
    assert result.removed == 0


def test_none_database_only_ages_out_orphans(tmp_path: Path) -> None:
    old = _write_cover(tmp_path, BILI, age_days=40)
    young = _write_cover(tmp_path, XHS, age_days=1)
    result = cleanup_image_cache(database=None, cache_dir=tmp_path, max_age_days=30)
    assert not old.exists()
    assert young.exists()
    assert result.removed_aged_orphans == 1


def test_mixed_states_same_cover_key_prefers_keep(tmp_path: Path) -> None:
    # Same cover URL referenced by two rows: one consumed+unsaved, one pending.
    # The pending reference wins -> cover kept.
    f = _write_cover(tmp_path, BILI)
    db = _FakeDB([(BILI, "shown", False), (BILI, "fresh", False)])
    result = cleanup_image_cache(database=db, cache_dir=tmp_path)
    assert f.exists()
    assert result.removed == 0


# ── Fetch + prefetch ──────────────────────────────────────────────


def test_is_allowed_image_url() -> None:
    assert is_allowed_image_url(BILI) is True
    assert is_allowed_image_url(XHS) is True
    assert is_allowed_image_url("https://lain.bgm.tv/pic/cover/l/demo.jpg") is True
    # TikTok cover CDNs (all three regional suffixes).
    assert (
        is_allowed_image_url("https://p16-sign.tiktokcdn.com/tos-maliva-avt-0068/x~tpl.image")
        is True
    )
    assert (
        is_allowed_image_url("https://p16-sign-va.tiktokcdn-us.com/tos-useast5-avt-0068-tx/x")
        is True
    )
    assert is_allowed_image_url("https://p16-sign.tiktokcdn-eu.com/tos-alisg-avt-0068/x") is True
    assert is_allowed_image_url("https://eviltiktokcdn.com/a.jpg") is False  # boundary
    # content_cache.cover_url forms: protocol-relative and http normalize to https.
    assert is_allowed_image_url(BILI_PROTO_RELATIVE) is True
    assert is_allowed_image_url(BILI_HTTP) is True
    assert is_allowed_image_url("https://example.com/a.jpg") is False
    assert is_allowed_image_url("https://evilhdslb.com/a.jpg") is False  # boundary
    assert is_allowed_image_url("ftp://i1.hdslb.com/a.jpg") is False
    assert is_allowed_image_url("https://user:pass@i1.hdslb.com/a.jpg") is False
    assert is_allowed_image_url("not-a-url") is False


class _FakeResp:
    def __init__(
        self,
        status_code: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._chunks = chunks or []

    async def aiter_bytes(self):  # noqa: ANN202 - test helper
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class _FakeHTTPX:
    def __init__(self) -> None:
        self.responses: dict[str, _FakeResp] = {}
        self.timeouts: set[str] = set()
        self.sent_urls: list[str] = []
        self.sent_headers: list[httpx.Headers] = []
        self.client_kwargs: list[dict[str, object]] = []

    def add(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.responses[url] = _FakeResp(status_code, headers, chunks)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self

        class _Client:
            def __init__(self, *_a: object, **_k: object) -> None:
                fake.client_kwargs.append(dict(_k))

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *_a: object) -> None:
                return None

            def build_request(
                self, method: str, url: str, *, headers: dict[str, str] | None = None
            ) -> httpx.Request:
                return httpx.Request(method, url, headers=headers)

            async def send(self, request: httpx.Request, *, stream: bool = False) -> _FakeResp:
                url = str(request.url)
                fake.sent_urls.append(url)
                fake.sent_headers.append(request.headers)
                if url in fake.timeouts:
                    raise httpx.TimeoutException("timed out", request=request)
                return fake.responses.get(url, _FakeResp(404))

        monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> _FakeHTTPX:
    fake = _FakeHTTPX()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("openbiliclaw.runtime.image_cache._CACHE_DIR", tmp_path)
    return tmp_path


def test_is_cover_cached(cache_dir: Path) -> None:
    assert is_cover_cached(BILI) is False
    save_image_bytes(BILI, b"data", "image/jpeg")
    assert is_cover_cached(BILI) is True
    # Empty file does not count as cached.
    (cache_dir / f"{image_cache_key(XHS)}.jpg").write_bytes(b"")
    assert is_cover_cached(XHS) is False


def test_atomic_save_failure_preserves_old_file_and_removes_temp(
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert save_image_bytes(BILI, b"old", "image/jpeg") is True
    target = next(cache_dir.glob(f"{image_cache_key(BILI)}.*"))

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert save_image_bytes(BILI, b"partial-new", "image/jpeg") is False
    assert target.read_bytes() == b"old"
    assert not list(cache_dir.glob(".*.tmp"))


def test_select_prefetch_targets_filters_dedups_and_prioritizes(cache_dir: Path) -> None:
    _write_cover(cache_dir, BILI_HTTP)  # already cached -> excluded
    candidates = [
        BILI,  # whitelisted, uncached, refetchable
        XHS,  # whitelisted, uncached, UN-refetchable -> must sort first
        "https://example.com/x.jpg",  # non-whitelist -> excluded
        BILI,  # duplicate -> excluded
        BILI_HTTP,  # already cached -> excluded
    ]
    targets = select_prefetch_targets(candidates, max_fetch=10)
    assert targets == [XHS, BILI]  # xhs (fragile) first, cached/non-whitelist/dup dropped


def test_select_prefetch_targets_caps_at_max_fetch(cache_dir: Path) -> None:
    urls = [f"https://i1.hdslb.com/bfs/archive/{i}.jpg" for i in range(10)]
    assert len(select_prefetch_targets(urls, max_fetch=3)) == 3


def test_select_prefetch_targets_deduplicates_rotated_cache_key(cache_dir: Path) -> None:
    assert select_prefetch_targets([XHS, XHS_ROTATED], max_fetch=10) == [XHS]


async def test_fetch_cover_bytes_success(fake_httpx: _FakeHTTPX) -> None:
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "image/webp"}, chunks=[b"webp"])
    data, content_type = await fetch_cover_bytes(XHS)
    assert data == b"webp"
    assert content_type == "image/webp"


async def test_sinaimg_fetch_uses_weibo_referer(fake_httpx: _FakeHTTPX) -> None:
    fake_httpx.add(
        WEIBO,
        status_code=200,
        headers={"content-type": "image/jpeg"},
        chunks=[b"jpeg"],
    )

    data, content_type = await fetch_cover_bytes(WEIBO)

    assert data == b"jpeg"
    assert content_type == "image/jpeg"
    assert fake_httpx.sent_headers[0]["referer"] == "https://weibo.com/"


async def test_sinaimg_referer_is_recomputed_after_redirect(fake_httpx: _FakeHTTPX) -> None:
    redirected = "https://i1.hdslb.com/bfs/archive/redirected.jpg"
    fake_httpx.add(
        WEIBO,
        status_code=302,
        headers={"location": redirected},
    )
    fake_httpx.add(
        redirected,
        status_code=200,
        headers={"content-type": "image/jpeg"},
        chunks=[b"jpeg"],
    )

    await fetch_cover_bytes(WEIBO)

    assert fake_httpx.sent_headers[0]["referer"] == "https://weibo.com/"
    assert "referer" not in fake_httpx.sent_headers[1]


async def test_network_failure_log_never_contains_signed_url(
    fake_httpx: _FakeHTTPX,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.runtime import image_cache

    monkeypatch.setattr(image_cache, "_failure_log_state", {})
    fake_httpx.timeouts.add(XHS)
    caplog.set_level("WARNING", logger="openbiliclaw.runtime.image_cache")

    with pytest.raises(CoverFetchError):
        await fetch_cover_bytes(XHS)

    assert "08ce340d7be55d7a8e30db2a22c173a3" not in caplog.text
    assert XHS not in caplog.text
    assert "host=sns-webpic-qc.xhscdn.com" in caplog.text
    assert f"cache={image_cache_key(XHS)[:12]}" in caplog.text


@pytest.fixture
def _reset_outbound_network() -> Iterator[None]:
    """Isolate openbiliclaw.network module state around a test."""
    from openbiliclaw import network

    try:
        yield
    finally:
        network.reset_outbound_proxy_for_tests()


_GUARD_PROXY = "socks5://127.0.0.1:9999"


@pytest.mark.usefixtures("_reset_outbound_network")
@pytest.mark.parametrize(
    ("mode", "overseas_kwargs"),
    [
        pytest.param("system", {"trust_env": True}, id="system-inherits-env"),
        pytest.param(
            "custom",
            {"proxy": _GUARD_PROXY, "trust_env": False},
            id="custom-explicit-proxy",
        ),
        pytest.param("direct", {"trust_env": False}, id="direct-forces-off"),
    ],
)
async def test_fetch_routes_cn_cdn_direct_and_overseas_by_network_mode(
    fake_httpx: _FakeHTTPX,
    mode: str,
    overseas_kwargs: dict[str, object],
) -> None:
    """CN CDNs bypass env/system proxies under every mode (proxy exit IPs get
    risk-controlled, same failure mode as the Bilibili login probe); overseas
    CDNs follow the process-wide ``[network]`` routing policy instead of raw
    ``trust_env=True``. Regression: under custom mode the configured proxy
    lives in openbiliclaw.network module state and is never written to
    os.environ, so the old hardwired trust_env=True saw no HTTP(S)_PROXY and
    i.ytimg.com was silently fetched direct — timing out from CN networks."""
    from openbiliclaw import network

    network.set_outbound_proxy(_GUARD_PROXY if mode == "custom" else "", mode=mode)

    yt = "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    # Bangumi covers live on lain.bgm.tv, which is Cloudflare-fronted (cf-ray
    # …-SIN edge, IP resolves overseas). A 2026-07-18 curl showed direct fetch
    # timing out while the proxy returned 200 in ~0.5s — the ytimg overseas
    # pattern, NOT the CN-CDN risk-control pattern — so it stays out of
    # _DIRECT_FETCH_HOST_SUFFIXES and rides the [network] policy.
    bgm = "https://lain.bgm.tv/pic/cover/l/65/12/11_bsxG3.jpg"
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "image/webp"}, chunks=[b"a"])
    fake_httpx.add(yt, status_code=200, headers={"content-type": "image/jpeg"}, chunks=[b"b"])
    fake_httpx.add(bgm, status_code=200, headers={"content-type": "image/jpeg"}, chunks=[b"c"])

    await fetch_cover_bytes(XHS)
    await fetch_cover_bytes(yt)
    await fetch_cover_bytes(bgm)

    cn_kwargs = fake_httpx.client_kwargs[0]
    assert cn_kwargs["trust_env"] is False  # xhscdn → direct under every mode
    assert "proxy" not in cn_kwargs

    for index in (1, 2):  # ytimg / lain.bgm.tv → [network] policy
        kwargs = fake_httpx.client_kwargs[index]
        for key, value in overseas_kwargs.items():
            assert kwargs.get(key) == value
        if "proxy" not in overseas_kwargs:
            assert "proxy" not in kwargs


async def test_fetch_cover_bytes_rejects_non_whitelisted() -> None:
    with pytest.raises(CoverFetchError) as exc:
        await fetch_cover_bytes("https://example.com/a.jpg")
    assert exc.value.status_code == 403


async def test_get_or_fetch_cover_bytes_uses_cached_copy(
    cache_dir: Path, fake_httpx: _FakeHTTPX
) -> None:
    save_image_bytes(XHS, b"cached-webp", "image/webp")

    data, content_type = await get_or_fetch_cover_bytes(XHS)

    assert data == b"cached-webp"
    assert content_type == "image/webp"
    assert fake_httpx.sent_urls == []


async def test_get_or_fetch_cover_bytes_fetches_and_caches_on_miss(
    cache_dir: Path, fake_httpx: _FakeHTTPX
) -> None:
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "image/webp"}, chunks=[b"webp"])

    data, content_type = await get_or_fetch_cover_bytes(XHS)

    assert data == b"webp"
    assert content_type == "image/webp"
    assert is_cover_cached(XHS) is True
    assert fake_httpx.sent_urls == [XHS]


async def test_prefetch_cover_caches_then_skips(cache_dir: Path, fake_httpx: _FakeHTTPX) -> None:
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "image/webp"}, chunks=[b"webp"])
    assert await prefetch_cover(XHS) is True
    assert is_cover_cached(XHS) is True
    # Second call is a no-op because it is already cached.
    assert await prefetch_cover(XHS) is False


async def test_prefetch_cover_skips_non_whitelisted(cache_dir: Path) -> None:
    # No fake response registered: a network attempt would 404, but the whitelist
    # check must short-circuit before any request.
    assert await prefetch_cover("https://example.com/a.jpg") is False


async def test_prefetch_cover_swallows_upstream_failure(
    cache_dir: Path, fake_httpx: _FakeHTTPX
) -> None:
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "text/html"}, chunks=[b"<html>"])
    assert await prefetch_cover(XHS) is False
    assert is_cover_cached(XHS) is False


# ── Extension-harvested cover uploads ─────────────────────────────
#
# xhscdn's TLS-fingerprint hotlink protection (2026-07) 403s every
# server-side fetch, so the extension ships cover bytes it fetched in the
# page context. save_extension_cover is the validation gate before those
# attacker-shaped bytes reach the disk cache.


def test_save_extension_cover_writes_valid_payload(cache_dir: Path) -> None:
    import base64

    from openbiliclaw.runtime.image_cache import save_extension_cover

    payload = base64.b64encode(b"webp-bytes").decode()
    assert save_extension_cover(XHS, payload, "image/webp") is True
    assert is_cover_cached(XHS) is True
    # Token-rotated URL of the same cover hits the same cache entry.
    assert is_cover_cached(XHS_ROTATED) is True


def test_save_extension_cover_skips_already_cached(cache_dir: Path) -> None:
    import base64

    from openbiliclaw.runtime.image_cache import save_extension_cover

    save_image_bytes(XHS, b"existing", "image/webp")
    payload = base64.b64encode(b"newer").decode()
    assert save_extension_cover(XHS, payload, "image/webp") is False


@pytest.mark.parametrize(
    ("url", "payload", "content_type"),
    [
        ("https://evil.example/x.jpg", "aGk=", "image/webp"),  # host not whitelisted
        (XHS, "aGk=", "text/html"),  # not an image content type
        (XHS, "aGk=", ""),  # missing content type
        (XHS, "!!!not-base64!!!", "image/webp"),  # undecodable
        (XHS, "", "image/webp"),  # empty payload
    ],
)
def test_save_extension_cover_rejects_invalid(
    cache_dir: Path, url: str, payload: str, content_type: str
) -> None:
    from openbiliclaw.runtime.image_cache import save_extension_cover

    assert save_extension_cover(url, payload, content_type) is False
    assert list(cache_dir.iterdir()) == []


def test_save_extension_cover_rejects_oversize(cache_dir: Path) -> None:
    import base64

    from openbiliclaw.runtime.image_cache import (
        MAX_EXTENSION_COVER_BYTES,
        save_extension_cover,
    )

    payload = base64.b64encode(b"x" * (MAX_EXTENSION_COVER_BYTES + 1)).decode()
    assert save_extension_cover(XHS, payload, "image/jpeg") is False
    assert list(cache_dir.iterdir()) == []


# ── Fetch-failure observability ───────────────────────────────────


def test_cover_fetch_failure_warns_first_then_rate_limits(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First failure per host logs immediately; repeats within the interval
    are suppressed (counted), and the next report carries the count."""
    import logging

    from openbiliclaw.runtime import image_cache

    monkeypatch.setattr(image_cache, "_failure_log_state", {})
    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.image_cache"):
        image_cache._log_cover_fetch_failure(XHS, "Upstream request failed (HTTP 403)")
        image_cache._log_cover_fetch_failure(XHS, "Upstream request failed (HTTP 403)")
        image_cache._log_cover_fetch_failure(XHS, "Upstream request failed (HTTP 403)")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "sns-webpic-qc.xhscdn.com" in warnings[0].message
    assert "HTTP 403" in warnings[0].message

    # After the interval elapses, the next failure reports the suppressed count.
    host = "sns-webpic-qc.xhscdn.com"
    count, _last = image_cache._failure_log_state[host]
    assert count == 2
    image_cache._failure_log_state[host] = (count, -10_000.0)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.image_cache"):
        image_cache._log_cover_fetch_failure(XHS, "Upstream request failed (HTTP 403)")
    assert any("3 failure(s)" in r.message for r in caplog.records)


async def test_fetch_cover_bytes_logs_upstream_status(
    cache_dir: Path,
    fake_httpx: _FakeHTTPX,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hotlink-protection 403 must be visible in logs with the real status."""
    import logging

    from openbiliclaw.runtime import image_cache

    monkeypatch.setattr(image_cache, "_failure_log_state", {})
    fake_httpx.add(XHS, status_code=403, headers={}, chunks=[b""])
    with (
        caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.image_cache"),
        pytest.raises(CoverFetchError) as excinfo,
    ):
        await fetch_cover_bytes(XHS)
    assert "HTTP 403" in excinfo.value.detail
    assert any("HTTP 403" in r.message for r in caplog.records)
