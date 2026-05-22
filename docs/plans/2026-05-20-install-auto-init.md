# Install Auto-Init Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Bash, PowerShell, AI-agent, Docker, and manual-source install channels guide the user through required confirmations and automatically run init once credentials and choices are available.

**Architecture:** Keep `agent_bootstrap.py` as the single state machine. Add an explicit interactive-confirm mode for human-run installers, keep non-interactive JSON/status behavior for AI agents, and teach Docker mode to sync confirmed config into the container runtime before running container init.

**Tech Stack:** Python stdlib argparse/subprocess/urllib, Bash, PowerShell 5.1-compatible scripting, Typer CLI, pytest, existing docs contract tests.

---

### Task 1: Lock Current Auto-Init Contract With Tests

**Files:**
- Modify: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests for current contract**

Add tests that document the intended default behavior before changing implementation:

```python
def test_init_decisions_required_for_all_optional_sources(tmp_path: Path) -> None:
    _write_minimal_config(tmp_path, embedding_provider="ollama", embedding_model="bge-m3")
    args = bootstrap.build_arg_parser().parse_args(["--project-dir", str(tmp_path)])

    decisions = bootstrap.detect_init_decisions(tmp_path, args, embedding_touched=False)

    assert decisions["missing"] == ["xhs", "douyin", "youtube"]


def test_build_init_command_appends_all_source_flags_for_local(tmp_path: Path) -> None:
    command = bootstrap.build_init_command(
        "local", tmp_path, "--no-xhs", "--no-douyin", "--yes-youtube"
    )

    assert command[-4:] == ["init", "--no-xhs", "--no-douyin", "--yes-youtube"]
```

**Step 2: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/test_agent_bootstrap.py
```

Expected: existing tests pass; new tests may pass immediately if behavior already exists. Keep them because they guard the expanded flow.

**Step 3: Commit**

```bash
git add tests/test_agent_bootstrap.py
git commit -m "test: lock install bootstrap init decisions"
```

### Task 2: Add Interactive Confirmation Mode To Bootstrap

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests around pure helper functions rather than spawning real prompts:

```python
def test_interactive_answers_apply_source_flags(tmp_path: Path) -> None:
    answers = bootstrap.InitConfirmationAnswers(
        embedding_provider="ollama",
        embedding_model="bge-m3",
        xhs=False,
        douyin=True,
        youtube=False,
        cookie_mode="manual",
        bilibili_cookie="SESSDATA=test; bili_jct=test; DedeUserID=1",
    )

    argv = bootstrap.confirmation_answers_to_bootstrap_args(answers)

    assert "--embedding-provider" in argv
    assert "--yes-douyin" in argv
    assert "--no-xhs" in argv
    assert "--no-youtube" in argv
    assert "--bilibili-cookie" in argv


def test_prompt_mode_requires_tty_when_no_answers_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "open_prompt_input", lambda: None)

    with pytest.raises(RuntimeError, match="interactive confirmation requires a terminal"):
        bootstrap.collect_interactive_confirmations()
```

**Step 2: Implement minimal helpers**

Add:

```python
@dataclass(frozen=True)
class InitConfirmationAnswers:
    embedding_provider: str
    embedding_model: str
    xhs: bool
    douyin: bool
    youtube: bool
    cookie_mode: str  # "extension" | "manual" | "existing"
    bilibili_cookie: str = ""


def confirmation_answers_to_bootstrap_args(answers: InitConfirmationAnswers) -> list[str]:
    args = [
        "--embedding-provider",
        answers.embedding_provider,
        "--embedding-model",
        answers.embedding_model,
        "--yes-xhs" if answers.xhs else "--no-xhs",
        "--yes-douyin" if answers.douyin else "--no-douyin",
        "--yes-youtube" if answers.youtube else "--no-youtube",
    ]
    if answers.cookie_mode == "manual" and answers.bilibili_cookie:
        args.extend(["--bilibili-cookie", answers.bilibili_cookie])
    return args
```

Add parser flags:

```python
parser.add_argument(
    "--interactive-confirm",
    action="store_true",
    help="Ask required init confirmations from the terminal before auto-init.",
)
parser.add_argument(
    "--wait-for-extension-cookie",
    action="store_true",
    help="After backend health, wait for the browser extension to sync Bilibili cookie.",
)
```

Interactive mode should:

- Ask embedding choice when no explicit embedding is present. Default: `ollama` / `bge-m3`.
- Ask XHS, Douyin, YouTube separately. Default: no.
- Ask Bilibili auth method if cookie is missing. Default: extension sync.
- Ask for manual cookie only if the user chooses manual.
- Never silently opt in optional sources.

**Step 3: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/test_agent_bootstrap.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: add interactive init confirmations to bootstrap"
```

### Task 3: Wait For Extension Cookie Then Continue Init

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add polling tests with injected detector:

```python
def test_wait_for_cookie_sync_returns_when_cookie_appears(tmp_path: Path) -> None:
    calls = {"count": 0}

    def detector(_project_dir: Path) -> dict[str, object]:
        calls["count"] += 1
        missing = ["bilibili.cookie"] if calls["count"] == 1 else []
        return {"missing": missing}

    assert bootstrap.wait_for_cookie_sync(
        tmp_path,
        timeout_seconds=1,
        interval_seconds=0,
        detector=detector,
    ) is True


def test_wait_for_cookie_sync_times_out(tmp_path: Path) -> None:
    assert bootstrap.wait_for_cookie_sync(
        tmp_path,
        timeout_seconds=0.01,
        interval_seconds=0,
        detector=lambda _project_dir: {"missing": ["bilibili.cookie"]},
    ) is False
```

**Step 2: Implement local cookie wait helper**

Add:

```python
def wait_for_cookie_sync(
    project_dir: Path,
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
    detector: Callable[[Path], dict[str, Any]] = detect_missing_secrets,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        missing = detector(project_dir).get("missing", [])
        if "bilibili.cookie" not in missing:
            return True
        time.sleep(interval_seconds)
    return False
```

Wire it after backend health:

- If `--wait-for-extension-cookie` is set and final missing secrets are exactly `["bilibili.cookie"]`, print extension instructions, poll for cookie, then recompute final status and continue to init.
- If timeout expires, emit `needs_secrets` with `bilibili.cookie` and a retry command.

**Step 3: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/test_agent_bootstrap.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: continue init after extension cookie sync"
```

### Task 4: Make Docker Mode Use Container Runtime Config

**Files:**
- Modify: `scripts/agent_bootstrap.py`
- Test: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests for command construction**

Add pure tests for Docker sync commands:

```python
def test_docker_runtime_config_copy_commands(tmp_path: Path) -> None:
    commands = bootstrap.build_docker_runtime_sync_commands(tmp_path)

    assert ["docker", "cp", str(tmp_path / "config.toml"), "openbiliclaw-backend:/app/runtime/config.toml"] in commands
    assert any("bilibili_cookie.json" in " ".join(command) for command in commands)


def test_docker_secret_detector_command_reads_runtime_config() -> None:
    command = bootstrap.build_docker_missing_secrets_command()

    assert command[:3] == ["docker", "exec", "openbiliclaw-backend"]
    assert "/app/runtime/config.toml" in " ".join(command)
```

**Step 2: Implement Docker config sync**

Add:

```python
DOCKER_CONTAINER_NAME = "openbiliclaw-backend"
DOCKER_RUNTIME_ROOT = "/app/runtime"


def build_docker_runtime_sync_commands(project_dir: Path) -> list[list[str]]:
    commands = [
        [
            "docker",
            "cp",
            str(project_dir / "config.toml"),
            f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/config.toml",
        ]
    ]
    cookie_file = project_dir / "data" / "bilibili_cookie.json"
    if cookie_file.exists():
        commands.append(
            [
                "docker",
                "cp",
                str(cookie_file),
                f"{DOCKER_CONTAINER_NAME}:{DOCKER_RUNTIME_ROOT}/data/bilibili_cookie.json",
            ]
        )
    return commands
```

After `docker_compose_up()` and health success:

- Copy host `config.toml` into `/app/runtime/config.toml`.
- Copy `data/bilibili_cookie.json` when present.
- Rebuild/reload can happen implicitly on the next `docker exec openbiliclaw init` process because CLI reads runtime config at process start.

Add a Docker detector for extension-cookie wait:

- Use `docker exec openbiliclaw-backend python -c "<small script>"`.
- The script reads `/app/runtime/config.toml` and `/app/runtime/data/bilibili_cookie.json`.
- It prints JSON shaped like `detect_missing_secrets()`.

**Step 3: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/test_agent_bootstrap.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
git commit -m "feat: sync docker runtime config before auto init"
```

### Task 5: Update Bash Installer To Drive Interactive Auto-Init

**Files:**
- Modify: `scripts/install.sh`
- Test: `tests/test_install_contract_docs.py`

**Step 1: Write failing contract tests**

Add assertions:

```python
def test_install_sh_uses_interactive_auto_init_contract() -> None:
    install_sh = _read("scripts/install.sh")

    assert "--interactive-confirm" in install_sh
    assert "--wait-for-extension-cookie" in install_sh
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_sh
```

**Step 2: Modify installer args**

In `run_bootstrap()`, append these flags by default when not in CI/non-interactive override:

```bash
if [ -z "${OPENBILICLAW_NONINTERACTIVE:-}" ] && [ -z "${CI:-}" ]; then
    args+=(--interactive-confirm --wait-for-extension-cookie)
fi
```

Keep `SKIP_START` behavior unchanged.

Update status block text:

- `needs_decisions`: "continue the printed bootstrap command; do not run bare init".
- cookie-only missing: "install extension; installer/bootstrap will continue init after sync" where applicable.
- final success: only say install complete when `init_complete` was emitted.

**Step 3: Run tests and shell syntax**

Run:

```bash
bash -n scripts/install.sh
.venv/bin/pytest -q tests/test_install_contract_docs.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/install.sh tests/test_install_contract_docs.py
git commit -m "feat: make bash installer drive auto init"
```

### Task 6: Update PowerShell Installer To Mirror Bash

**Files:**
- Modify: `scripts/install.ps1`
- Test: `tests/test_install_contract_docs.py`

**Step 1: Write failing contract tests**

Extend the previous test:

```python
def test_install_ps1_uses_interactive_auto_init_contract() -> None:
    install_ps1 = _read("scripts/install.ps1")

    assert "--interactive-confirm" in install_ps1
    assert "--wait-for-extension-cookie" in install_ps1
    assert "docker exec -it openbiliclaw-backend openbiliclaw init" not in install_ps1
```

**Step 2: Modify PowerShell args**

In `Invoke-Bootstrap`, add:

```powershell
if (-not $env:OPENBILICLAW_NONINTERACTIVE -and -not $env:CI) {
    $args += '--interactive-confirm'
    $args += '--wait-for-extension-cookie'
}
```

Keep the `-SkipStart` switch unchanged.

**Step 3: Run tests**

Run:

```bash
.venv/bin/pytest -q tests/test_install_contract_docs.py
```

If `pwsh` exists locally, also run:

```bash
pwsh -NoProfile -Command '$ErrorActionPreference="Stop"; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts/install.ps1), [ref]$null) | Out-Null'
```

Expected: pytest PASS. PowerShell syntax check PASS when available.

**Step 4: Commit**

```bash
git add scripts/install.ps1 tests/test_install_contract_docs.py
git commit -m "feat: make powershell installer drive auto init"
```

### Task 7: Update Documentation Contract

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/agent-install.md`
- Modify: `docs/agent-deployment.md`
- Modify: `docs/docker-deployment.md`
- Modify: `docs/openclaw-quickstart.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Test: `tests/test_install_contract_docs.py`

**Step 1: Write failing docs tests**

Add assertions:

```python
def test_docs_make_auto_init_primary_for_all_install_channels() -> None:
    readme = _read("README.md")
    docker_doc = _read("docs/docker-deployment.md")
    agent_doc = _read("docs/agent-install.md")

    assert "自动运行 init" in readme
    assert "agent_bootstrap.py --mode docker" in docker_doc
    assert "init_complete" in agent_doc
    assert "手动 fallback" in docker_doc
```

**Step 2: Update docs**

Make these content changes:

- README Chinese/English: install scripts and Docker path say init is automatic after confirmations.
- Docker guide: primary quickstart uses `agent_bootstrap.py --mode docker`; manual `docker exec ... init` moves to fallback/troubleshooting.
- Agent install/deployment docs: `needs_decisions` and cookie wait are not final success states; agents must continue until `init_complete` or explicit blocker.
- OpenClaw guide: replace primary manual init step with bootstrap-driven init.
- CLI/config docs: state direct `openbiliclaw init` is advanced/manual rerun path.
- Changelog: add a top entry for installer auto-init convergence.

**Step 3: Run docs tests**

Run:

```bash
.venv/bin/pytest -q tests/test_install_contract_docs.py
```

Expected: PASS.

**Step 4: Commit**

```bash
git add README.md README_EN.md docs/agent-install.md docs/agent-deployment.md docs/docker-deployment.md docs/openclaw-quickstart.md docs/modules/cli.md docs/modules/config.md docs/changelog.md tests/test_install_contract_docs.py
git commit -m "docs: make auto init primary across install channels"
```

### Task 8: End-To-End Verification

**Files:**
- No code changes unless verification finds a bug.

**Step 1: Run focused tests**

Run:

```bash
.venv/bin/pytest -q tests/test_agent_bootstrap.py tests/test_install_contract_docs.py tests/test_docker_runtime.py
```

Expected: PASS.

**Step 2: Run CLI init focused tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_cli.py::test_init_guides_missing_runtime_config_interactively \
  tests/test_cli.py::test_init_guides_missing_auth_interactively \
  tests/test_cli.py::test_init_runs_history_preference_profile_and_discovery \
  tests/test_cli.py::test_init_includes_xhs_bootstrap_events \
  tests/test_cli.py::test_init_includes_douyin_bootstrap_events_in_analysis_and_profile \
  tests/test_cli.py::test_init_youtube_env_skip_overrides_yes_flag
```

Expected: PASS.

**Step 3: Run script and type checks**

Run:

```bash
bash -n scripts/install.sh
python3 -m py_compile scripts/agent_bootstrap.py
.venv/bin/ruff check scripts/agent_bootstrap.py tests/test_agent_bootstrap.py tests/test_install_contract_docs.py
.venv/bin/mypy src/
```

Expected: all commands exit 0.

**Step 4: Run broader regression**

Run:

```bash
.venv/bin/pytest -q
```

Expected: full suite passes or only known unrelated skips/warnings.

**Step 5: Commit verification fixes if needed**

If verification required fixes:

```bash
git add <fixed files>
git commit -m "fix: stabilize install auto init flow"
```

### Task 9: Release Prep

**Files:**
- Modify only if release is requested: `pyproject.toml`, `extension/package.json`, `extension/package-lock.json`, `extension/manifest.json`, `docs/changelog.md`

**Step 1: Check status**

Run:

```bash
git status -sb
git log --oneline -5
```

Expected: branch contains the task commits and only intentional files are modified.

**Step 2: If releasing, bump backend version**

Only do this if the user asks for release. This change is backend/docs/scripts only unless extension code changes later.

**Step 3: Final release verification**

Run the full verification commands from Task 8 again.

**Step 4: Commit and tag**

Use Conventional Commits for any version bump and follow existing `backend-v*` release workflow.
