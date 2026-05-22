from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shell_installers_recommend_same_default_llm_provider() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")

    expected_default = "Choose your LLM provider (default: deepseek):"
    expected_supported = "Supported: deepseek | openai | gemini | claude | openrouter | ollama"

    assert expected_default in install_sh
    assert expected_default in install_ps1
    assert expected_supported in install_sh
    assert expected_supported in install_ps1
    assert "DeepSeek:   https://platform.deepseek.com/api_keys" in install_sh
    assert "DeepSeek:   https://platform.deepseek.com/api_keys" in install_ps1


def test_install_sh_uses_interactive_auto_init_contract() -> None:
    install_sh = _read("scripts/install.sh")

    assert "--interactive-confirm" in install_sh
    assert "--wait-for-extension-cookie" in install_sh
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_sh


def test_install_ps1_uses_interactive_auto_init_contract() -> None:
    install_ps1 = _read("scripts/install.ps1")

    assert "--interactive-confirm" in install_ps1
    assert "--wait-for-extension-cookie" in install_ps1
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_ps1


def test_one_line_installers_default_to_lan_accessible_backend() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert 'HOST="${HOST:-0.0.0.0}"' in install_sh
    assert "HOST             API host  (default: 0.0.0.0)" in install_sh
    assert "Backend bind address. Default: 0.0.0.0" in install_ps1
    assert "if (-not $ApiHost)    { $ApiHost    = '0.0.0.0' }" in install_ps1
    assert 'DEFAULT_HOST = "0.0.0.0"' in bootstrap
    assert "default: 0.0.0.0" in bootstrap


def test_docs_make_auto_init_primary_for_all_install_channels() -> None:
    readme = _read("README.md")
    docker_doc = _read("docs/docker-deployment.md")
    agent_doc = _read("docs/agent-install.md")

    assert "自动运行 init" in readme
    assert "agent_bootstrap.py --mode docker" in docker_doc
    assert "init_complete" in agent_doc
    assert "手动 fallback" in docker_doc


def test_agent_install_llm_menu_numbering_matches_current_options() -> None:
    doc = _read("docs/agent-install.md")

    assert "Present **seven top-level options**" in doc
    assert "Present **three top-level options**" not in doc
    assert 'is folded into "Advanced" further down' not in doc
    assert "**Hardware caveat for option 7 (Ollama)**" in doc
    assert "#### Options 3-6 (OpenAI 官方 / Gemini / Claude / OpenRouter)" in doc
    assert "#### Option 2 (OpenAI 官方 / Gemini / Claude / OpenRouter)" not in doc


def test_cli_module_docs_show_current_init_llm_menu() -> None:
    doc = _read("docs/modules/cli.md")
    bootstrap = _read("scripts/agent_bootstrap.py")

    assert "1   DeepSeek 官方 ★默认推荐" in doc
    assert "2   ★ 第二推荐 — 中转站 / OpenAI 协议兼容服务" in doc
    assert "3   OpenAI 官方" in doc
    assert "Tip:不确定就选 1 (DeepSeek)" in doc
    assert "请输入序号或名称（默认 1=DeepSeek） [1]:" in doc
    assert "1   本地 Ollama bge-m3 ★默认推荐" in doc
    assert "3   暂不启用 embedding" in doc
    assert "不会跟随主 LLM" in doc
    assert "| 1 | 本地 Ollama，自动探测 + 拉取 `bge-m3` |" in doc
    assert "Ollama 排第一" not in doc
    assert "1) 跟随你刚才选的 LLM" not in doc
    assert "跟随主 provider（默认）" not in doc
    assert "User picked OpenAI 官方 (option 2 in agent-install.md)" not in bootstrap


def test_backend_tag_workflow_does_not_publish_backend_packages() -> None:
    workflow = _read(".github/workflows/release-backend.yml")
    docs_index = _read("docs/index.md")
    extension_doc = _read("docs/modules/extension.md")

    assert "backend-v*" in workflow
    assert "Validate Backend Source Tag" in workflow
    assert "Verify backend version matches source tag" in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "upload-artifact" not in workflow
    assert "Build backend release archive" not in workflow
    assert "Publish backend release" not in workflow

    assert "后端源码更新看 `backend-v*` tag，不发布后端桌面包" in docs_index
    assert "后端桌面包不走 GitHub Release 分发" in extension_doc
