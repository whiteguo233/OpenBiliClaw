"""Static contracts shared by desktop, setup wizard, and extension settings."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_HTML = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
DESKTOP_JS = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
SETUP_HTML = (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
POPUP_HTML = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
POPUP_JS = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
POPUP_API_JS = (ROOT / "extension/popup/popup-api.js").read_text(encoding="utf-8")


def test_desktop_exposes_instance_library_global_chain_and_module_chains() -> None:
    for marker in (
        'id="llmInstanceList"',
        'id="addLlmInstance"',
        'id="llmDefaultChain"',
        'id="probeLlmChain"',
        'id="moduleSoulMode"',
        'id="moduleDiscoveryMode"',
        'id="moduleRecommendationMode"',
        'id="moduleEvaluationMode"',
        'id="llmInstanceDialog"',
    ):
        assert marker in DESKTOP_HTML

    assert 'id="llmFallbackSameWarning"' not in DESKTOP_HTML
    assert "一个实例就是完整可调用端点" in DESKTOP_HTML
    assert "默认继承全局链" in DESKTOP_HTML
    assert "未启用（不跟随聊天链）" in DESKTOP_HTML


def test_desktop_serializes_v2_shape_and_probes_exact_instance_or_chain() -> None:
    assert "routing_version: 2" in DESKTOP_JS
    assert "instances: clonePlain(llmDraft.instances)" in DESKTOP_JS
    assert "default_chain: [...llmDraft.default_chain]" in DESKTOP_JS
    assert 'probeConfigService("llm_instance"' in DESKTOP_JS
    assert 'probeConfigService("llm_chain"' in DESKTOP_JS
    assert "function llmInstanceReferences(instanceId)" in DESKTOP_JS
    assert "无法删除：仍被" in DESKTOP_JS
    assert 'data-chain-action="up"' in DESKTOP_JS
    assert 'data-chain-action="down"' in DESKTOP_JS


def test_model_fields_are_editable_comboxes_with_no_write_discovery() -> None:
    for marker in (
        'id="llmInstanceModel" list="llmInstanceModelOptions"',
        'id="refreshLlmInstanceModels"',
        'id="llmInstanceModelDiscoveryStatus"',
        'id="llmInstanceReasoning" list="llmInstanceReasoningOptions"',
    ):
        assert marker in DESKTOP_HTML

    assert 'configModelDiscovery: "/config/discover-models"' in DESKTOP_JS
    assert "function discoverLlmInstanceModels()" in DESKTOP_JS
    assert "当前输入未改动，仍可手填" in DESKTOP_JS
    assert '"openai_compatible"].includes(providerType)' in DESKTOP_JS

    for marker in (
        'id="model" list="modelOptions"',
        'id="refreshModels"',
        'id="modelDiscoveryStatus"',
        'id="reasoningEffort" list="reasoningEffortOptions"',
    ):
        assert marker in SETUP_HTML
    assert '"/api/config/discover-models"' in SETUP_HTML
    assert "function discoverSetupModels()" in SETUP_HTML


def test_instance_editor_never_prefills_a_masked_secret_and_can_explicitly_clear() -> None:
    assert 'setInput("llmInstanceApiKey", "")' in DESKTOP_JS
    assert 'id="llmInstanceApiKey" type="password"' in DESKTOP_HTML
    assert 'id="llmInstanceClearApiKey"' in DESKTOP_HTML
    assert '$("#llmInstanceClearApiKey")?.checked' in DESKTOP_JS
    assert 'config: "/config"' in DESKTOP_JS
    assert 'config: "/config?reveal_keys=true"' not in DESKTOP_JS


def test_guided_setup_writes_or_updates_one_native_instance() -> None:
    assert "let savedLlmInstances = {}" in SETUP_HTML
    assert "routing_version: 2" in SETUP_HTML
    assert "function buildSetupLlmRouting(instanceId, instance)" in SETUP_HTML
    assert "savedBlockingLlmInstanceIds" in SETUP_HTML
    assert "enabled: false" in SETUP_HTML
    assert "!demotedIds.has(id)" in SETUP_HTML
    assert "routes: savedLlmRoutes" in SETUP_HTML
    assert "restart_required" in SETUP_HTML
    assert "SETUP_RESTART_MARKER" in SETUP_HTML
    assert "blockingConfigMessages" in SETUP_HTML


def test_extension_manages_native_instances_and_preserves_module_routes() -> None:
    for marker in (
        'id="cfgLlmRoutingSummary"',
        'id="cfgAddLlmInstance"',
        'id="cfgLlmInstanceList"',
        'id="cfgLlmDefaultChain"',
        'id="cfgLlmInstanceDialog"',
        'id="cfgOpenDesktopModels"',
        'id="cfgProbeLlmChain"',
    ):
        assert marker in POPUP_HTML

    assert "function renderLlmRoutingSummary(llm = null)" in POPUP_JS
    assert "function saveLlmInstanceDraft()" in POPUP_JS
    assert "function deleteLlmInstance(instanceId)" in POPUP_JS
    assert "routing_version: 2" in POPUP_JS
    assert "instances: clonePlain(llmDraft.instances)" in POPUP_JS
    assert "default_chain: [...llmDraft.default_chain]" in POPUP_JS
    assert "routes: Object.fromEntries(" in POPUP_JS
    assert 'probeConfigService("llm_instance", collectForm(), instanceId)' in POPUP_JS
    assert 'probeConfigService("llm_chain", collectForm())' in POPUP_JS
    assert "timeoutMs: 125_000" in POPUP_API_JS
    assert 'setVal("cfgLlmInstanceApiKey", "")' in POPUP_JS
    assert 'id="cfgLlmInstanceClearApiKey"' in POPUP_HTML
    assert 'getInt("cfgLlmConcurrencyV2", 3)' in POPUP_JS
    assert 'getInt("cfgLlmTimeoutV2", 1200)' in POPUP_JS
    assert 'id="cfgLlmTimeoutV2" type="number" min="10" max="1200"' in POPUP_HTML


def test_embedding_fallback_copy_calls_out_vector_space_compatibility() -> None:
    assert "输出维度兼容" in DESKTOP_HTML
    assert "相似度结果漂移" in DESKTOP_HTML
    assert 'id="embeddingFallbackModel"' not in DESKTOP_HTML
    assert 'id="embeddingFallbackApiKey"' not in DESKTOP_HTML
    assert 'id="embeddingFallbackBaseUrl"' not in DESKTOP_HTML
