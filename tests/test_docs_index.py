import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _homepage_source() -> str:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script\b[^>]*\bsrc="([^"?#]+)', html)
    return (
        html
        + "\n"
        + "\n".join(
            (ROOT / "docs" / path).read_text(encoding="utf-8")
            for path in scripts
            if not path.startswith(("https://", "http://", "//"))
        )
    )


def test_docs_homepage_mentions_current_platform_sources() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    source_list = re.search(r'id="sources"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert source_list is not None
    source_labels = set(re.findall(r"<(?:li|h3)\b[^>]*>([^<]+)</(?:li|h3)>", source_list.group(1)))
    source_labels = {
        label.replace("X（Twitter）", "X").replace("通用 Web", "Web") for label in source_labels
    }
    assert {
        "B 站",
        "小红书",
        "抖音",
        "YouTube",
        "X",
        "知乎",
        "Reddit",
        "Linux.do",
        "Bangumi",
        "V2EX",
        "微博",
        "GitHub",
        "Web",
    } <= source_labels
    assert f'"softwareVersion": "{project_version}"' in html


def test_docs_homepage_matches_readme_product_positioning() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = _homepage_source()
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    installation_en = (ROOT / "docs/installation.en.md").read_text(encoding="utf-8")

    assert "先理解人，再找内容" in html
    assert "项目内反馈与对话" in html
    assert "心理画像持续深化" in html
    assert "proactively searches" in source
    for client in (
        "浏览器插件",
        "桌面 Web",
        "移动 Web",
        "Flutter 原生客户端",
        "DeepSeek Harness",
    ):
        assert client in html
    assert "同一套推荐、内容库、对话与画像能力" in html
    assert "share recommendations, library, chat, and profile capabilities" in source
    assert "缺失 channel 显示未发布，不回填上一版资产" in installation
    assert (
        "missing channels are shown as unpublished instead of being backfilled" in installation_en
    )
    assert "桌面包如果落后" not in installation
    assert "用户看到的是一个浏览器侧边栏" not in html
    assert "The user-facing surface is a browser sidebar" not in source


def test_linked_installation_guides_explain_weibo_discovery_and_initialization() -> None:
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    installation_en = (ROOT / "docs/installation.en.md").read_text(encoding="utf-8")

    assert "公开发现无需登录" in installation
    assert "个人初始化需在同一浏览器登录 https://weibo.com 并连接插件" in installation
    assert "No login for public discovery" in installation_en
    assert "sign in at https://weibo.com in the extension browser for personal initialization" in (
        installation_en
    )


def test_linked_installation_guides_explain_macos_first_launch_security_bypass() -> None:
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    installation_en = (ROOT / "docs/installation.en.md").read_text(encoding="utf-8")

    for guide in (installation, installation_en):
        assert "OpenBiliClaw-macos-v*-arm64.dmg" in guide
        assert "Control-click" in guide
        assert 'APP="/Applications/OpenBiliClaw.app"' in guide
        assert 'xattr -dr com.apple.quarantine "$APP"' in guide
        assert "README bypass steps" not in guide
    assert "隐私与安全性" in installation
    assert "已损坏" in installation
    assert "Privacy & Security" in installation_en
    assert "is damaged and can't be opened" in installation_en


def test_docs_homepage_does_not_call_github_rest_from_the_browser() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = _homepage_source()

    assert "api.github.com" not in source
    assert "stargazers_count" not in source
    assert "https://github.com/whiteguo233/OpenBiliClaw" in html
