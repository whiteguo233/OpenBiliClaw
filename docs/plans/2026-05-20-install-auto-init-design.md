# Install Auto-Init Design

## Goal

Make every supported installation channel guide the user through the same init flow and automatically run init once all required choices and credentials are available.

The rule is "auto-init after explicit confirmation", not "silent init". Installers must ask for LLM, embedding, Bilibili auth, and optional source opt-in decisions before running the first profile build.

## Current Gap

`agent_bootstrap.py` already auto-runs `openbiliclaw init` when credentials and explicit decisions are present. Bash and PowerShell installers call it, but several user-facing paths still describe a manual follow-up step:

- Docker docs tell users to run `docker exec -it openbiliclaw-backend openbiliclaw init`.
- Manual source install docs tell users to run `openbiliclaw init`.
- Extension-cookie sync paths can stop after "backend ready, waiting for cookie" and rely on the user or agent to remember the next command.

This creates inconsistent expectations. Users expect "install" to leave the app usable, meaning the soul profile and first discovery pool exist.

## Chosen Approach

Use `agent_bootstrap.py` as the single auto-init contract for all installer-like channels.

Supported channels should converge on one state machine:

1. Prepare checkout and dependencies.
2. Start the backend.
3. Confirm required runtime choices with the user.
4. Wait for or collect Bilibili auth.
5. Run init automatically.
6. Report per-source init counts and health status.

Direct `openbiliclaw init` remains available as an advanced fallback and for reruns.

## User Confirmations

Every channel must surface these decisions before init:

| Decision | Default | Auto-init mapping |
| --- | --- | --- |
| LLM provider | DeepSeek | `--provider ...` plus model/key fields |
| Embedding provider | Ollama `bge-m3` | `--embedding-provider ollama --embedding-model bge-m3` |
| Bilibili auth | Browser extension sync | wait for synced cookie or collect `--bilibili-cookie` |
| Xiaohongshu init data | No | `--yes-xhs` only after explicit opt-in, otherwise `--no-xhs` |
| Douyin init data | No | `--yes-douyin` only after explicit opt-in, otherwise `--no-douyin` |
| YouTube init data | No | `--yes-youtube` only after explicit opt-in, otherwise `--no-youtube` |

Omitting any source flag is treated as "the installer failed to ask"; bootstrap should pause with `needs_decisions`.

## Channel Behavior

### Bash Installer

`install.sh` keeps invoking `agent_bootstrap.py`. Its summary should not present manual init as the main next step. If credentials or decisions are missing, it prints the next bootstrap command. If only the Bilibili cookie is missing and the user chooses extension sync, the flow should wait for cookie arrival or instruct the agent to poll and continue with bootstrap.

### PowerShell Installer

`install.ps1` mirrors `install.sh`. Windows users should receive the same decisions, status names, and rerun command shape.

### AI Agent Install

`docs/agent-install.md` remains the canonical contract. It should state that the agent must continue until `init_complete` or a concrete blocker occurs. `needs_decisions` and cookie-sync wait states are not final install success states.

### Docker

Docker should no longer be documented as "compose up, then manually run init" for the main path. Instead:

- `agent_bootstrap.py --mode docker` starts the compose stack.
- Once healthy and decisions are explicit, bootstrap runs container init using `docker exec -i openbiliclaw-backend openbiliclaw init ...`.
- Manual `docker exec -it ... init` stays documented as an advanced fallback.

### Manual Source Install

The preferred manual path becomes:

```bash
python3 scripts/agent_bootstrap.py --mode local --project-dir .
```

The docs may still show `pip install -e ".[dev]"` for contributors, but first-run user onboarding should point to bootstrap so init is not forgotten.

### Browser Extension

The extension does not own backend init. It supports the install flow by syncing Bilibili cookies and returning XHS/Douyin/YouTube bootstrap tasks. Docs should not imply that installing the extension alone initializes the backend.

## Error Handling

- Missing LLM key: stop with `needs_secrets`, print the exact bootstrap command to continue.
- Missing Bilibili cookie: prefer extension sync; once synced, continue bootstrap/init.
- Missing source decisions: stop with `needs_decisions`, ask the user, then continue.
- Init non-zero exit: keep backend running, emit `init_failed`, and print retry command.
- Optional source returns 0 items: init continues and reports the likely cause in the summary.

## Tests

Add or update focused tests for:

- `agent_bootstrap.py` Docker/local init command construction with all three source flags.
- `agent_bootstrap.py` pauses on missing source decisions and resumes when flags are present.
- Bash and PowerShell installer status text no longer treats manual init as the primary next step.
- Docs contract requires every install channel to reach `init_complete` or an explicit blocker.
- README / Docker / OpenClaw docs show bootstrap auto-init as the main path and manual init as fallback.

## Documentation Impact

Update:

- `README.md`
- `README_EN.md`
- `docs/agent-install.md`
- `docs/agent-deployment.md`
- `docs/docker-deployment.md`
- `docs/openclaw-quickstart.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/changelog.md`

No architecture dataflow change is expected; this is an installer/onboarding contract change.
