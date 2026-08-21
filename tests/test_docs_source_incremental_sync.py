"""Documentation contract for extension-online source account refresh."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INTERVAL_FIELDS = (
    "source_incremental_enabled",
    "source_incremental_hours",
    "xhs_incremental_hours",
    "douyin_incremental_hours",
    "youtube_incremental_hours",
    "zhihu_incremental_hours",
    "reddit_incremental_hours",
    "linuxdo_incremental_hours",
    "v2ex_incremental_hours",
)


def _read(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def test_source_incremental_docs_cover_configuration_and_six_source_staging() -> None:
    config_docs = _read("docs/modules/config.md")
    config_example = _read("config.example.toml")
    storage_docs = _read("docs/modules/storage.md")

    for field in _INTERVAL_FIELDS:
        assert field in config_docs
        assert field in config_example
    assert "source_incremental_enabled = false" in config_example
    assert "douyin_incremental_hours = 0" in config_example
    assert "默认关闭" in config_docs
    assert "### 六来源任务结果 staging" in storage_docs
    for queue_name in (
        "XhsTaskQueue",
        "DyTaskQueue",
        "YtTaskQueue",
        "ZhihuTaskQueue",
        "RedditTaskQueue",
        "LinuxdoTaskQueue",
        "V2EXTaskQueue",
    ):
        assert queue_name in storage_docs
    assert "### 五来源任务结果 staging" not in storage_docs
    assert "### 四来源任务结果 staging" not in storage_docs


def test_source_incremental_docs_state_online_and_atomic_admission_boundaries() -> None:
    architecture = _read("docs/architecture.md")
    extension_docs = _read("docs/modules/extension.md")
    readme = _read("docs/architecture-overview.md")
    readme_en = _read("docs/architecture-overview.en.md")

    assert "BEGIN IMMEDIATE" in architecture
    assert "XHS→抖音→YouTube→知乎→Reddit→Linux.do" in architecture
    assert "XHS→抖音→YouTube→知乎→Reddit→V2EX" in architecture
    assert "不是后端绕过浏览器登录态" in extension_docs
    assert "source_incremental_enabled=false" in extension_docs
    assert "douyin_incremental_hours=0" in extension_docs
    assert "扩展在线周期回拉" in readme
    assert "账号周期回拉默认关闭" in _read("README.md")
    assert "extension-online periodic re-pull" in readme_en.lower()
    assert "periodic re-pull (off by default" in readme_en.lower()
