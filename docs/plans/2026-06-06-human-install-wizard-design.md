# Human One-Line Install Wizard Design

## Context

The current one-line installer already detects an interactive terminal and passes
`--interactive-confirm --wait-for-extension-cookie` into `agent_bootstrap.py`.
That interactive path asks for embedding, optional source imports, Bilibili
signal limits, and Bilibili cookie mode, but it does not ask for the LLM
provider or required provider credentials before installing and starting the
backend.

For a fresh human user running:

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

the installer can therefore reach a partial state and print a second bootstrap
command instead of completing the setup in one terminal session. The AI-agent
install flow already has the desired decision order in `docs/agent-install.md`.
The human command-line installer should present the same choices directly in the
terminal.

## Goals

- Make the human one-line install path ask all required choices before install,
  backend start, service checks, and init.
- Align the human terminal questions with the AI one-line install flow:
  LLM provider first, embedding second, source opt-ins, then Bilibili auth.
- Keep non-interactive and AI-agent paths unchanged: no prompts under CI,
  `OPENBILICLAW_NONINTERACTIVE=1`, or non-TTY execution.
- Keep `agent_bootstrap.py` as the single bootstrap state machine.
- Avoid printing API keys or cookies in logs, summaries, or status events.

## Non-Goals

- Do not duplicate the full wizard in shell or PowerShell.
- Do not require dependencies to be installed before collecting choices.
- Do not change the browser extension cookie sync protocol.
- Do not enable Xiaohongshu, Douyin, or YouTube imports by default.
- Do not remove the AI-agent `BOOTSTRAP_STATUS` contract.

## Proposed Flow

When `install.sh` or `install.ps1` detects a human interactive terminal, it
continues to invoke `agent_bootstrap.py --interactive-confirm`. The Python
bootstrap expands that mode into a full human install wizard before dependency
installation.

The wizard asks, in order:

1. LLM provider
   - DeepSeek default recommendation.
   - OpenAI-compatible relay / gateway as the second recommendation.
   - OpenAI, Gemini, Claude, OpenRouter, and Ollama as explicit options.
2. Provider-specific fields
   - Remote providers ask for API key.
   - OpenAI-compatible relay asks for base URL, API key, and model.
   - Official providers offer current default model names and allow override.
   - Ollama asks for chat model only and uses no API key.
3. Embedding provider
   - Default: local Ollama `bge-m3`.
   - Alternatives: Gemini, OpenAI, custom OpenAI-compatible endpoint, disabled.
4. Bilibili init signal limits
   - Favorites default 300.
   - Followed creators default 100.
5. Optional source imports
   - Xiaohongshu default no.
   - Douyin default no.
   - YouTube default no.
6. Bilibili cookie source
   - Default: browser extension sync.
   - Alternatives: manual paste, existing reused cookie.

After collecting answers, bootstrap writes config/cookie values, runs dependency
installation, starts the backend, checks `/api/health`, verifies the configured
LLM and embedding services, and then runs `openbiliclaw init` automatically when
checks pass.

## Reuse Behavior

`REUSE_FROM` and auto-detected old installs remain supported. In human mode,
the wizard should surface reused fields before asking questions:

- Reused LLM provider and provider connection fields.
- Reused Bilibili cookie inline or cookie file.
- Reused values are defaults, not locks.

The user can accept reused values, replace them, or choose a different provider.
Secrets must be represented as present/absent only; never echo actual values.

## Error Handling

- Missing terminal has two paths:
  - Non-interactive shell / CI runs should not pass `--interactive-confirm`, so
    bootstrap preserves the current status-driven behavior and never prompts.
  - A caller that explicitly passes `--interactive-confirm` without a TTY gets a
    fail-fast `BOOTSTRAP_STATUS` error with `step=interactive_confirm`.
- Invalid provider choice: re-prompt with the same menu.
- Empty required API key/base URL/model: re-prompt unless the selected provider
  explicitly allows the field to be blank.
- Secret prompts must never fall back to echoed input. If Python cannot disable
  echo for an API key or Cookie prompt, abort the wizard with a clear error and
  leave the existing status-driven recovery path intact.
- Cookie mode controls extension waiting. `extension` enables
  `wait_for_extension_cookie`; `manual` and `existing` must turn it off even
  though shell installers pass the flag before the wizard runs.
- Service check failure: do not run init; print which service failed and keep the
  backend health URL visible.
- Extension cookie timeout: return the existing `needs_secrets` status and show
  manual fallback instructions.

## Resolved Decisions

- Human option 2 ("中转站 / OpenAI 协议兼容服务") writes to
  `[llm.openai_compatible]`, not `[llm.openai]`. The codebase already treats
  `openai_compatible` as a first-class provider with an explicit `base_url`
  invariant, so the human wizard should use that clearer config path. The legacy
  non-interactive `--llm-preset` behavior remains unchanged in this phase to
  avoid breaking existing AI-agent prompts.
- Python `getpass` is acceptable only when it can disable terminal echo. The
  implementation must convert `getpass.GetPassWarning` into a `RuntimeError`
  before the exception leaves the wizard, so `run()` can emit
  `BOOTSTRAP_STATUS` with `step=interactive_confirm` and exit code 2.
- Wizard answer objects must reject unknown providers before config is written.
  In particular, aliases such as `openai-compat` are input syntax only; the
  stored provider value is always `openai_compatible`.
- Reused API keys are scoped to the chosen provider. Pressing Enter to reuse a
  key is offered only when the selected provider matches the provider that owns
  the existing key.

## Implementation Sketch

- Add a richer answer dataclass in `scripts/agent_bootstrap.py`, separate from
  the current init-only `InitConfirmationAnswers`.
- Add `collect_human_install_wizard(input_func=input,
  secret_input_func=read_secret_no_echo)` for full human install choices.
- Guard `collect_human_install_wizard()` with the existing interactive-terminal
  check. A headless `--interactive-confirm` invocation must emit an error status
  instead of blocking on `input()`.
- Keep `collect_interactive_confirmations()` or adapt it as the init-decision
  subset used by tests.
- Apply human answers to parsed args before `config_summary`, so the rest of
  `run()` continues to use the existing config write, install, service check, and
  init pipeline.
- Keep shell and PowerShell changes minimal: they only detect interactivity and
  pass the interactive flag. Bootstrap then reconciles `wait_for_extension_cookie`
  from the user's final cookie mode.

## Testing Plan

- Unit tests with fake input for:
  - Default DeepSeek provider selection.
  - OpenAI-compatible relay requiring base URL, API key, and model.
  - Ollama chat model without API key.
  - Embedding default Ollama `bge-m3`.
  - Source opt-ins defaulting to no.
  - Manual cookie and extension cookie modes.
- Regression tests for `manual` / `existing` cookie modes turning off extension
  waiting.
- Regression tests for non-TTY `--interactive-confirm` failing fast.
- Secret-prompt tests that assert API keys and Cookies are collected through the
  secret input path and are never present in captured output.
- Regression tests that non-interactive bootstrap still emits status events and
  does not prompt.
- Contract tests for `install.sh` and `install.ps1` interactive flags.
- A real one-line install smoke with a temporary checkout and explicit port.
