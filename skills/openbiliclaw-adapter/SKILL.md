---
name: openbiliclaw-adapter
description: Use OpenBiliClaw's versioned Agent Bridge CLI to read multi-source recommendations, profile state, dialogue, probes, saved lists, and submit explicit feedback.
user-invocable: true
---

# OpenBiliClaw Agent Bridge Skill

Use this skill when you are inside the OpenBiliClaw workspace and need current state or want to push feedback back into the learning loop. The bridge is host-neutral: OpenClaw, Hermes and WorkBuddy use the same JSON contract.

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

Keep the repository checkout available so the host can discover this workspace skill.

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

For a longer setup guide, read `docs/openclaw-quickstart.md` and `docs/agent-integration.md`.

## Command Bridge

Always call the adapter through the JSON CLI bridge:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli <command> [flags]
```

Supported commands:

- `capabilities` — negotiate `agent-bridge/v2` and the complete capability list before caching tools
- `sync-account`
- `get-profile`
- `recommend --limit 5 [--source-platform <platform>] [--exclude-item-id <id>] [--realtime]`
- `reshuffle` / `append` — replace or append precomputed recommendation pages
- `get-delight` / `respond-delight` — view, like, dislike, dismiss or chat about a surprise
- `activity-feed` / `platform-availability`
- `next-probe` — get the next speculative-interest hypothesis to ask the user about
- `respond-interest-probe --domain "..." --response confirm|reject|defer|chat [--message "..."]`
- `next-avoidance-probe` / `respond-avoidance-probe --domain "..." --response confirm|reject|defer|chat`
- `chat --message "..." [--session openclaw]` — durable Socratic dialogue turn
- `chat-history [--session openclaw]`
- `profile-edit-state` / `edit-profile` — read or update deterministic profile overlays
- `save-local`, `list-saved`, `remove-saved` — local-first saved lists
- `sync-saved --allow-state-changing` — explicitly authorized native-save synchronization
- `runtime-status`
- `submit-feedback --recommendation-id 7 --feedback-type like --request-id feedback-7-like-1 --note "很对胃口"`
- `listen` — long-running WebSocket stream for real-time push events (see below)

The complete source of truth is `openbiliclaw_get_capabilities` / `emit-skill-descriptors`; do not hard-code an older subset.

## Proactive Push (WebSocket)

Instead of polling `get-delight` / `next-probe`, OpenClaw can receive real-time push notifications via WebSocket:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli listen
```

This connects to the runtime stream and outputs one JSON line per event:

```json
{"ok": true, "data": {"status": "connected", "ws_url": "ws://127.0.0.1:8420/api/runtime-stream", "event_types": ["avoidance.chat", "avoidance.confirmed", "avoidance.deferred", "avoidance.probe", "avoidance.rejected", "delight.candidate", "delight.chat", "delight.disliked", "delight.liked", "delight.refreshed", "interest.chat", "interest.confirmed", "interest.deferred", "interest.probe", "interest.rejected"]}}
{"ok": true, "data": {"type": "delight.candidate", "bvid": "BV1xxx", "title": "...", "delight_reason": "...", "delight_score": 0.92, "delight_hook": "深层共鸣"}}
{"ok": true, "data": {"type": "interest.probe", "domain": "建筑美学", "reason": "...", "question": "我从你最近的轨迹里嗅到你可能对【建筑美学】感兴趣——... 这个方向你自己认不认？"}}
{"ok": true, "data": {"type": "avoidance.probe", "domain": "浅层热点复读", "reason": "...", "question": "我猜【浅层热点复读】可能是你想避开的方向——... 这个判断准吗？"}}
```

Default event types include `delight.candidate`, `interest.probe`, `avoidance.probe` and their confirmed/rejected/deferred result events. The command auto-reconnects on disconnection. Press Ctrl-C to stop.

Options:
- `--ws-url <url>` — override the WebSocket endpoint
- `--events <types>` — comma-separated event types to forward; omit it to use the current default manifest

## Socratic Dialogue & Interest Probing

The host can proactively ask the user to clarify or confirm interests and avoidances, then send the answer back into the learning loop.

### Get the next interest hypothesis

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-probe
```

Returns a ready-to-ask `question` plus raw hypothesis data (`domain`, `reason`, `specifics`, `confidence`). If no active hypothesis exists, `probe` is `null`.

### Get or answer the next avoidance hypothesis

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
```

If the user confirms, rejects or defers the hypothesis:

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm \
  --message "对，这类我不想看"
```

Use `--response defer` to snooze it without treating it as a permanent rejection.

### Relay the user's answer via Socratic dialogue

```bash
uv run python -m openbiliclaw.integrations.openclaw.cli chat \
  --message "嗯对，最近在看很多参数化设计的东西"
```

The agent replies in Socratic style (probing deeper, proposing hypotheses) and the dialogue automatically feeds back into the soul engine to refine the user's profile.

## Daily Loop

Use this order for routine work:

1. `capabilities`
2. `get-profile` / `runtime-status`
3. `next-probe` and `next-avoidance-probe`; ask and respond with the matching four-state command
4. `reshuffle --limit <n>` / `append` (fast, precomputed) or `recommend --limit <n>`
5. `submit-feedback` / `respond-delight`
6. `get-delight` or `listen` for proactive surprise recommendations and probes
7. `sync-account` when long-term signals need refreshing
8. Use saved-list commands only when the user asked to save or remove an item

## Working Rules

1. Parse the returned JSON instead of relying on prose.
2. If the JSON payload is `{ "ok": false, ... }`, surface the error and stop.
3. Prefer `reshuffle --limit <n>` (or `append` for pagination) for fast precomputed pages. `recommend --limit <n>` now also serves precomputed pool copy by default; add `--realtime` only when you explicitly want fresh per-item LLM expressions (slow). Neither triggers a runtime refresh unless you pass `--refresh-if-needed`.
4. Use `--refresh-if-needed` only when the user explicitly wants a heavier freshness check before recommendation fetch.
5. For every feedback action, create one stable non-empty `--request-id` (maximum 400 characters) and reuse it for every retry of that same action. Never reuse it for a different recommendation/type/note.
6. For `comment` feedback, always include `--note`.
7. For `like`, `dislike`, `dismiss` delight actions, create and reuse a stable `--request-id`.
8. `save-local` is local-only; never run `sync-saved` without explicit user authorization and `--allow-state-changing`.
9. After an upgrade, rerun `capabilities`; if a host caches descriptors, refresh the cache when `protocol_version` or skill names change.

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
  --request-id feedback-12-comment-1 \
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
