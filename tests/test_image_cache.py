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
                pass

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


async def test_fetch_cover_bytes_success(fake_httpx: _FakeHTTPX) -> None:
    fake_httpx.add(XHS, status_code=200, headers={"content-type": "image/webp"}, chunks=[b"webp"])
    data, content_type = await fetch_cover_bytes(XHS)
    assert data == b"webp"
    assert content_type == "image/webp"


async def test_fetch_cover_bytes_rejects_non_whitelisted() -> None:
    with pytest.raises(CoverFetchError) as exc:
        await fetch_cover_bytes("https://example.com/a.jpg")
    assert exc.value.status_code == 403


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
