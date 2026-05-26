"""Static regressions for mobile recommend first-load resilience."""

from pathlib import Path


def test_mobile_recommend_api_requests_have_timeouts() -> None:
    api_js = Path("src/openbiliclaw/web/js/api.js").read_text()

    assert "DEFAULT_READ_TIMEOUT_MS" in api_js
    assert 'requestJson("/recommendations", { timeoutMs: DEFAULT_READ_TIMEOUT_MS })' in api_js
    assert 'requestJson("/runtime-status", { timeoutMs: QUICK_READ_TIMEOUT_MS })' in api_js
    assert "timeoutMs: DEFAULT_READ_TIMEOUT_MS" in api_js


def test_mobile_recommend_initial_load_does_not_wait_forever_on_recommendations() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.js").read_text()

    assert "await fetchRecommendations().catch(() => [])" in recommend_js
    assert "hydrateRecommendSideChannels()" in recommend_js
    assert "const [recs, status, delights, activity] = await Promise.all([" not in recommend_js
    assert "loading = false;" in recommend_js


def test_mobile_badge_load_does_not_fetch_delights_eagerly() -> None:
    chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()

    assert "includeDelights = false" in chat_js
    assert "includeDelights ? fetchDelightBatch(10).catch(() => [])" in chat_js
