# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenBiliClaw is an AI Agent for personalized Bilibili content recommendation. It builds a deep psychological profile ("Soul") of users through behavioral analysis, then proactively discovers and recommends content with warm, friend-like explanations. The project is bilingual (Chinese primary, English supported) and in pre-alpha (v0.1-dev).

## Build & Development Commands

### Python Backend

```bash
pip install -e ".[dev]"          # Install with dev dependencies
pytest                           # Run all tests
pytest tests/test_foo.py         # Run single test file
pytest tests/test_foo.py::test_bar  # Run single test
pytest --cov=openbiliclaw        # Tests with coverage
ruff format src/ tests/          # Format code
ruff check src/ tests/           # Lint
mypy src/                        # Type check (strict mode)
```

### Browser Extension (extension/)

```bash
cd extension
npm run build                    # Full build (clean + types + bundle)
npm run typecheck                # Type check only
npm run test                     # Run tests (node --test)
```

### CLI

```bash
openbiliclaw start               # Start daemon
openbiliclaw init                # First-time setup (fetch history + generate profile)
openbiliclaw recommend           # Show recommendations
openbiliclaw profile             # View user portrait
openbiliclaw config-show         # Show current config
openbiliclaw serve-api           # Start FastAPI server (used by Docker)
```

### Docker

```bash
docker compose up -d --build     # Start backend (port 8420)
# Health check: http://127.0.0.1:8420/api/health
```

## Architecture

The system follows a pipeline: **Behavioral Data -> Soul Engine -> Discovery -> Recommendation**.

### Core Pipeline

1. **Soul Engine** (`soul/`) - Transforms raw behavioral events into deep user understanding through 5 layers: Event -> Preference -> Awareness -> Insight -> Soul. Each layer feeds bidirectionally into the next. The `SoulEngine` orchestrates analyzers (`preference_analyzer.py`, `insight_analyzer.py`, `awareness_analyzer.py`) and outputs a `SoulProfile`.

2. **Memory Manager** (`memory/manager.py`) - Coordinates 4 memory types (Core, Episodic, Semantic, Working) across a networked architecture with cross-layer updates and self-editing capabilities.

3. **Discovery Engine** (`discovery/engine.py`) - Finds content via 4 strategies defined in `discovery/strategies/strategies.py`:
   - `SearchStrategy` - generates keywords from soul profile, searches Bilibili
   - `TrendingStrategy` - scans trending channels
   - `ExploreStrategy` - cross-domain exploration outside user's comfort zone
   - `RelatedChainStrategy` - follows related video chains deeply

4. **Recommendation Engine** (`recommendation/engine.py`) - Ranks discovered content against soul profile and generates natural-language explanations for each recommendation.

### Supporting Layers

- **LLM Adapter** (`llm/`) - Multi-provider abstraction. All providers implement `LLMProvider` protocol from `base.py`. `registry.py` handles provider instantiation by name. Supported: OpenAI, Claude, Gemini, DeepSeek, Ollama (local), OpenRouter.

- **Bilibili Integration** (`bilibili/api.py`) - `BilibiliAPIClient` wraps the bilibili-api-python library. Authentication via cookie or QR code (`auth.py`). Browser automation via agent-browser (`browser.py`).

- **FastAPI Backend** (`api/app.py`) - REST API on port 8420 serving the browser extension. Factory function `create_app()` initializes all components. Receives behavior events, serves recommendations, and pushes real-time cognition updates.

- **Storage** (`storage/database.py`) - SQLite with vector index for semantic search. Single database at `data/openbiliclaw.db`.

- **CLI** (`cli.py`) - Typer-based. Entry point: `openbiliclaw.cli:app`.

### Extension <-> Backend Flow

The Chrome extension (`extension/`) captures user behavior on bilibili.com pages via content script (`content/collector.ts`), buffers events in the service worker (`background/service-worker.ts`), and sends them to the FastAPI backend at `http://127.0.0.1:8420`. The popup/side panel (`popup/`) displays recommendations fetched from the same backend.

## Configuration

- Template: `config.example.toml` -> copy to `config.toml` for local use
- `config.toml` is gitignored; never commit it
- Key sections: `[llm]` (provider + API keys), `[bilibili]` (auth), `[scheduler]` (discovery cron), `[storage]` (db path)
- Config logic: `src/openbiliclaw/config.py` with Pydantic validation and env var overrides

## LLM Prompt-Cache Convention (v0.3.28+)

OpenBiliClaw runs many LLM calls per discovery cycle. Provider-side
prompt caching (DeepSeek 90% off / OpenAI 50% / Claude 90% / Gemini 75%
on cached tokens) only fires when the **system message prefix is
byte-identical across calls**. So:

**Rule for every prompt builder in `src/openbiliclaw/llm/prompts.py`**:

1. `system_prompt` MUST be 100% static — define it as a module-level
   constant `_<NAME>_SYSTEM_PROMPT` and return it as-is. **No f-strings,
   no concatenation with per-call data, no platform/source/profile
   substitution.**
2. ALL per-call variables (profile, content, source_platform, tone, …)
   live in `user_prompt`, ordered from most stable (persona) to most
   variable (this batch's items).
3. JSON serialization MUST be deterministic: always pass
   `ensure_ascii=False, indent=2, sort_keys=True` to `json.dumps`. A
   dict-key reordering is enough to break the cache prefix.
4. Reference the system prompt's "see user message for X / Y / Z"
   contract explicitly so the LLM knows where to find each variable.

**Exception**: `build_socratic_dialogue_prompt` keeps tone / friend
label / core memory in system. That's intentional for OpenBiliClaw's
single-user model — per-user state is stable across that user's calls,
so cache still fires. Multi-user deployments would refactor it.

**Enforcement**: `tests/test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant`
calls every covered builder with two distinct inputs and asserts the
system message is byte-identical. Add new builders to its
`_builder_test_inputs()` list.

**Observability**: `openbiliclaw cost --by caller` shows per-caller
cache hit rate (color-coded — red if <30%, almost certainly a builder
that broke the convention).

## Code Conventions

- Python 3.11+, 4-space indent, 100-char line length
- Type annotations required on all functions (MyPy strict)
- Ruff for formatting and linting (rules: E, W, F, I, N, UP, B, SIM, TCH)
- Test files: `test_<module>.py`, test functions: `test_<behavior>`
- Integration tests requiring real Bilibili credentials: mark with `@pytest.mark.integration`
- Async tests use `asyncio_mode = "auto"` (no manual `@pytest.mark.asyncio` needed)
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`

## Documentation Requirements

**Every commit/merge to main, and every release, must keep docs and architecture diagrams in sync with the code.** Not optional. Branches without doc updates should not merge. Scope is not limited to "todolist tasks" — any change that touches interfaces, module boundaries, data flow, config, CLI, dependencies, or external integrations triggers this rule.

Mandatory updates (apply whichever match the PR's scope):

1. `docs/modules/<module>.md` — update "implemented features" table and "public API" section for any module whose code changed
2. `docs/changelog.md` — every release adds a top entry (`## vX.Y.Z: theme (YYYY-MM-DD)`); every PR also adds a short bullet under the current version block
3. **Architecture diagrams** — when a PR changes cross-module wiring, adds modules / adapters, alters data flow, or introduces a new dependency block (e.g. embedding service, xhs path):
   - `docs/architecture.md` (text layers + module roles)
   - `docs/spec.md` §3 system architecture ASCII diagram
   - `README.md` and `README_EN.md` top-of-page architecture diagrams
   The architecture diagram is not decorative — it MUST reflect what's on main.
4. `docs/modules/cli.md` — when CLI commands are added / removed / renamed
5. `docs/modules/config.md` — when `config.toml` fields are added / renamed / removed

Update on demand based on PR type:

6. `docs/index.md` — new module docs, module-status changes, highlighted docs
7. `README.md` / `README_EN.md` — positioning changes, tagline changes, core feature list changes, install flow changes, version releases
8. GitHub About (`gh repo edit --description`) — when project positioning shifts
9. `scripts/install.sh` post-install summary, `docs/agent-install.md`, `docs/docker-deployment.md` — installer flow / dependencies / opt-in steps changing
10. `README.md` / `README_EN.md` 📌 vX.Y.Z highlights callout — keep it a **teaser, not a mini-changelog**. Hard rules:
    - **At most 4 bullets**, each one tight sentence (~60 字 / ~40 words max).
    - Surface only the release's biggest **user-facing** wins: new platform, behaviour change, perf jump, breaking config. Skip internal smokes, test coverage, refactor, default-value tweaks, observability-only changes — those live only in `docs/changelog.md`.
    - When releasing, **replace** the previous version's callout entirely; never stack two version headers, and never append the new version's bullets onto the old list.
    - Both `README.md` (中文) and `README_EN.md` (英文) callouts must stay in sync — same bullet count, same items, same order.
    - The bullet ends with a one-liner "完整变更详见 [docs/changelog.md](docs/changelog.md)。" (CN) / "Full changelog: [docs/changelog.md](docs/changelog.md)." (EN). The full detail is *always* in changelog, never in README.

Pre-merge checklist:

- [ ] `docs/modules/<modules touched>.md` updated
- [ ] `docs/changelog.md` has a new entry
- [ ] Architecture changed → `docs/architecture.md` + `docs/spec.md` diagram + README diagrams synced
- [ ] CLI / config changed → corresponding module doc synced
- [ ] Installer flow changed → `install.sh` output + agent-install.md + docker-deployment.md synced
- [ ] Positioning / tagline changed → README CN/EN + GitHub About synced
- [ ] New release → README CN/EN 📌 highlights callout **replaced** (not appended), ≤4 bullets, ≤1 sentence each, CN/EN in sync, no internal smokes/test coverage entries

## Development Order

Follow `docs/v0.1-todolist.md` roadmap: Connect -> Understand -> Discover -> Recommend -> Learn -> Extension -> Stable Delivery. Do not skip lower layers to build upper-layer features.
