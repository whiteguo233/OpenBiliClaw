from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_homepage_mentions_reddit_source() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "Reddit 推荐" in html
    assert "sourceRedditTitle" in html
    assert "sourceRedditText" in html
    assert "知乎 / Reddit 登录态任务桥" in html
    assert "Zhihu, Reddit, and Web sources" in html
    assert '"softwareVersion": "0.3.149"' in html
