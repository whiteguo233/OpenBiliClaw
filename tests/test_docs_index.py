import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_homepage_mentions_current_platform_sources() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    source_list = re.search(r'id="sources".*?<ul\b[^>]*>(.*?)</ul>', html, re.DOTALL)
    assert source_list is not None
    source_labels = set(re.findall(r"<li\b[^>]*>([^<]+)</li>", source_list.group(1)))
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
    assert (
        'href="https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/installation.md"' in html
    )
    assert (
        'data-href-en="https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/installation.en.md"'
        in html
    )
    assert f'"softwareVersion": "{project_version}"' in html


def test_docs_homepage_matches_readme_product_positioning() -> None:
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    script = (ROOT / "docs/assets/home.js").read_text(encoding="utf-8")
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    installation_en = (ROOT / "docs/installation.en.md").read_text(encoding="utf-8")

    assert "在你的电脑上运行" in html
    assert "解释为什么推荐给你" in html
    assert "根据你的反馈持续调整" in html
    assert "explains each recommendation and learns from your feedback" in script
    for client in (
        "浏览器插件",
        "桌面 Web",
        "手机浏览器",
        "Flutter 原生客户端",
        "DeepSeek Harness",
    ):
        assert client in html
    assert "它们都需要连接运行中的后端" in html
    assert "Every client connects to a running backend" in script
    assert "缺失 channel 显示未发布，不回填上一版资产" in installation
    assert (
        "missing channels are shown as unpublished instead of being backfilled" in installation_en
    )
    assert "桌面包如果落后" not in installation
    assert "用户看到的是一个浏览器侧边栏" not in html
    assert "The user-facing surface is a browser sidebar" not in html + script


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
    script = (ROOT / "docs/assets/home.js").read_text(encoding="utf-8")

    assert "api.github.com" not in html + script
    assert "stargazers_count" not in html + script
    assert "https://github.com/whiteguo233/OpenBiliClaw" in html
