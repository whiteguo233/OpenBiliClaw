# Human One-Line Install Wizard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the human `curl ... install.sh | bash` flow collect the same install choices as the AI one-line flow before dependencies, backend start, service checks, and init.

**Architecture:** Keep `scripts/agent_bootstrap.py` as the single bootstrap state machine and implement the human wizard there with Python standard library only. Shell and PowerShell continue to only detect interactivity and pass `--interactive-confirm --wait-for-extension-cookie`; bootstrap reconciles the final cookie mode and disables extension waiting for manual/existing cookie paths. Non-interactive and AI-agent flag-driven installs keep the existing status contract.

**Tech Stack:** Bash, PowerShell, Python standard library, pytest, Ruff, MyPy, existing `BOOTSTRAP_STATUS` contract.

---

## Constraints

- Do not use Typer/Rich from `src/openbiliclaw/cli.py` in the bootstrap wizard; dependencies may not be installed yet.
- Do not echo API keys or cookies in shell logs, `BOOTSTRAP_STATUS`, or summary text.
- Keep `--llm-preset` behavior for non-interactive agents unchanged.
- Human OpenAI-compatible / relay installs write concrete values to provider `openai_compatible`, matching the codebase's first-class provider and its explicit `base_url` invariant. The legacy non-interactive `--llm-preset` path remains unchanged in this phase.
- Convert `getpass.GetPassWarning` into a hard failure. Never fall back to echoed secret input.
- Preserve the existing non-TTY guard for `--interactive-confirm`; the wizard must fail fast instead of blocking on `input()`.
- Do not touch unrelated dirty files such as `packaging/*`, packaging workflows, or packaging tests.

## Task 1: Add Human Wizard Answer Types And Prompt Primitives

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests near the existing interactive confirmation tests:

```python
def test_human_install_choice_parser_accepts_numbers_and_aliases() -> None:
    assert bootstrap.resolve_human_llm_choice("") == "deepseek"
    assert bootstrap.resolve_human_llm_choice("1") == "deepseek"
    assert bootstrap.resolve_human_llm_choice("2") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("relay") == "openai_compatible"
    assert bootstrap.resolve_human_llm_choice("ollama") == "ollama"
    assert bootstrap.resolve_human_llm_choice("bad") is None


def test_secret_presence_label_never_includes_secret_value() -> None:
    assert "sk-test" not in bootstrap.mask_secret_for_prompt("sk-test")
    assert bootstrap.mask_secret_for_prompt("") == "not set"


def test_collect_human_install_wizard_refuses_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_human_install_wizard()


def test_human_install_answers_reject_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        bootstrap.HumanInstallAnswers(provider="openai-compat")
```

**Step 2: Verify the tests fail**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "human_install_choice_parser or secret_presence or refuses_without_tty or reject_unknown_provider" -v
```

Expected: FAIL because the helpers do not exist.

**Step 3: Implement minimal helpers**

In `scripts/agent_bootstrap.py`, add after `LLM_PRESETS`:

```python
HUMAN_LLM_MENU: tuple[tuple[str, str, str], ...] = (
    ("deepseek", "DeepSeek 官方 ★默认推荐", "deepseek-v4-flash"),
    ("openai_compatible", "★ 中转站 / OpenAI 协议兼容服务", "relay preset"),
    ("openai", "OpenAI 官方", "gpt-5-nano"),
    ("gemini", "Gemini 官方", "gemini-2.5-flash"),
    ("claude", "Claude 官方", "claude-sonnet-4-6"),
    ("openrouter", "OpenRouter 聚合", "openai/gpt-5-nano"),
    ("ollama", "本地 Ollama", "qwen2.5:7b"),
)

PROVIDER_MODEL_DEFAULTS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-5-nano",
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-6",
    "openrouter": "openai/gpt-5-nano",
    "ollama": "qwen2.5:7b",
}


@dataclass(frozen=True)
class HumanInstallAnswers:
    provider: str
    llm_api_key: str = ""
    llm_base_url: str | None = None
    llm_model: str | None = None
    embedding_provider: str = "ollama"
    embedding_model: str = "bge-m3"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    xhs: bool = False
    douyin: bool = False
    youtube: bool = False
    cookie_mode: str = "extension"
    bilibili_cookie: str = ""
    bilibili_favorite_limit: int = DEFAULT_BILIBILI_FAVORITE_LIMIT
    bilibili_follow_limit: int = DEFAULT_BILIBILI_FOLLOW_LIMIT

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unknown provider: {self.provider}")


def mask_secret_for_prompt(value: str) -> str:
    return "set, press Enter to reuse" if value.strip() else "not set"


def ensure_human_wizard_tty(input_func: Any) -> None:
    if input_func is input and not sys.stdin.isatty():
        raise RuntimeError("interactive confirmation requires a terminal")


def resolve_human_llm_choice(raw: str) -> str | None:
    value = raw.strip().lower()
    if not value:
        return "deepseek"
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(HUMAN_LLM_MENU):
            return HUMAN_LLM_MENU[index - 1][0]
        return None
    aliases = {
        "relay": "openai_compatible",
        "oneapi": "openai_compatible",
        "openai-compatible": "openai_compatible",
        "openai_compatible": "openai_compatible",
        "openai-compat": "openai_compatible",
        "compat": "openai_compatible",
    }
    return aliases.get(value, value if value in {key for key, _, _ in HUMAN_LLM_MENU} else None)
```

**Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "human_install_choice_parser or secret_presence or refuses_without_tty or reject_unknown_provider" -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only commit if the execution session is using commits and the user approved committing from the dirty worktree:

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: add human install wizard primitives"
```

## Task 2: Implement LLM Provider Collection

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests:

```python
def test_collect_human_llm_defaults_to_deepseek() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["", ""])
    secret_inputs = iter(["sk-deepseek"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "deepseek"
    assert answer.llm_api_key == "sk-deepseek"
    assert answer.llm_model == "deepseek-v4-flash"
    assert answer.llm_base_url is None
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_openai_compat_relay_collects_triplet() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["2", "", "https://relay.example/v1", ""])
    secret_inputs = iter(["sk-relay"])
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "openai_compatible"
    assert answer.provider in bootstrap.SUPPORTED_PROVIDERS
    assert answer.llm_base_url == "https://relay.example/v1"
    assert answer.llm_api_key == "sk-relay"
    assert answer.llm_model == "gpt-5-nano"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_llm_ollama_needs_no_api_key() -> None:
    plain_inputs = iter(["7", "qwen2.5:7b"])
    secret_prompts: list[str] = []
    answer = bootstrap.collect_human_llm_config(
        input_func=lambda _prompt: next(plain_inputs),
        secret_input_func=lambda prompt: secret_prompts.append(prompt) or "",
    )

    assert answer.provider == "ollama"
    assert answer.llm_api_key == ""
    assert answer.llm_model == "qwen2.5:7b"
    assert secret_prompts == []


def test_collect_human_llm_does_not_reuse_key_across_providers() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter(["3", ""])  # choose OpenAI, accept default model
    secret_inputs = iter(["", "sk-openai"])

    answer = bootstrap.collect_human_llm_config(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
        existing_provider="deepseek",
        existing_api_key="sk-old-deepseek",
    )

    assert answer.provider == "openai"
    assert answer.llm_api_key == "sk-openai"
    assert all("press Enter to reuse" not in prompt for _kind, prompt in prompts)


def test_prompt_secret_converts_getpass_warning_to_runtime_error() -> None:
    import getpass

    def raise_getpass_warning(_prompt: str) -> str:
        raise getpass.GetPassWarning("echo cannot be disabled")

    with pytest.raises(RuntimeError, match="cannot disable terminal echo"):
        bootstrap._prompt_secret(raise_getpass_warning, "API Key")
```

**Step 2: Verify the tests fail**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "collect_human_llm or prompt_secret" -v
```

Expected: FAIL because `collect_human_llm_config` does not exist.

**Step 3: Implement LLM collection**

Add:

```python
def _prompt_required(input_func: Any, prompt: str, *, default: str = "") -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = str(input_func(f"{prompt}{suffix}: ")).strip() or default
        if value:
            return value
        print("This value is required.")


def _prompt_optional(input_func: Any, prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return str(input_func(f"{prompt}{suffix}: ")).strip() or default


def _prompt_secret(
    secret_input_func: Any,
    prompt: str,
    *,
    existing: str = "",
    required: bool = True,
) -> str:
    import getpass

    while True:
        suffix = f" ({mask_secret_for_prompt(existing)})" if existing else ""
        try:
            value = str(secret_input_func(f"{prompt}{suffix}: ")).strip()
        except getpass.GetPassWarning as exc:
            raise RuntimeError(
                f"cannot disable terminal echo for secret prompt: {exc}"
            ) from exc
        if value:
            return value
        if existing:
            return ""
        if not required:
            return ""
        print("API key is required for this provider.")


def read_secret_no_echo(prompt: str) -> str:
    import getpass
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning as exc:
            raise RuntimeError(
                f"cannot disable terminal echo for secret prompt: {exc}"
            ) from exc


def collect_human_llm_config(
    *,
    input_func: Any = input,
    secret_input_func: Any | None = None,
    existing_provider: str = "deepseek",
    existing_api_key: str = "",
    existing_base_url: str = "",
    existing_model: str = "",
) -> HumanInstallAnswers:
    ...
```

Implementation notes:
- Import `getpass` and default `secret_input_func` to `read_secret_no_echo`.
- In production, default `secret_input_func` to `read_secret_no_echo`, not raw
  `getpass.getpass`, so `GetPassWarning` becomes a hard failure before a secret
  can be read with terminal echo enabled.
- Print the seven-option menu in plain text.
- Re-prompt until `resolve_human_llm_choice()` returns a valid choice.
- For `openai_compatible`, show a submenu using existing `LLM_PRESETS`; support relay as default and collect base URL, API key, and model.
- For official remote providers, collect API key and model only.
- For `ollama`, collect model only and no API key.
- Only pass an `existing_api_key` into `_prompt_secret` when the selected
  provider equals `existing_provider`; switching providers makes the new
  provider's key required and shows no "press Enter to reuse" affordance.
- Return a `HumanInstallAnswers` with only LLM fields populated.

**Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "collect_human_llm or prompt_secret" -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only if commits are enabled:

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: collect llm choices in human installer"
```

## Task 3: Implement Embedding, Source, Limits, And Cookie Collection

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests:

```python
def test_collect_human_install_wizard_default_path() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter([
        "",              # LLM DeepSeek
        "",              # LLM model
        "",              # embedding Ollama
        "",              # favorite limit
        "",              # follow limit
        "",              # xhs no
        "",              # douyin no
        "",              # youtube no
        "",              # extension cookie
    ])
    secret_inputs = iter(["sk-deepseek"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "deepseek"
    assert answer.embedding_provider == "ollama"
    assert answer.embedding_model == "bge-m3"
    assert answer.bilibili_favorite_limit == 300
    assert answer.bilibili_follow_limit == 100
    assert answer.xhs is False
    assert answer.douyin is False
    assert answer.youtube is False
    assert answer.cookie_mode == "extension"
    assert any(kind == "secret" and "API Key" in prompt for kind, prompt in prompts)


def test_collect_human_install_wizard_manual_cookie() -> None:
    prompts: list[tuple[str, str]] = []
    plain_inputs = iter([
        "7", "qwen2.5:7b",
        "3",
        "120", "80",
        "n", "y", "n",
        "manual",
    ])
    secret_inputs = iter(["SESSDATA=test; bili_jct=test; DedeUserID=1"])

    answer = bootstrap.collect_human_install_wizard(
        input_func=lambda prompt: prompts.append(("plain", prompt)) or next(plain_inputs),
        secret_input_func=lambda prompt: prompts.append(("secret", prompt)) or next(secret_inputs),
    )

    assert answer.provider == "ollama"
    assert answer.embedding_provider == ""
    assert answer.embedding_model == ""
    assert answer.douyin is True
    assert answer.cookie_mode == "manual"
    assert answer.bilibili_cookie.startswith("SESSDATA=")
    assert any(kind == "secret" and "Cookie" in prompt for kind, prompt in prompts)
```

**Step 2: Verify the tests fail**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "collect_human_install_wizard" -v
```

Expected: FAIL because full wizard collection does not exist.

**Step 3: Implement full collection**

Add:

```python
def _collect_human_embedding_config(
    input_func: Any,
    secret_input_func: Any,
) -> tuple[str, str, str | None, str | None]:
    ...


def collect_human_install_wizard(
    *,
    input_func: Any = input,
    secret_input_func: Any | None = None,
    existing_provider: str = "deepseek",
    existing_api_key: str = "",
    existing_base_url: str = "",
    existing_model: str = "",
) -> HumanInstallAnswers:
    ensure_human_wizard_tty(input_func)
    llm = collect_human_llm_config(
        input_func=input_func,
        secret_input_func=secret_input_func,
        existing_provider=existing_provider,
        existing_api_key=existing_api_key,
        existing_base_url=existing_base_url,
        existing_model=existing_model,
    )
    embedding_provider, embedding_model, embedding_base_url, embedding_api_key = (
        _collect_human_embedding_config(input_func, secret_input_func)
    )
    favorite_limit = _ask_non_negative_int(input_func, "Max Bilibili favorites to import", default=300)
    follow_limit = _ask_non_negative_int(input_func, "Max Bilibili followed creators to import", default=100)
    xhs = _ask_yes_no(input_func, "Include Xiaohongshu likes/favorites?", default=False)
    douyin = _ask_yes_no(input_func, "Include Douyin post/favorite/like/follow data?", default=False)
    youtube = _ask_yes_no(input_func, "Include YouTube history/subscriptions/likes?", default=False)
    ...
```

Implementation notes:
- Embedding menu options:
  - `1` or empty: `ollama`, `bge-m3`
  - `2`: `gemini`, `gemini-embedding-001`, ask Gemini key
  - `3`: disabled with provider and model empty
  - `4`: custom OpenAI-compatible embedding, ask base URL, API key, model
  - `5`: advanced provider, ask provider/model and optional base URL/key
- Cookie menu options:
  - empty or `extension`: no cookie flag, set `cookie_mode="extension"`
  - `manual`: collect cookie with `secret_input_func`
  - `existing`: do not pass a cookie, set `cookie_mode="existing"`
- Keep `collect_interactive_confirmations()` for unit compatibility or make it call a subset helper; do not remove existing tests.

**Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "collect_human_install_wizard or interactive_answers" -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only if commits are enabled:

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: collect full human install choices"
```

## Task 4: Apply Human Answers To Bootstrap Args

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests:

```python
def test_apply_human_install_answers_sets_all_bootstrap_args(tmp_path: Path) -> None:
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])
    answers = bootstrap.HumanInstallAnswers(
        provider="deepseek",
        llm_api_key="sk-test",
        llm_model="deepseek-v4-flash",
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test",
        bilibili_favorite_limit=120,
        bilibili_follow_limit=80,
    )

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.provider == "deepseek"
    assert args.llm_api_key == "sk-test"
    assert args.llm_model == "deepseek-v4-flash"
    assert args.embedding_provider == "ollama"
    assert args.embedding_model == "bge-m3"
    assert args.no_xhs is True
    assert args.yes_douyin is True
    assert args.no_youtube is True
    assert args.bilibili_cookie == "SESSDATA=test"
    assert args.bilibili_favorite_limit == 120
    assert args.bilibili_follow_limit == 80


def test_apply_human_install_answers_does_not_clear_existing_secret_on_empty_answer(
    tmp_path: Path,
) -> None:
    args = bootstrap.build_arg_parser().parse_args(
        ["--project-dir", str(tmp_path), "--llm-api-key", "explicit"]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek", llm_api_key="")

    bootstrap.apply_human_install_answers_to_args(args, answers)

    assert args.llm_api_key == "explicit"


def test_apply_human_install_answers_reconciles_extension_wait_flag(tmp_path: Path) -> None:
    for cookie_mode, expected_wait in [
        ("extension", True),
        ("manual", False),
        ("existing", False),
    ]:
        args = bootstrap.build_arg_parser().parse_args(
            [
                "--project-dir",
                str(tmp_path),
                "--wait-for-extension-cookie",
            ]
        )
        answers = bootstrap.HumanInstallAnswers(
            provider="deepseek",
            cookie_mode=cookie_mode,
            bilibili_cookie="SESSDATA=test" if cookie_mode == "manual" else "",
        )

        bootstrap.apply_human_install_answers_to_args(args, answers)

        assert args.wait_for_extension_cookie is expected_wait
```

**Step 2: Verify the tests fail**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "apply_human_install_answers" -v
```

Expected: FAIL because the helper does not exist.

**Step 3: Implement apply helper**

Add:

```python
def apply_human_install_answers_to_args(
    args: argparse.Namespace,
    answers: HumanInstallAnswers,
) -> None:
    if answers.provider not in SUPPORTED_PROVIDERS:
        raise RuntimeError(f"unknown provider from human install wizard: {answers.provider}")
    if args.provider is None:
        args.provider = answers.provider
    if args.llm_api_key is None and answers.llm_api_key:
        args.llm_api_key = answers.llm_api_key
    if args.llm_base_url is None and answers.llm_base_url is not None:
        args.llm_base_url = answers.llm_base_url
    if args.llm_model is None and answers.llm_model is not None:
        args.llm_model = answers.llm_model
    if args.embedding_provider is None:
        args.embedding_provider = answers.embedding_provider
    if args.embedding_model is None:
        args.embedding_model = answers.embedding_model
    if args.embedding_base_url is None and answers.embedding_base_url is not None:
        args.embedding_base_url = answers.embedding_base_url
    if args.embedding_api_key is None and answers.embedding_api_key:
        args.embedding_api_key = answers.embedding_api_key
    ...
```

Implementation notes:
- Set source flags only when neither side of the mutually exclusive pair was already passed.
- Set `args.wait_for_extension_cookie = (answers.cookie_mode == "extension")`
  unconditionally. This intentionally overrides the shell installers'
  pre-wizard `--wait-for-extension-cookie` flag for `manual` and `existing`.
- Set `args.bilibili_cookie` only for manual cookie with a non-empty cookie.
- Do not overwrite explicit command-line flags.

**Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "apply_human_install_answers" -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only if commits are enabled:

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: apply human install choices to bootstrap"
```

## Task 5: Integrate The Human Wizard Into `run()`

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing integration test**

Add a test that monkeypatches the wizard and verifies the existing config write pipeline receives those values:

```python
def test_run_interactive_confirm_collects_full_human_install_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(bootstrap, "ensure_repo_checkout", lambda project_dir, _repo_url, _branch: project_dir)
    monkeypatch.setattr(bootstrap, "ensure_config_toml", lambda _project_dir: tmp_path / "config.toml")
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: bootstrap.HumanInstallAnswers(
            provider="deepseek",
            llm_api_key="sk-new",
            llm_model="deepseek-v4-flash",
            embedding_provider="ollama",
            embedding_model="bge-m3",
            xhs=False,
            douyin=False,
            youtube=False,
            cookie_mode="manual",
            bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
        ),
    )
    monkeypatch.setattr(bootstrap, "ensure_ollama_ready", lambda _models: {"running": True, "pulled": ["bge-m3"]})

    returncode = bootstrap.run(args)

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    output = capsys.readouterr().out

    assert returncode == 0
    assert 'default_provider = "deepseek"' in text
    assert 'api_key = "sk-new"' in text
    assert 'provider = "ollama"' in text
    assert args.wait_for_extension_cookie is False
    assert "sk-new" not in output
    assert "SESSDATA=test" not in output


def test_run_interactive_confirm_without_tty_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )

    monkeypatch.setattr(bootstrap, "ensure_repo_checkout", lambda project_dir, _repo_url, _branch: project_dir)
    monkeypatch.setattr(bootstrap, "ensure_config_toml", lambda _project_dir: tmp_path / "config.toml")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    assert returncode == 2
    assert "interactive confirmation requires a terminal" in output


def test_run_interactive_confirm_getpass_warning_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    monkeypatch.setattr(bootstrap, "ensure_repo_checkout", lambda project_dir, _repo_url, _branch: project_dir)
    monkeypatch.setattr(bootstrap, "ensure_config_toml", lambda _project_dir: tmp_path / "config.toml")
    monkeypatch.setattr(
        bootstrap,
        "collect_human_install_wizard",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cannot disable terminal echo for secret prompt")
        ),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]


def test_run_interactive_confirm_apply_error_returns_interactive_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_config(tmp_path)
    args = bootstrap.build_arg_parser().parse_args(
        [
            "--project-dir",
            str(tmp_path),
            "--mode",
            "local",
            "--skip-install",
            "--skip-start",
            "--interactive-confirm",
        ]
    )
    answers = bootstrap.HumanInstallAnswers(provider="deepseek")

    monkeypatch.setattr(bootstrap, "ensure_repo_checkout", lambda project_dir, _repo_url, _branch: project_dir)
    monkeypatch.setattr(bootstrap, "ensure_config_toml", lambda _project_dir: tmp_path / "config.toml")
    monkeypatch.setattr(bootstrap, "collect_human_install_wizard", lambda **_kwargs: answers)
    monkeypatch.setattr(
        bootstrap,
        "apply_human_install_answers_to_args",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("unknown provider")),
    )

    returncode = bootstrap.run(args)

    output = capsys.readouterr().out
    status_lines = [
        json.loads(line.removeprefix("BOOTSTRAP_STATUS: "))
        for line in output.splitlines()
        if line.startswith("BOOTSTRAP_STATUS: ")
    ]
    assert returncode == 2
    assert status_lines[-1]["status"] == "error"
    assert status_lines[-1]["details"]["step"] == "interactive_confirm"
    assert "unexpected" not in status_lines[-1]["message"]
```

**Step 2: Verify the test fails**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "run_interactive_confirm_collects_full_human_install_choices" -v
```

Expected: FAIL because `run()` still calls `collect_interactive_confirmations()`.

**Step 3: Update `run()`**

Replace the current `if args.interactive_confirm:` block with:

```python
    if args.interactive_confirm:
        try:
            current = detect_missing_secrets(project_dir)
            provider = str(current.get("provider") or "deepseek")
            provider_cfg = read_simple_toml(project_dir / "config.toml").get("llm", {}).get(provider, {})
            answers = collect_human_install_wizard(
                existing_provider=provider,
                existing_api_key=str(provider_cfg.get("api_key", "") or ""),
                existing_base_url=str(provider_cfg.get("base_url", "") or ""),
                existing_model=str(provider_cfg.get("model", "") or ""),
            )
            apply_human_install_answers_to_args(args, answers)
        except RuntimeError as exc:
            emit(BootstrapResult("error", str(exc), {"step": "interactive_confirm"}))
            return 2
        emit(
            BootstrapResult(
                "ok",
                "human_install_choices_set",
                {
                    "provider": args.provider,
                    "llm_model": args.llm_model,
                    "embedding_provider": args.embedding_provider,
                    "embedding_model": args.embedding_model,
                    "xhs": "yes" if args.yes_xhs else "no",
                    "douyin": "yes" if args.yes_douyin else "no",
                    "youtube": "yes" if args.yes_youtube else "no",
                    "cookie_mode": answers.cookie_mode,
                },
            )
        )
```

Implementation notes:
- Keep `collect_interactive_confirmations()` and `apply_confirmation_answers_to_args()` for compatibility with existing tests unless all callers are migrated.
- The status event must not include API key, cookie, or secret-bearing URLs except base URL if already shown to the user. Safer: omit base URL from the status event.
- Because the wizard writes concrete provider/base URL/model values, it must not rely on the `main()` `--llm-preset` resolution block.
- The `existing_*` values passed from `run()` are scoped to the detected
  existing provider only. `collect_human_llm_config()` is responsible for
  discarding them when the user selects a different provider, so "press Enter to
  reuse" is never shown for a provider that does not own the existing key.
- Keep `apply_human_install_answers_to_args()` inside the same
  `try/except RuntimeError` as collection. Defensive provider-validation errors
  should be reported as `step=interactive_confirm` rather than falling through
  to the outer unexpected-exception handler.
- Preserve the existing `RuntimeError("interactive confirmation requires a terminal")`
  behavior for non-TTY runs and emit it as an `error` `BOOTSTRAP_STATUS`.
- `GetPassWarning` must already be converted to `RuntimeError` before it
  reaches this block, so `run()` returns 2 with `step=interactive_confirm`
  instead of falling through to the outer unexpected-exception handler.

**Step 4: Run targeted integration tests**

Run:

```bash
uv run pytest tests/test_agent_bootstrap.py -k "run_interactive_confirm or collect_human or apply_human" -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only if commits are enabled:

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: run human install wizard before bootstrap"
```

## Task 6: Update Installer Contract Tests And Docs

**Files:**
- Modify: `tests/test_install_contract_docs.py`
- Modify: `docs/agent-install.md`
- Modify: `docs/docker-deployment.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`
- Optional Modify: `README.md`, `README_EN.md` only if the top-level install callout describes the old two-step behavior.

**Step 1: Write failing contract assertions**

Update or add:

```python
def test_human_installers_run_full_terminal_wizard_before_init() -> None:
    install_sh = _read("scripts/install.sh")
    install_ps1 = _read("scripts/install.ps1")
    bootstrap = _read("scripts/agent_bootstrap.py")
    agent_doc = _read("docs/agent-install.md")

    assert "--interactive-confirm" in install_sh
    assert "--interactive-confirm" in install_ps1
    assert "human_install_choices_set" in bootstrap
    assert "human one-line installer asks LLM provider first" in agent_doc
    assert "openai_compatible" in bootstrap
    assert "GetPassWarning" in bootstrap
```

**Step 2: Verify the contract test fails**

Run:

```bash
uv run pytest tests/test_install_contract_docs.py -k "human_installers_run_full_terminal_wizard" -v
```

Expected: FAIL until docs and status event text are updated.

**Step 3: Update docs**

Required doc updates:
- `docs/agent-install.md`: state that human `install.sh` / `install.ps1` ask LLM provider first, then embedding/source/cookie, while AI-agent non-interactive installs still use flags and `BOOTSTRAP_STATUS`.
- `docs/agent-install.md`: state that human option 2 writes to
  `[llm.openai_compatible]`, while existing non-interactive `--llm-preset`
  remains a compatibility path for AI-agent prompts.
- `docs/docker-deployment.md`: clarify that Docker/agent bootstrap still performs service checks before init, and human shell installers now collect choices inline.
- `docs/modules/cli.md`: align the install-channel description with the new full human wizard.
- `docs/changelog.md`: add a short entry under the current version.

**Step 4: Run docs contract tests**

Run:

```bash
uv run pytest tests/test_install_contract_docs.py -v
```

Expected: PASS.

**Step 5: Commit checkpoint**

Only if commits are enabled:

```bash
git add tests/test_install_contract_docs.py docs/agent-install.md docs/docker-deployment.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: document human one-line install wizard"
```

## Task 7: Run Static Checks And Full Test Suite

**Files:**
- No file edits expected.

**Step 1: Run focused checks**

Run:

```bash
uv run ruff check scripts/agent_bootstrap.py tests/test_agent_bootstrap.py tests/test_install_contract_docs.py
uv run pytest tests/test_agent_bootstrap.py tests/test_install_contract_docs.py -v
```

Expected: PASS.

**Step 2: Verify secret capture defenses**

Add or run a focused regression that monkeypatches the secret reader to return a
known sentinel such as `sk-should-not-appear`, runs the interactive path, and
asserts the sentinel is absent from captured stdout and any bootstrap summary
log parser input used by `install.sh` / `install.ps1`.

Expected: PASS. The sentinel must not appear in `BOOTSTRAP_STATUS`, human logs,
or installer summary parsing.

**Step 3: Run repo checks**

Run:

```bash
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
git diff --check
```

Expected: all PASS.

**Step 4: Fix failures**

If a check fails, use @superpowers:systematic-debugging for the failure before changing implementation.

## Task 8: Real Human One-Line Install Smoke

**Files:**
- No repo edits expected, unless the smoke finds a bug.

**Step 1: Prepare an isolated install target**

Run with a temporary checkout and a non-default port:

```bash
tmpdir=$(mktemp -d /tmp/openbiliclaw-human-install.XXXXXX)
PORT=18421 INSTALL_DIR="$tmpdir/app" REUSE_FROM= OPENBILICLAW_REPO_URL="file:///Users/white/workspace/OpenBiliClaw" OPENBILICLAW_BRANCH="$(git branch --show-current)" bash scripts/install.sh
```

Expected: installer opens the human wizard before dependency install, with LLM provider as the first question.

**Step 2: Feed a deterministic answer path**

Use a pseudo-terminal runner such as `script` or `expect` so the installer sees a TTY. Recommended smoke path:

- LLM: `7` Ollama
- Chat model: a local model already present, or the smallest acceptable test model
- Embedding: `3` disabled if no local Ollama embedding model should be pulled during smoke, otherwise `1`
- Favorites: `0`
- Follows: `0`
- XHS/Douyin/YouTube: default no
- Cookie: `existing` only if `REUSE_FROM` has a valid cookie, otherwise manual test cookie for a controlled failure, or extension path for manual observation

Expected:
- No API key or cookie appears in installer logs.
- Service checks run before init.
- If a real LLM and cookie are provided, init completes.
- If smoke intentionally skips a real cookie, bootstrap ends in the existing cookie-missing path and does not crash.

**Step 3: Clean up**

Run:

```bash
pkill -f "openbiliclaw.*18421" || true
rm -rf "$tmpdir"
```

Expected: port is free and temp checkout is removed.

## Task 9: Final Review

**Files:**
- No file edits expected unless review finds an issue.

**Step 1: Inspect diff**

Run:

```bash
git diff -- scripts/agent_bootstrap.py scripts/install.sh scripts/install.ps1 tests/test_agent_bootstrap.py tests/test_install_contract_docs.py docs/agent-install.md docs/docker-deployment.md docs/modules/cli.md docs/changelog.md
```

Expected:
- No secret values in tests or docs.
- `install.sh` and `install.ps1` remain thin wrappers.
- `BOOTSTRAP_STATUS` payloads include no secrets.
- Human wizard path does not affect `OPENBILICLAW_NONINTERACTIVE=1` or CI.

**Step 2: Summarize result**

Report:
- What changed.
- Which checks passed.
- Whether real one-line smoke completed or which controlled missing-secret state it reached.
- Any remaining manual validation, especially extension cookie sync.
