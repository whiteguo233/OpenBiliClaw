---
name: openbiliclaw_adapter
description: Use OpenBiliClaw's adapter CLI to sync account signals, read profile summaries, fetch recommendations, submit feedback, and inspect runtime status.
user-invocable: true
---

# OpenBiliClaw Adapter Skill

Use this skill when you are inside the OpenBiliClaw workspace and need current OpenBiliClaw state or want to push feedback back into the learning loop.

## Deployment Choice

Choose deployment by target machine capability:

1. Docker available: prefer Docker
2. No Docker: use local Python deployment

## Bootstrap

### Docker-first

Run:

```bash
docker compose up -d --build
docker exec -it openbiliclaw-backend openbiliclaw init
```

Keep the repository checkout available so OpenClaw can discover this workspace skill.

### Local fallback

If Docker is unavailable, bootstrap locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
```

Then initialize OpenBiliClaw once:

```bash
openbiliclaw init
```

If `config.toml` is still missing API Key or B 站 Cookie and the terminal is interactive, `openbiliclaw init` will guide the operator through setup. After init, verify the adapter bridge:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli doctor
```

For a longer setup guide, read `docs/openclaw-quickstart.md`.

## Command Bridge

Always call the adapter through the JSON CLI bridge:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli <command> [flags]
```

Supported commands:

- `sync-account`
- `get-profile`
- `get-delight` — check for a proactive surprise recommendation
- `next-probe` — get the next speculative-interest hypothesis to ask the user about
- `next-avoidance-probe` — get the next speculative avoidance hypothesis to ask about
- `respond-avoidance-probe --domain "..." --response confirm|reject|chat [--message "..."]`
- `chat --message "..." [--session openclaw]` — send one Socratic dialogue turn, returns agent reply
- `runtime-status`
- `recommend --limit 5`
- `recommend --limit 5 --refresh-if-needed`
- `submit-feedback --recommendation-id 7 --feedback-type like --note "很对胃口"`
- `listen` — long-running WebSocket stream for real-time push events (see below)

## Proactive Push (WebSocket)

Instead of polling `get-delight` / `next-probe`, OpenClaw can receive real-time push notifications via WebSocket:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli listen
```

This connects to the runtime stream and outputs one JSON line per event:

```json
{"ok": true, "data": {"status": "connected", "ws_url": "ws://127.0.0.1:8420/api/runtime-stream", "event_types": ["avoidance.probe", "delight.candidate", "interest.probe"]}}
{"ok": true, "data": {"type": "delight.candidate", "bvid": "BV1xxx", "title": "...", "delight_reason": "...", "delight_score": 0.92, "delight_hook": "深层共鸣"}}
{"ok": true, "data": {"type": "interest.probe", "domain": "建筑美学", "reason": "...", "question": "我从你最近的轨迹里嗅到你可能对【建筑美学】感兴趣——... 这个方向你自己认不认？"}}
{"ok": true, "data": {"type": "avoidance.probe", "domain": "浅层热点复读", "reason": "...", "question": "我猜【浅层热点复读】可能是你想避开的方向——... 这个判断准吗？"}}
```

Default event types: `delight.candidate` (surprise recommendation), `interest.probe` (interest hypothesis to confirm), and `avoidance.probe` (avoidance hypothesis to confirm). The command auto-reconnects on disconnection. Press Ctrl-C to stop.

Options:
- `--ws-url <url>` — override the WebSocket endpoint
- `--events <types>` — comma-separated event types to forward (default: `avoidance.probe,delight.candidate,interest.probe`)

## Socratic Dialogue & Interest Probing

OpenClaw can proactively ask the user to clarify or confirm their interests, then send the answer back into the learning loop.

### Get the next interest hypothesis

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-probe
```

Returns a ready-to-ask `question` plus raw hypothesis data (`domain`, `reason`, `specifics`, `confidence`). If no active hypothesis exists, `probe` is `null`.

### Get or answer the next avoidance hypothesis

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
```

If the user confirms the hypothesis:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm \
  --message "对，这类我不想看"
```

### Relay the user's answer via Socratic dialogue

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli chat \
  --message "嗯对，最近在看很多参数化设计的东西"
```

The agent replies in Socratic style (probing deeper, proposing hypotheses) and the dialogue automatically feeds back into the soul engine to refine the user's profile.

## Daily Loop

Use this order for routine work:

1. `get-profile`
2. `next-probe` — if a hypothesis is pending, ask the user and relay via `chat`
3. `next-avoidance-probe` — if a hypothesis is pending, ask and relay via `respond-avoidance-probe`
4. `recommend --limit <n>`
5. `submit-feedback`
6. `runtime-status`
7. `get-delight` or `listen` for proactive surprise recommendations and probes
8. `sync-account` when long-term signals need refreshing

## Working Rules

1. Parse the returned JSON instead of relying on prose.
2. If the JSON payload is `{ "ok": false, ... }`, surface the error and stop.
3. Prefer `recommend --limit <n>` for normal recommendation fetches. This is the fast path and does not trigger runtime refresh by default.
4. Use `--refresh-if-needed` only when the user explicitly wants a heavier freshness check before recommendation fetch.
5. For `comment` feedback, always include `--note`.

## Examples

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli get-profile
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli recommend --limit 3 --refresh-if-needed
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli submit-feedback \
  --recommendation-id 12 \
  --feedback-type comment \
  --note "方向对，但我想看更深一点。"
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli get-delight
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-probe
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli chat \
  --message "嗯对，最近在看很多参数化设计的东西"
```

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli listen
```
