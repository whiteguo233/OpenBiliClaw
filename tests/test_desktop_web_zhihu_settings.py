"""Static regressions for desktop Zhihu source settings."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_web_round_trips_zhihu_source_modes() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for element_id in (
        "zhihuModeSearch",
        "zhihuModeHot",
        "zhihuModeFeed",
        "zhihuModeCreator",
        "zhihuModeRelated",
    ):
        assert f'id="{element_id}"' in html
        assert f'"{element_id}"' in js

    assert "setZhihuSourceModes(config.sources?.zhihu?.source_modes)" in js
    assert "source_modes: collectZhihuSourceModes()" in js
