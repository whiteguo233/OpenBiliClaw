from pathlib import Path


def test_saved_sync_docs_name_default_and_routes() -> None:
    config_doc = Path("docs/modules/config.md").read_text()
    integration_doc = Path("docs/modules/integrations.md").read_text()
    saved_sync_doc = Path("docs/modules/saved-sync.md").read_text()
    storage_doc = Path("docs/modules/storage.md").read_text()
    architecture_doc = Path("docs/architecture.md").read_text()
    spec_doc = Path("docs/spec.md").read_text()
    readme = Path("docs/architecture-overview.md").read_text()
    readme_en = Path("docs/architecture-overview.en.md").read_text()
    docs_index = Path("docs/index.md").read_text()
    e2e_doc = Path("docs/native-save-e2e.md").read_text()
    changelog = Path("docs/changelog.md").read_text()

    assert "[saved_sync]" in config_doc
    assert "auto_sync_enabled = false" in config_doc
    assert "OpenBiliClaw" in integration_doc
    assert "watch_later" in integration_doc
    assert "favorite" in integration_doc
    assert "B站稍后再看" in integration_doc
    assert "B站稍后观看" not in integration_doc
    assert "三个图形化保存界面 + CLI 配置可见" in saved_sync_doc
    assert "三个图形化保存界面 + CLI 配置可见" in architecture_doc
    assert "native-save-e2e.md" in docs_index
    assert "set -Eeuo pipefail" in e2e_doc
    assert "--noproxy '*' --connect-timeout 5 --max-time 30" in e2e_doc
    assert "trap cleanup_native_save_e2e EXIT" in e2e_doc
    assert "trap 'exit 130' INT" in e2e_doc
    assert "OBC_RESTORE_DONE=1" in e2e_doc
    assert "bash --noprofile --norc" in e2e_doc
    assert "OBC_CONFIG_TOUCHED=1" in e2e_doc
    assert "自动同步配置恢复失败" in e2e_doc
    assert "OBC_HEADERS=()" in e2e_doc
    assert "if (( ${#OBC_HEADERS[@]} )); then" in e2e_doc
    assert "Bash 3.2" in e2e_doc
    assert "授权 E2E" in saved_sync_doc
    assert "授权真实账号 E2E" in changelog
    assert "仅本地保存" in saved_sync_doc
    assert "saved_memberships(item_key)" in storage_doc
    assert "canonical `saved_memberships` 保护" in changelog
    assert "question/answer/article" in saved_sync_doc
    assert "知乎真实保存" in changelog
    assert "trap - EXIT INT TERM" in e2e_doc
    assert "(.items | length) > 0" in e2e_doc
    assert "非浏览器 Bearer" in e2e_doc

    for token in (
        "ExtensionNativeSaveBroker",
        "ExtensionNativeSaveJob",
        "ExtensionNativeSaveResultIn",
    ):
        assert token in saved_sync_doc
    for token in (
        "extension_native_save_jobs",
        "create_or_reuse_extension_native_save_job",
        "claim_extension_native_save_job",
        "complete_extension_native_save_job",
        "mark_unclaimed_extension_native_save_job_extension_required",
    ):
        assert token in storage_doc
    assert "扩展原生保存 durable broker" in changelog
    for diagram_doc in (architecture_doc, spec_doc, readme, readme_en):
        assert "ExtensionNativeSaveBroker" in diagram_doc
        assert "extension_native_save_jobs" in diagram_doc


def test_saved_sync_docs_register_extension_adapters_and_completed_executors() -> None:
    architecture = Path("docs/architecture.md").read_text(encoding="utf-8")
    spec = Path("docs/spec.md").read_text(encoding="utf-8")
    module = Path("docs/modules/saved-sync.md").read_text(encoding="utf-8")
    storage = Path("docs/modules/storage.md").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    readme = Path("docs/architecture-overview.md").read_text(encoding="utf-8")
    readme_en = Path("docs/architecture-overview.en.md").read_text(encoding="utf-8")

    for text in (architecture, spec, module, changelog, readme, readme_en):
        assert "unsupported_adapter_missing" in text
    assert "六平台扩展保存 adapter" in architecture
    assert "extension executor 已 6/6 接线" in architecture
    assert "扩展 executor 尚未实现" not in architecture
    assert "Tasks 4–8" not in architecture
    assert "Tasks 4–8" not in module
    assert "Tasks 4–8" not in storage
    assert "runtime broker" in storage
    assert "Phase 1 只注册 Bilibili 账号写入 adapter" not in architecture
    assert "其它来源当前保持 local-only" not in architecture
    assert "账号写入 adapter\n仍是后续独立计划" not in module
    assert "Phase 1 尚无 adapter 的平台" not in module
    assert "直到各平台后续计划实现对应 adapter" not in storage


def test_saved_sync_docs_explain_truthful_graphical_state_interpretation() -> None:
    saved_sync = Path("docs/modules/saved-sync.md").read_text(encoding="utf-8")
    extension = Path("docs/modules/extension.md").read_text(encoding="utf-8")
    recommendation = Path("docs/modules/recommendation.md").read_text(encoding="utf-8")
    config = Path("docs/modules/config.md").read_text(encoding="utf-8")
    architecture = Path("docs/architecture.md").read_text(encoding="utf-8")
    spec = Path("docs/spec.md").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    readme = Path("docs/architecture-overview.md").read_text(encoding="utf-8")
    readme_en = Path("docs/architecture-overview.en.md").read_text(encoding="utf-8")

    for text in (saved_sync, extension, architecture, spec, readme, readme_en):
        assert "unsupported_content_type" in text
        assert "unsupported_adapter_missing" in text
    assert "`pending + 空 sync_task_id`" in saved_sync
    assert "`pending + 非空 sync_task_id`" in saved_sync
    assert "后端状态驱动" in extension
    assert "auto_sync_enabled" in recommendation
    assert "auto_sync_enabled = false" in config
    assert "后端状态驱动" in changelog


def test_six_platform_native_save_docs_define_safe_e2e_contract() -> None:
    saved_sync = Path("docs/modules/saved-sync.md").read_text(encoding="utf-8")
    extension = Path("docs/modules/extension.md").read_text(encoding="utf-8")
    config = Path("docs/modules/config.md").read_text(encoding="utf-8")
    integration = Path("docs/platform-source-integration.md").read_text(encoding="utf-8")
    architecture = Path("docs/architecture.md").read_text(encoding="utf-8")
    spec = Path("docs/spec.md").read_text(encoding="utf-8")
    readme = Path("docs/architecture-overview.md").read_text(encoding="utf-8")
    readme_en = Path("docs/architecture-overview.en.md").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    docs_index = Path("docs/index.md").read_text(encoding="utf-8")
    runbook = Path("docs/testing/six-platform-native-save-e2e.md").read_text(encoding="utf-8")
    runbook_flat = " ".join(runbook.split())

    mapping_rows = (
        "| YouTube | `OpenBiliClaw` | `YouTube Watch Later` |",
        "| Xiaohongshu | `小红书收藏` | `小红书收藏` |",
        "| Douyin | `抖音收藏` | `抖音收藏` |",
        "| X/Twitter | `X Bookmarks` | `X Bookmarks` |",
        "| Zhihu | `OpenBiliClaw` | `OpenBiliClaw` |",
        "| Reddit | `Reddit Saved` | `Reddit Saved` |",
    )
    for row in mapping_rows:
        assert row in runbook
    for token in (
        "allow_state_changing=true",
        "public content_id",
        "expected_target",
        "auto_sync_enabled = false",
        "manual favorite",
        "manual watch-later",
        "explicit auto-sync consent",
        "already_synced",
        "local cleanup",
        "platform saves remain",
        "account IDs",
        "cookies",
        "tokens",
        "HTML",
        "response bodies",
        "URLs with secrets",
    ):
        assert token in runbook_flat
    for field in (
        '"platform"',
        '"action"',
        '"content_id"',
        '"expected_target"',
        '"task_status"',
        '"error_code"',
    ):
        assert field in runbook

    assert "six-platform-native-save-e2e.md" in docs_index
    assert "auto_sync_enabled = false" in config
    assert "精确命名授权" in saved_sync
    assert "登录态只存在于已安装扩展" in extension
    assert "native-save 精确授权记录" in integration
    assert "本地删除不反向删除平台保存" in saved_sync
    assert "六平台原生保存安全 E2E" in changelog

    broker_flow = (
        "extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension"
    )
    for diagram in (architecture, spec, readme, readme_en):
        assert broker_flow in diagram
        assert "OpenBiliClaw" in diagram
        assert "YouTube Watch Later" in diagram

    for text in (saved_sync, extension, runbook):
        for status in (
            "synced",
            "already_synced",
            "login_required",
            "rate_limited",
            "unsupported_content_type",
            "extension_required",
            "failed",
        ):
            assert status in text
