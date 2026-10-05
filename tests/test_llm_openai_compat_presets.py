"""OpenAI 协议兼容 preset 一致性测试（cli 交互子菜单 ↔ agent_bootstrap --llm-preset）。

重点覆盖 issue #193 的商汤日日新（sensenova）免费额度 preset：
- 两张 preset 表键与顺序完全同步（文档按同一顺序展示）
- sensenova 的 base_url / 默认模型与真实环境实测值一致
- ``--llm-preset sensenova`` 解析为 openai_compatible + 实测 endpoint
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import agent_bootstrap as bootstrap  # noqa: E402

from openbiliclaw.cli import _OPENAI_COMPAT_PRESETS  # noqa: E402


def _cli_preset_keys() -> list[str]:
    return [key for key, _ in _OPENAI_COMPAT_PRESETS]


def test_cli_and_bootstrap_preset_keys_stay_in_sync() -> None:
    """交互子菜单与 --llm-preset 的键集合、顺序必须一致（文档按此顺序展示）。"""
    cli_keys = _cli_preset_keys()
    bootstrap_keys = list(bootstrap.HUMAN_OPENAI_COMPAT_PRESETS)
    # cli 的 "custom" 对应 bootstrap 的 "custom"（同为完全手填 escape hatch）。
    assert cli_keys == bootstrap_keys, f"preset 表漂移: cli={cli_keys} bootstrap={bootstrap_keys}"
    assert len(cli_keys) == 10, (
        "新增 preset 时记得同步 docs 里的子菜单清单（agent-install / openclaw-quickstart）"
    )


def test_sensenova_preset_matches_validated_endpoint() -> None:
    """sensenova preset 的 endpoint / 模型与真实环境实测值一致（issue #193）。"""
    presets = dict(_OPENAI_COMPAT_PRESETS)
    assert "sensenova" in presets
    preset = presets["sensenova"]
    assert preset["base_url"] == "https://token.sensenova.cn/v1"
    assert preset["default_model"] == "deepseek-v4-flash"
    # token 推理端点未验证 /v1/embeddings —— 必须如实标注，引导独立 Ollama bge-m3。
    assert preset["supports_embedding"] == "false"
    assert "bge-m3" in preset["embedding_alt"]
    # 免费额度是该 preset 的存在理由（issue #193），文案必须提及。
    assert "免费" in preset["description"]


def test_sensenova_in_bootstrap_llm_presets() -> None:
    assert bootstrap.LLM_PRESETS["sensenova"] == {
        "base_url": "https://token.sensenova.cn/v1",
        "model": "deepseek-v4-flash",
    }


def test_llm_preset_sensenova_resolves_openai_compatible(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--llm-preset", "sensenova"]
    )
    assert bootstrap.apply_llm_preset_args(args) is None
    assert args.provider == "openai_compatible"
    assert args.llm_base_url == "https://token.sensenova.cn/v1"
    assert args.llm_model == "deepseek-v4-flash"
