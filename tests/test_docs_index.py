import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_homepage_mentions_current_platform_sources() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    assert "Reddit 推荐" in html
    assert "sourceRedditTitle" in html
    assert "sourceRedditText" in html
    assert "Bangumi 推荐" in html
    assert "sourceBangumiTitle" in html
    assert "sourceBangumiText" in html
    assert "V2EX 推荐" in html
    assert "sourceV2exTitle" in html
    assert "sourceV2exText" in html
    assert "sourceLinuxdoTitle" in html
    assert "sourceLinuxdoText" in html
    assert "sourceWeiboTitle" in html
    assert "sourceWeiboText" in html
    assert "十三类平台来源（含实验性 Instagram）与开放 Web" in html
    assert "Thirteen platform sources (including experimental Instagram) and the open web" in html
    source_cards = re.findall(r'data-source="([a-z0-9_-]+)"', html)
    assert source_cards == [
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "x",
        "zhihu",
        "reddit",
        "linuxdo",
        "bangumi",
        "v2ex",
        "weibo",
        "github",
        "instagram",
        "web",
    ]
    assert "sourceYoutubeTitle" in html
    assert "sourceYoutubeText" in html
    assert "sourceXTitle" in html
    assert "sourceXText" in html
    assert "sourceGithubTitle" in html
    assert "sourceGithubText" in html
    assert "sourceInstagramTitle" in html
    assert "sourceInstagramText" in html
    assert "默认关闭、尚未随版本发布" in html
    assert "disabled by default and not yet released" in html
    assert "docs/platform-source-acceptance.instagram.md" in html
    assert "Linux.do、V2EX、微博等十一类来源" not in html
    assert "Weibo, and eight other platform sources" not in html
    assert "登录微博后可在初始化时通过同源只读任务导入收藏、关注和互动" in html
    assert f'"softwareVersion": "{project_version}"' in html


def test_docs_homepage_matches_readme_product_positioning() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "通用个性化内容推荐 · 本地运行 · 只为你一个人构建" in html
    assert "喜欢、不感兴趣和聊天反馈都会改变后续推荐" in html
    assert "一套本地后端，五种使用入口" in html
    assert "浏览器插件、桌面 Web、移动 Web、Flutter 原生客户端和 DSH 客户端插件" in html
    assert "One local backend, five ways to use it" in html
    assert "DeepSeek Harness 客户端插件" in html
    assert "某个 channel 尚未发布时会显示未发布，不会回填上一版资产" in html
    assert "桌面包如果落后" not in html
    assert "用户看到的是一个浏览器侧边栏" not in html
    assert "The user-facing surface is a browser sidebar" not in html


def test_docs_homepage_chinese_weibo_translation_is_chinese() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert html.count("公开 discovery 覆盖 search / hot / creator") == 2
    assert html.count("Public discovery covers search, hot, and creator") == 1


def test_docs_homepage_mentions_macos_first_launch_security_bypass() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "OpenBiliClaw-macos-v*-arm64.dmg" in html
    assert "Control-click" in html
    assert "隐私与安全性" in html
    assert "已损坏" in html
    assert "xattr -dr com.apple.quarantine /Applications/OpenBiliClaw.app" in html
    assert "README bypass steps" not in html


def test_docs_homepage_does_not_call_github_rest_from_the_browser() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    assert "api.github.com" not in html
    assert "stargazers_count" not in html
    assert "https://github.com/whiteguo233/OpenBiliClaw" in html
