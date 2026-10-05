"""Keep the actual desktop source form aligned with the Instagram contract."""

import re
from pathlib import Path

from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "src/openbiliclaw/web/desktop/index.html"
JS = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"
CSS = ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css"


def test_desktop_source_cards_cover_the_canonical_registry() -> None:
    cards = re.findall(r'data-source-status="([a-z0-9]+)"', HTML.read_text())
    assert set(cards) == set(CANONICAL_SOURCE_FAMILIES)
    assert len(cards) == len(set(cards))


def test_desktop_instagram_settings_round_trip_all_exposed_fields() -> None:
    html, js = HTML.read_text(), JS.read_text()
    for element_id in (
        "instagramEnabled",
        "instagramModeTopic",
        "instagramModeCreator",
        "instagramDailyTopicBudget",
        "instagramDailyCreatorBudget",
        "instagramRequestInterval",
        "instagramMinInterval",
        "instagramBootstrapLimit",
        "shareInstagram",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js
    assert 'data-source-credential="instagram"' in html
    assert 'aria-controls="sourceCardBody-instagram"' in html
    assert (
        "setCheckedValues(INSTAGRAM_SOURCE_MODE_FIELDS, config.sources?.instagram?.source_modes)"
        in js
    )
    assert (
        'source_modes: collectCheckedValues(INSTAGRAM_SOURCE_MODE_FIELDS, ["topic", "creator"])'
        in js
    )
    assert 'instagram: getIntInput("shareInstagram", 1)' in js
    assert 'instagram: $("#instagramEnabled").value === "on"' in js
    assert 'if (shares.instagram !== undefined) setInput("shareInstagram", shares.instagram)' in js
    assert 'setInput("shareInstagram", scheduler.pool_source_shares?.instagram)' in js
    assert 'bootstrap_limit: getIntInput("instagramBootstrapLimit", 300)' in js
    assert 'id="instagramCookie"' not in html


def test_desktop_instagram_source_mark_has_a_visible_background() -> None:
    assert '.source-card-logo[data-source-logo="instagram"] { background:' in CSS.read_text()
