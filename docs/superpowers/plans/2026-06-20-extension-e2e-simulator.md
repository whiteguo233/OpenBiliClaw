# Extension E2E Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only real end-to-end simulator that asks the installed extension to open or reuse Douyin, Xiaohongshu, and X/Twitter pages, perform whitelisted DOM actions through content scripts, then have the backend verify that naturally captured behavior events reached the events table.

**Architecture:** Backend publishes a signed `extension_e2e_run` runtime event over the existing `/api/runtime-stream` WebSocket. The extension background service worker receives it, drives tabs and content scripts, and reports action execution results back to the backend. Content scripts execute platform-specific DOM recipes but never send synthetic behavior events directly; the existing capture collectors must observe the resulting DOM events and send normal `BEHAVIOR_EVENT` messages.

**Tech Stack:** FastAPI + Pydantic backend, existing `RuntimeEventHub`, SQLite-backed `Database` event queries, Chrome Manifest V3 service worker, TypeScript content scripts, Node built-in tests already used under `extension/tests`, pytest for backend route/helper tests.

---

## Scope And Invariants

- [ ] Keep all new control surfaces local-only. Reject non-local callers using the existing `AuthGate.is_trusted_local(request)` check.
- [ ] Do not require Chrome DevTools Protocol, Playwright, Puppeteer, AppleScript, or direct browser debugging for the production simulator path.
- [ ] Preserve current capture pipeline semantics. Content scripts may click, scroll, and type, but they must not directly emit `BEHAVIOR_EVENT` for e2e success.
- [ ] Default to safe actions: `snapshot`, `scroll`, `click`, and `share`. State-changing actions require `allow_state_changing=true`.
- [ ] Treat Douyin, Xiaohongshu, and X/Twitter as one unified simulator framework with platform-specific URL and selector recipes.
- [ ] Return a report that distinguishes action execution from capture verification.

## Expected Operator Flow

```bash
# backend already running on the configured local API port
curl -sS -X POST http://127.0.0.1:8420/api/extension/e2e/run \
  -H 'Content-Type: application/json' \
  -d '{
    "platforms": ["douyin", "xiaohongshu", "twitter"],
    "actions": {
      "douyin": ["snapshot", "scroll", "click", "share"],
      "xiaohongshu": ["snapshot", "scroll", "click", "share"],
      "twitter": ["snapshot", "scroll", "click", "share"]
    },
    "allow_state_changing": false,
    "timeout_seconds": 45
  }' | jq .
```

Expected success shape:

```json
{
  "run_id": "e2e-1f2b3c",
  "status": "ok",
  "started_at": "2026-06-20T10:10:00Z",
  "finished_at": "2026-06-20T10:10:23Z",
  "platforms": [
    {
      "platform": "douyin",
      "status": "ok",
      "actions": [
        {
          "action": "click",
          "executed": true,
          "captured": true,
          "event_id": 88210,
          "observed_event_type": "click"
        }
      ]
    }
  ]
}
```

## Backend Implementation

### Task 1: Add E2E API Models

- [ ] Modify `src/openbiliclaw/api/models.py`.
- [ ] Add the import if it is missing:

```python
from typing import Literal
```

- [ ] Add these models near the other extension/runtime API models:

```python
ExtensionE2EPlatform = Literal["douyin", "xiaohongshu", "twitter"]
ExtensionE2EAction = Literal[
    "snapshot",
    "scroll",
    "click",
    "like",
    "favorite",
    "share",
    "follow",
    "repost",
    "bookmark",
]
ExtensionE2EActionStatus = Literal["ok", "skipped", "failed"]
ExtensionE2ERunStatus = Literal["ok", "partial", "failed", "timeout"]


class ExtensionE2ERunIn(BaseModel):
    """Request body for local extension-driven capture e2e runs."""

    platforms: list[ExtensionE2EPlatform] = Field(
        default_factory=lambda: ["douyin", "xiaohongshu", "twitter"]
    )
    actions: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]] = Field(default_factory=dict)
    allow_state_changing: bool = False
    timeout_seconds: int = Field(default=45, ge=5, le=180)


class ExtensionE2EActionResultIn(BaseModel):
    """Action execution result posted by the extension background runner."""

    action: ExtensionE2EAction
    status: ExtensionE2EActionStatus
    executed: bool = False
    selector: str | None = None
    error: str | None = None


class ExtensionE2EPlatformResultIn(BaseModel):
    """Platform execution result posted by the extension background runner."""

    platform: ExtensionE2EPlatform
    status: ExtensionE2EActionStatus
    url: str | None = None
    actions: list[ExtensionE2EActionResultIn] = Field(default_factory=list)
    error: str | None = None


class ExtensionE2EResultIn(BaseModel):
    """Signed extension callback for one e2e run."""

    run_id: str
    token: str
    platforms: list[ExtensionE2EPlatformResultIn] = Field(default_factory=list)


class ExtensionE2EEventMatchOut(BaseModel):
    """Captured backend event matched to an executed e2e action."""

    event_id: int | None = None
    observed_event_type: str | None = None
    captured: bool = False


class ExtensionE2EActionReportOut(BaseModel):
    """Final per-action report combining extension execution and backend capture."""

    action: ExtensionE2EAction
    executed: bool
    captured: bool
    status: ExtensionE2EActionStatus
    event_id: int | None = None
    observed_event_type: str | None = None
    selector: str | None = None
    error: str | None = None


class ExtensionE2EPlatformReportOut(BaseModel):
    """Final per-platform e2e report."""

    platform: ExtensionE2EPlatform
    status: ExtensionE2ERunStatus
    url: str | None = None
    actions: list[ExtensionE2EActionReportOut] = Field(default_factory=list)
    error: str | None = None


class ExtensionE2ERunOut(BaseModel):
    """Final report for one local extension e2e run."""

    run_id: str
    status: ExtensionE2ERunStatus
    started_at: str
    finished_at: str | None = None
    platforms: list[ExtensionE2EPlatformReportOut] = Field(default_factory=list)
    error: str | None = None
```

- [ ] Add backend tests in `tests/test_api_app.py`:

```python
def test_extension_e2e_run_rejects_state_changing_actions_without_flag(self, client: TestClient) -> None:
    response = client.post(
        "/api/extension/e2e/run",
        json={
            "platforms": ["twitter"],
            "actions": {"twitter": ["like"]},
            "allow_state_changing": False,
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 400
    assert "allow_state_changing" in response.json()["detail"]
```

```python
def test_extension_e2e_run_rejects_unknown_platform(self, client: TestClient) -> None:
    response = client.post(
        "/api/extension/e2e/run",
        json={"platforms": ["unknown"], "timeout_seconds": 5},
    )

    assert response.status_code == 422
```

Verification:

```bash
.venv/bin/pytest tests/test_api_app.py -q -k "extension_e2e"
```

### Task 2: Add Backend Run Registry And Runtime Event Publishing

- [ ] Modify `src/openbiliclaw/api/app.py`.
- [ ] Import the new models and standard library helpers:

```python
import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
```

```python
from openbiliclaw.api.models import (
    ExtensionE2EActionReportOut,
    ExtensionE2EEventMatchOut,
    ExtensionE2EPlatformReportOut,
    ExtensionE2EResultIn,
    ExtensionE2ERunIn,
    ExtensionE2ERunOut,
)
```

- [ ] Add backend constants and run-state types near the runtime helper functions:

```python
_E2E_STATE_CHANGING_ACTIONS: set[str] = {"like", "favorite", "follow", "repost", "bookmark"}
_E2E_DEFAULT_ACTIONS: dict[str, list[str]] = {
    "douyin": ["snapshot", "scroll", "click", "share"],
    "xiaohongshu": ["snapshot", "scroll", "click", "share"],
    "twitter": ["snapshot", "scroll", "click", "share"],
}
_E2E_ACTION_TO_EVENT_TYPES: dict[str, set[str]] = {
    "snapshot": {"snapshot"},
    "scroll": {"scroll"},
    "click": {"click"},
    "like": {"like", "favorite", "click"},
    "favorite": {"favorite", "bookmark", "click"},
    "share": {"share", "click"},
    "follow": {"follow", "click"},
    "repost": {"share", "repost", "click"},
    "bookmark": {"bookmark", "favorite", "click"},
}


@dataclass
class _ExtensionE2ERunState:
    run_id: str
    token: str
    started_at: datetime
    after_event_id: int
    expected_actions: dict[str, list[str]]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    extension_result: ExtensionE2EResultIn | None = None
    error: str | None = None
```

- [ ] Initialize the run registry when the FastAPI app is created:

```python
app.state.extension_e2e_runs = {}
```

- [ ] Add helper functions in `app.py`:

```python
def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _extension_e2e_actions_for_request(body: ExtensionE2ERunIn) -> dict[str, list[str]]:
    actions: dict[str, list[str]] = {}
    for platform in body.platforms:
        requested = body.actions.get(platform) or _E2E_DEFAULT_ACTIONS[platform]
        blocked = sorted(set(requested) & _E2E_STATE_CHANGING_ACTIONS)
        if blocked and not body.allow_state_changing:
            blocked_text = ", ".join(blocked)
            raise HTTPException(
                status_code=400,
                detail=f"allow_state_changing=true is required for actions: {blocked_text}",
            )
        actions[platform] = list(dict.fromkeys(requested))
    return actions


def _latest_e2e_event_id(database: Any) -> int:
    getter = getattr(database, "get_latest_event_id", None)
    if callable(getter):
        return int(getter())
    return 0


def _query_e2e_events(database: Any, *, after_event_id: int) -> list[dict[str, Any]]:
    query = getattr(database, "query_events_since", None)
    if callable(query):
        return query(
            after_event_id=after_event_id,
            event_types=sorted({event for events in _E2E_ACTION_TO_EVENT_TYPES.values() for event in events}),
        )
    return []


def _match_e2e_event(
    events: list[dict[str, Any]],
    *,
    platform: str,
    action: str,
    used_event_ids: set[int],
) -> ExtensionE2EEventMatchOut:
    expected_types = _E2E_ACTION_TO_EVENT_TYPES[action]
    for row in events:
        event_id = int(row.get("id") or 0)
        if event_id in used_event_ids:
            continue
        if row.get("source") != platform:
            continue
        event_type = str(row.get("event_type") or "")
        if event_type not in expected_types:
            continue
        used_event_ids.add(event_id)
        return ExtensionE2EEventMatchOut(
            event_id=event_id,
            observed_event_type=event_type,
            captured=True,
        )
    return ExtensionE2EEventMatchOut(captured=False)
```

- [ ] Add route implementation below `/api/extension/reload`:

```python
@app.post("/api/extension/e2e/run", response_model=ExtensionE2ERunOut)
async def run_extension_e2e(body: ExtensionE2ERunIn, request: Request) -> ExtensionE2ERunOut:
    gate = _get_auth_gate()
    if not gate.is_trusted_local(request):
        raise HTTPException(status_code=403, detail="Extension e2e is only available to trusted local callers")

    ctx = _get_ctx()
    expected_actions = _extension_e2e_actions_for_request(body)
    run_id = f"e2e-{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(24)
    started_at = datetime.now(UTC)
    state = _ExtensionE2ERunState(
        run_id=run_id,
        token=token,
        started_at=started_at,
        after_event_id=_latest_e2e_event_id(ctx.database),
        expected_actions=expected_actions,
    )
    app.state.extension_e2e_runs[run_id] = state

    await ctx.event_hub.publish(
        {
            "type": "extension_e2e_run",
            "source": "backend",
            "run_id": run_id,
            "token": token,
            "platforms": list(expected_actions.keys()),
            "actions": expected_actions,
            "allow_state_changing": body.allow_state_changing,
            "timeout_seconds": body.timeout_seconds,
        }
    )

    try:
        await asyncio.wait_for(state.event.wait(), timeout=body.timeout_seconds)
    except asyncio.TimeoutError:
        state.error = "Timed out waiting for extension e2e result"

    events = _query_e2e_events(ctx.database, after_event_id=state.after_event_id)
    report = _build_extension_e2e_report(state, events)
    app.state.extension_e2e_runs.pop(run_id, None)
    return report
```

- [ ] Add result callback route:

```python
@app.post("/api/extension/e2e/result")
async def post_extension_e2e_result(body: ExtensionE2EResultIn, request: Request) -> dict[str, str]:
    gate = _get_auth_gate()
    if not gate.is_trusted_local(request):
        raise HTTPException(status_code=403, detail="Extension e2e result is only available to trusted local callers")

    state = app.state.extension_e2e_runs.get(body.run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown extension e2e run")
    if not secrets.compare_digest(state.token, body.token):
        raise HTTPException(status_code=403, detail="Invalid extension e2e token")

    state.extension_result = body
    state.event.set()
    return {"status": "accepted"}
```

- [ ] Add report builder:

```python
def _build_extension_e2e_report(
    state: _ExtensionE2ERunState,
    events: list[dict[str, Any]],
) -> ExtensionE2ERunOut:
    used_event_ids: set[int] = set()
    result_by_platform = {
        result.platform: result
        for result in (state.extension_result.platforms if state.extension_result is not None else [])
    }
    platform_reports: list[ExtensionE2EPlatformReportOut] = []

    for platform, actions in state.expected_actions.items():
        extension_result = result_by_platform.get(platform)
        execution_by_action = {
            item.action: item
            for item in (extension_result.actions if extension_result is not None else [])
        }
        action_reports: list[ExtensionE2EActionReportOut] = []
        for action in actions:
            execution = execution_by_action.get(action)
            match = _match_e2e_event(
                events,
                platform=platform,
                action=action,
                used_event_ids=used_event_ids,
            )
            action_reports.append(
                ExtensionE2EActionReportOut(
                    action=action,
                    executed=bool(execution and execution.executed),
                    captured=match.captured,
                    status=(execution.status if execution is not None else "failed"),
                    event_id=match.event_id,
                    observed_event_type=match.observed_event_type,
                    selector=(execution.selector if execution is not None else None),
                    error=(execution.error if execution is not None else "extension_result_missing"),
                )
            )

        if all(item.executed and item.captured for item in action_reports):
            platform_status = "ok"
        elif any(item.executed or item.captured for item in action_reports):
            platform_status = "partial"
        else:
            platform_status = "failed"

        platform_reports.append(
            ExtensionE2EPlatformReportOut(
                platform=platform,
                status=platform_status,
                url=(extension_result.url if extension_result is not None else None),
                actions=action_reports,
                error=(extension_result.error if extension_result is not None else None),
            )
        )

    if state.error is not None:
        run_status = "timeout"
    elif all(platform.status == "ok" for platform in platform_reports):
        run_status = "ok"
    elif any(platform.status in {"ok", "partial"} for platform in platform_reports):
        run_status = "partial"
    else:
        run_status = "failed"

    return ExtensionE2ERunOut(
        run_id=state.run_id,
        status=run_status,
        started_at=_utc_iso(state.started_at),
        finished_at=_utc_iso(datetime.now(UTC)),
        platforms=platform_reports,
        error=state.error,
    )
```

- [ ] Add backend tests:

```python
def test_extension_e2e_result_rejects_bad_token(self, client: TestClient) -> None:
    from openbiliclaw.api.app import _ExtensionE2ERunState

    app = client.app
    state = _ExtensionE2ERunState(
        run_id="e2e-test",
        token="expected",
        started_at=datetime.now(UTC),
        after_event_id=0,
        expected_actions={"twitter": ["click"]},
    )
    app.state.extension_e2e_runs["e2e-test"] = state

    response = client.post(
        "/api/extension/e2e/result",
        json={"run_id": "e2e-test", "token": "wrong", "platforms": []},
    )

    assert response.status_code == 403
```

```python
def test_match_e2e_event_matches_platform_and_action_once() -> None:
    from openbiliclaw.api.app import _match_e2e_event

    used: set[int] = set()
    events = [
        {"id": 1, "source": "douyin", "event_type": "click"},
        {"id": 2, "source": "twitter", "event_type": "share"},
    ]

    first = _match_e2e_event(events, platform="twitter", action="share", used_event_ids=used)
    second = _match_e2e_event(events, platform="twitter", action="share", used_event_ids=used)

    assert first.captured is True
    assert first.event_id == 2
    assert second.captured is False
```

Verification:

```bash
.venv/bin/pytest tests/test_api_app.py -q -k "extension_e2e or match_e2e"
ruff check src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_app.py
mypy src/
```

## Extension Implementation

### Task 3: Add Shared E2E Types And Guards

- [ ] Create `extension/src/shared/e2e.ts`.

```typescript
export type E2EPlatform = 'douyin' | 'xiaohongshu' | 'twitter';

export type E2EAction =
  | 'snapshot'
  | 'scroll'
  | 'click'
  | 'like'
  | 'favorite'
  | 'share'
  | 'follow'
  | 'repost'
  | 'bookmark';

export type E2EActionStatus = 'ok' | 'skipped' | 'failed';

export interface ExtensionE2ERuntimeEvent {
  type: 'extension_e2e_run';
  source?: string;
  run_id: string;
  token: string;
  platforms: E2EPlatform[];
  actions: Partial<Record<E2EPlatform, E2EAction[]>>;
  allow_state_changing: boolean;
  timeout_seconds: number;
}

export interface E2EActionExecutionResult {
  action: E2EAction;
  status: E2EActionStatus;
  executed: boolean;
  selector?: string;
  error?: string;
}

export interface E2EPlatformExecutionResult {
  platform: E2EPlatform;
  status: E2EActionStatus;
  url?: string;
  actions: E2EActionExecutionResult[];
  error?: string;
}

export interface E2EContentExecuteMessage {
  action: 'OBC_E2E_EXECUTE';
  runId: string;
  platform: E2EPlatform;
  actions: E2EAction[];
  allowStateChanging: boolean;
}

export const E2E_PLATFORM_URLS: Record<E2EPlatform, string> = {
  douyin: 'https://www.douyin.com/',
  xiaohongshu: 'https://www.xiaohongshu.com/explore',
  twitter: 'https://x.com/home',
};

export const E2E_STATE_CHANGING_ACTIONS = new Set<E2EAction>([
  'like',
  'favorite',
  'follow',
  'repost',
  'bookmark',
]);

const E2E_DEFAULT_ACTIONS: Record<E2EPlatform, E2EAction[]> = {
  douyin: ['snapshot', 'scroll', 'click', 'share'],
  xiaohongshu: ['snapshot', 'scroll', 'click', 'share'],
  twitter: ['snapshot', 'scroll', 'click', 'share'],
};

export function isExtensionE2ERuntimeEvent(value: unknown): value is ExtensionE2ERuntimeEvent {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const event = value as Partial<ExtensionE2ERuntimeEvent>;
  return (
    event.type === 'extension_e2e_run' &&
    typeof event.run_id === 'string' &&
    typeof event.token === 'string' &&
    Array.isArray(event.platforms) &&
    typeof event.timeout_seconds === 'number'
  );
}

export function actionsForE2EPlatform(
  event: ExtensionE2ERuntimeEvent,
  platform: E2EPlatform,
): E2EAction[] {
  const requested = event.actions?.[platform];
  return Array.isArray(requested) && requested.length > 0
    ? [...new Set(requested)]
    : E2E_DEFAULT_ACTIONS[platform];
}

export function isActionAllowed(action: E2EAction, allowStateChanging: boolean): boolean {
  return allowStateChanging || !E2E_STATE_CHANGING_ACTIONS.has(action);
}
```

- [ ] Add `extension/tests/e2e-shared.test.ts`:

```typescript
import test from "node:test";
import assert from "node:assert/strict";
import {
  actionsForE2EPlatform,
  isActionAllowed,
  isExtensionE2ERuntimeEvent,
} from "../src/shared/e2e.ts";

test("isExtensionE2ERuntimeEvent recognizes signed extension e2e runtime events", () => {
  assert.equal(
    isExtensionE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["twitter"],
      actions: {},
      timeout_seconds: 30,
    }),
    true,
  );
});

test("actionsForE2EPlatform deduplicates requested platform actions", () => {
  const actions = actionsForE2EPlatform(
    {
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["click", "click", "share"] },
      allow_state_changing: false,
      timeout_seconds: 30,
    },
    "twitter",
  );

  assert.deepEqual(actions, ["click", "share"]);
});

test("isActionAllowed blocks state changing actions unless explicitly allowed", () => {
  assert.equal(isActionAllowed("like", false), false);
  assert.equal(isActionAllowed("like", true), true);
  assert.equal(isActionAllowed("share", false), true);
});
```

Verification:

```bash
cd extension && node --test --experimental-strip-types tests/e2e-shared.test.ts
```

### Task 4: Add Background E2E Runner

- [ ] Create `extension/src/background/e2e-runner.ts`.

```typescript
import {
  actionsForE2EPlatform,
  E2E_PLATFORM_URLS,
  type E2EPlatform,
  type E2EPlatformExecutionResult,
  type ExtensionE2ERuntimeEvent,
  isExtensionE2ERuntimeEvent,
} from '../shared/e2e.ts';
import { apiUrl } from '../shared/backend-endpoint.ts';

let activeRunId: string | null = null;

export async function handleE2ERuntimeEvent(event: unknown): Promise<boolean> {
  if (!isExtensionE2ERuntimeEvent(event)) {
    return false;
  }
  if (activeRunId !== null) {
    await postE2EResult(event, [
      {
        platform: event.platforms[0] ?? 'twitter',
        status: 'failed',
        actions: [],
        error: `e2e run already in progress: ${activeRunId}`,
      },
    ]);
    return true;
  }

  activeRunId = event.run_id;
  const platformResults: E2EPlatformExecutionResult[] = [];
  try {
    for (const platform of event.platforms) {
      platformResults.push(await executePlatformE2ERun(event, platform));
    }
  } finally {
    activeRunId = null;
  }

  await postE2EResult(event, platformResults);
  return true;
}

async function executePlatformE2ERun(
  event: ExtensionE2ERuntimeEvent,
  platform: E2EPlatform,
): Promise<E2EPlatformExecutionResult> {
  try {
    const tab = await openOrReusePlatformTab(platform);
    if (typeof tab.id !== 'number') {
      throw new Error(`Missing tab id for ${platform}`);
    }
    await waitForTabComplete(tab.id, event.timeout_seconds * 1000);
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: 'OBC_E2E_EXECUTE',
      runId: event.run_id,
      platform,
      actions: actionsForE2EPlatform(event, platform),
      allowStateChanging: event.allow_state_changing,
    });
    return {
      platform,
      status: response?.status === 'ok' ? 'ok' : 'failed',
      url: tab.url,
      actions: Array.isArray(response?.actions) ? response.actions : [],
      error: response?.error,
    };
  } catch (error) {
    return {
      platform,
      status: 'failed',
      actions: [],
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function openOrReusePlatformTab(platform: E2EPlatform): Promise<chrome.tabs.Tab> {
  const targetUrl = E2E_PLATFORM_URLS[platform];
  const targetHost = new URL(targetUrl).host;
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => {
    if (!tab.url) {
      return false;
    }
    try {
      return new URL(tab.url).host === targetHost;
    } catch {
      return false;
    }
  });
  if (existing?.id !== undefined) {
    return chrome.tabs.update(existing.id, { active: true, url: existing.url ?? targetUrl });
  }
  return chrome.tabs.create({ active: true, url: targetUrl });
}

async function waitForTabComplete(tabId: number, timeoutMs: number): Promise<void> {
  const tab = await chrome.tabs.get(tabId);
  if (tab.status === 'complete') {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error(`Timed out waiting for tab ${tabId} to finish loading`));
    }, timeoutMs);
    const listener = (updatedTabId: number, info: chrome.tabs.TabChangeInfo) => {
      if (updatedTabId === tabId && info.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function postE2EResult(
  event: ExtensionE2ERuntimeEvent,
  platforms: E2EPlatformExecutionResult[],
): Promise<void> {
  await fetch(await apiUrl('/extension/e2e/result'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      run_id: event.run_id,
      token: event.token,
      platforms,
    }),
  });
}
```

- [ ] Modify `extension/src/background/service-worker.ts`.
- [ ] Import the runner:

```typescript
import { handleE2ERuntimeEvent } from './e2e-runner.ts';
```

- [ ] In the runtime event handler, route the new event before task-specific handlers:

```typescript
if (await handleE2ERuntimeEvent(event)) {
  return;
}
```

- [ ] Create `extension/tests/helpers/chrome-mock.ts` so the e2e runner tests do not duplicate a Chrome API mock:

```typescript
export interface TabUpdatedListener {
  (tabId: number, changeInfo: { status?: string }): void;
}

export interface ChromeMockState {
  createdTabs: { url: string; active?: boolean }[];
  updatedTabs: { tabId: number; url?: string; active?: boolean }[];
  sentMessages: { tabId: number; message: unknown }[];
  fetchCalls: { url: string; body?: unknown }[];
  queryResult: Array<{ id?: number; url?: string; status?: string }>;
  tabById: Map<number, { id: number; url?: string; status?: string }>;
  sendMessageImpl: (tabId: number, message: unknown) => Promise<unknown>;
}

export function installChromeMock(): ChromeMockState {
  const listeners: TabUpdatedListener[] = [];
  const state: ChromeMockState = {
    createdTabs: [],
    updatedTabs: [],
    sentMessages: [],
    fetchCalls: [],
    queryResult: [],
    tabById: new Map([[42, { id: 42, status: "complete", url: "https://x.com/home" }]]),
    sendMessageImpl: async () => ({ status: "ok", actions: [] }),
  };

  const chromeMock = {
    tabs: {
      create: async (opts: { url: string; active?: boolean }) => {
        state.createdTabs.push(opts);
        const tab = { id: 42, status: "complete", url: opts.url };
        state.tabById.set(42, tab);
        return tab;
      },
      query: async () => state.queryResult,
      get: async (tabId: number) => state.tabById.get(tabId) ?? { id: tabId, status: "complete" },
      update: async (tabId: number, opts: { url?: string; active?: boolean }) => {
        state.updatedTabs.push({ tabId, ...opts });
        const current = state.tabById.get(tabId) ?? { id: tabId };
        const updated = { ...current, ...opts, status: current.status ?? "complete" };
        state.tabById.set(tabId, updated);
        return updated;
      },
      sendMessage: async (tabId: number, message: unknown) => {
        state.sentMessages.push({ tabId, message });
        return state.sendMessageImpl(tabId, message);
      },
      onUpdated: {
        addListener: (listener: TabUpdatedListener) => listeners.push(listener),
        removeListener: (listener: TabUpdatedListener) => {
          const index = listeners.indexOf(listener);
          if (index >= 0) {
            listeners.splice(index, 1);
          }
        },
        _emit: (tabId: number, changeInfo: { status?: string }) => {
          for (const listener of [...listeners]) {
            listener(tabId, changeInfo);
          }
        },
      },
    },
  };

  globalThis.chrome = chromeMock as unknown as typeof chrome;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    state.fetchCalls.push({
      url: String(input),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(JSON.stringify({ status: "accepted" }), { status: 200 });
  }) as typeof fetch;

  return state;
}
```

- [ ] Add `extension/tests/e2e-runner.test.ts` using `node:test`:

```typescript
import test from "node:test";
import assert from "node:assert/strict";

import { handleE2ERuntimeEvent } from "../src/background/e2e-runner.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

test("e2e background runner opens a platform tab, dispatches content execution, and posts backend result", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "click", status: "ok", executed: true }],
  });

  await handleE2ERuntimeEvent({
    type: "extension_e2e_run",
    run_id: "e2e-test",
    token: "secret",
    platforms: ["twitter"],
    actions: { twitter: ["click"] },
    allow_state_changing: false,
    timeout_seconds: 5,
  });

  assert.deepEqual(state.createdTabs, [{ active: true, url: "https://x.com/home" }]);
  assert.equal(state.sentMessages.length, 1);
  assert.deepEqual(state.sentMessages[0], {
    tabId: 42,
    message: {
      action: "OBC_E2E_EXECUTE",
      runId: "e2e-test",
      platform: "twitter",
      actions: ["click"],
      allowStateChanging: false,
    },
  });
  assert.equal(state.fetchCalls.length, 1);
  assert.match(state.fetchCalls[0].url, /\/extension\/e2e\/result$/);
  assert.deepEqual(state.fetchCalls[0].body, {
    run_id: "e2e-test",
    token: "secret",
    platforms: [
      {
        platform: "twitter",
        status: "ok",
        url: "https://x.com/home",
        actions: [{ action: "click", status: "ok", executed: true }],
      },
    ],
  });
});
```

Verification:

```bash
cd extension && node --test --experimental-strip-types tests/e2e-runner.test.ts
```

### Task 5: Add Content-Script E2E Executor

- [ ] Create `extension/src/content/e2e-executor.ts`.

```typescript
import {
  isActionAllowed,
  type E2EAction,
  type E2EActionExecutionResult,
  type E2EContentExecuteMessage,
  type E2EPlatform,
} from '../shared/e2e.ts';

type Recipe = {
  clickSelectors: string[];
  shareText: RegExp[];
  likeText: RegExp[];
  favoriteText: RegExp[];
  followText: RegExp[];
};

export interface E2EDomEnvironment {
  document: Pick<Document, 'querySelectorAll'>;
  window: Pick<Window, 'getComputedStyle' | 'innerHeight' | 'scrollBy'>;
  sleep: (ms: number) => Promise<void>;
}

const RECIPES: Record<E2EPlatform, Recipe> = {
  douyin: {
    clickSelectors: ['[data-e2e="feed-active-video"]', 'video', '[role="button"]'],
    shareText: [/share/i, /分享/],
    likeText: [/like/i, /赞/],
    favoriteText: [/favorite/i, /收藏/],
    followText: [/follow/i, /关注/],
  },
  xiaohongshu: {
    clickSelectors: ['.note-item', '[data-v-]:not(script)', 'section'],
    shareText: [/share/i, /分享/],
    likeText: [/like/i, /赞/],
    favoriteText: [/collect/i, /收藏/],
    followText: [/follow/i, /关注/],
  },
  twitter: {
    clickSelectors: ['article [role="link"]', 'article', '[data-testid="tweet"]'],
    shareText: [/share/i],
    likeText: [/like/i],
    favoriteText: [/bookmark/i],
    followText: [/follow/i],
  },
};

let registeredPlatform: E2EPlatform | null = null;

export function registerE2EExecutor(platform: E2EPlatform): void {
  if (registeredPlatform !== null) {
    return;
  }
  registeredPlatform = platform;
  chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
    if (!isE2EContentExecuteMessage(message) || message.platform !== platform) {
      return false;
    }
    executeE2EContentMessage(message)
      .then(sendResponse)
      .catch((error) => {
        sendResponse({
          status: 'failed',
          actions: [],
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return true;
  });
}

function isE2EContentExecuteMessage(value: unknown): value is E2EContentExecuteMessage {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const message = value as Partial<E2EContentExecuteMessage>;
  return message.action === 'OBC_E2E_EXECUTE' && typeof message.platform === 'string';
}

async function executeE2EContentMessage(message: E2EContentExecuteMessage): Promise<{
  status: 'ok' | 'failed';
  actions: E2EActionExecutionResult[];
  error?: string;
}> {
  const actions: E2EActionExecutionResult[] = [];
  for (const action of message.actions) {
    actions.push(await executeAction(message.platform, action, message.allowStateChanging));
    await sleep(350);
  }
  return {
    status: actions.every((item) => item.status === 'ok' || item.status === 'skipped') ? 'ok' : 'failed',
    actions,
  };
}

export async function executeAction(
  platform: E2EPlatform,
  action: E2EAction,
  allowStateChanging: boolean,
  env: E2EDomEnvironment = defaultDomEnvironment(),
): Promise<E2EActionExecutionResult> {
  if (!isActionAllowed(action, allowStateChanging)) {
    return { action, status: 'skipped', executed: false, error: 'state_changing_action_blocked' };
  }
  try {
    if (action === 'snapshot') {
      await env.sleep(500);
      return { action, status: 'ok', executed: true };
    }
    if (action === 'scroll') {
      env.window.scrollBy({ top: Math.max(300, Math.floor(env.window.innerHeight * 0.75)), behavior: 'smooth' });
      await env.sleep(700);
      return { action, status: 'ok', executed: true };
    }

    const target = findTarget(platform, action, env);
    if (!target) {
      return { action, status: 'failed', executed: false, error: 'target_not_found' };
    }
    target.scrollIntoView({ block: 'center', inline: 'center' });
    await env.sleep(150);
    target.click();
    return {
      action,
      status: 'ok',
      executed: true,
      selector: describeElement(target),
    };
  } catch (error) {
    return {
      action,
      status: 'failed',
      executed: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function findTarget(platform: E2EPlatform, action: E2EAction, env: E2EDomEnvironment): HTMLElement | null {
  const recipe = RECIPES[platform];
  if (action === 'click') {
    return findFirstVisible(recipe.clickSelectors, env);
  }
  if (action === 'share' || action === 'repost') {
    return findButtonByText(recipe.shareText, env);
  }
  if (action === 'like') {
    return findButtonByText(recipe.likeText, env);
  }
  if (action === 'favorite' || action === 'bookmark') {
    return findButtonByText(recipe.favoriteText, env);
  }
  if (action === 'follow') {
    return findButtonByText(recipe.followText, env);
  }
  return null;
}

function findFirstVisible(selectors: string[], env: E2EDomEnvironment): HTMLElement | null {
  for (const selector of selectors) {
    const element = Array.from(env.document.querySelectorAll<HTMLElement>(selector)).find((candidate) =>
      isVisible(candidate, env),
    );
    if (element) {
      return element;
    }
  }
  return null;
}

function findButtonByText(patterns: RegExp[], env: E2EDomEnvironment): HTMLElement | null {
  const candidates = Array.from(
    env.document.querySelectorAll<HTMLElement>('button, [role="button"], a, div[aria-label], span[aria-label]'),
  );
  return (
    candidates.find((element) => {
      if (!isVisible(element, env)) {
        return false;
      }
      const text = `${element.getAttribute('aria-label') ?? ''} ${element.textContent ?? ''}`.trim();
      return patterns.some((pattern) => pattern.test(text));
    }) ?? null
  );
}

function isVisible(element: HTMLElement, env: E2EDomEnvironment): boolean {
  const rect = element.getBoundingClientRect();
  const style = env.window.getComputedStyle(element);
  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
}

function describeElement(element: HTMLElement): string {
  const role = element.getAttribute('role');
  const label = element.getAttribute('aria-label');
  const testId = element.getAttribute('data-testid');
  return [element.tagName.toLowerCase(), role && `role=${role}`, label && `aria-label=${label}`, testId && `data-testid=${testId}`]
    .filter(Boolean)
    .join(' ');
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function defaultDomEnvironment(): E2EDomEnvironment {
  return {
    document,
    window,
    sleep,
  };
}
```

- [ ] Keep runtime registration API as the production entry point. Tests should call `executeAction` with an injected `E2EDomEnvironment`.
- [ ] Modify platform content entries:

`extension/src/content/douyin.ts`

```typescript
import { registerE2EExecutor } from './e2e-executor.ts';

registerE2EExecutor('douyin');
```

`extension/src/content/xiaohongshu.ts`

```typescript
import { registerE2EExecutor } from './e2e-executor.ts';

registerE2EExecutor('xiaohongshu');
```

`extension/src/content/x.ts`

```typescript
import { registerE2EExecutor } from './e2e-executor.ts';

registerE2EExecutor('twitter');
```

- [ ] Add `extension/tests/e2e-executor.test.ts` using an injected DOM environment:

```typescript
import test from "node:test";
import assert from "node:assert/strict";

import { executeAction, type E2EDomEnvironment } from "../src/content/e2e-executor.ts";

type FakeElement = HTMLElement & { clicked: number; scrolled: number };

function makeElement(attrs: Record<string, string>, textContent = ""): FakeElement {
  const element = {
    clicked: 0,
    scrolled: 0,
    tagName: "BUTTON",
    textContent,
    getAttribute: (name: string) => attrs[name] ?? null,
    getBoundingClientRect: () => ({ width: 20, height: 20 }) as DOMRect,
    scrollIntoView: () => {
      element.scrolled += 1;
    },
    click: () => {
      element.clicked += 1;
    },
  };
  return element as unknown as FakeElement;
}

function makeEnv(elements: FakeElement[]): E2EDomEnvironment {
  return {
    document: {
      querySelectorAll: <T extends Element = Element>() => elements as unknown as NodeListOf<T>,
    },
    window: {
      innerHeight: 900,
      scrollBy: () => {},
      getComputedStyle: () => ({ display: "block", visibility: "visible" }) as CSSStyleDeclaration,
    },
    sleep: async () => {},
  };
}

test("executeAction executes a visible twitter share button click", async () => {
  const button = makeElement({ "aria-label": "Share post" });
  const result = await executeAction("twitter", "share", false, makeEnv([button]));

  assert.deepEqual(
    { action: result.action, status: result.status, executed: result.executed },
    { action: "share", status: "ok", executed: true },
  );
  assert.equal(button.clicked, 1);
  assert.equal(button.scrolled, 1);
});

test("executeAction skips blocked state-changing actions", async () => {
  const result = await executeAction("twitter", "like", false, makeEnv([]));

  assert.deepEqual(result, {
    action: "like",
    status: "skipped",
    executed: false,
    error: "state_changing_action_blocked",
  });
});
```

Verification:

```bash
cd extension && node --test --experimental-strip-types tests/e2e-executor.test.ts
```

### Task 6: Wire Service Worker And Build Entries

- [ ] Confirm the platform entry files imported by `extension/manifest.json` include the executor registration once and only once.
- [ ] Confirm the service worker route does not block existing runtime event handling.
- [ ] Run:

```bash
cd extension && npm run typecheck
cd extension && npm run build
cd extension && node --test --experimental-strip-types tests/e2e-*.test.ts
```

- [ ] Manually inspect built assets:

```bash
ls -1 extension/dist
rg "OBC_E2E_EXECUTE|extension_e2e_run" extension/dist
```

## End-To-End Verification

### Task 7: Real Local E2E With Installed Extension

- [ ] Start backend from the current workspace:

```bash
openbiliclaw start
```

- [ ] Hot reload or reinstall the unpacked extension from `extension/dist` in the user's logged-in Chrome profile.
- [ ] Confirm the backend is reachable and runtime status returns JSON:

```bash
curl -sS http://127.0.0.1:8420/api/health | jq .
curl -sS http://127.0.0.1:8420/api/runtime-status | jq .
```

- [ ] Run safe e2e:

```bash
curl -sS -X POST http://127.0.0.1:8420/api/extension/e2e/run \
  -H 'Content-Type: application/json' \
  -d '{
    "platforms": ["douyin", "xiaohongshu", "twitter"],
    "actions": {
      "douyin": ["snapshot", "scroll", "click", "share"],
      "xiaohongshu": ["snapshot", "scroll", "click", "share"],
      "twitter": ["snapshot", "scroll", "click", "share"]
    },
    "allow_state_changing": false,
    "timeout_seconds": 60
  }' | tee /tmp/openbiliclaw-extension-e2e-report.json | jq .
```

- [ ] Inspect event counts for the exact run window:

```bash
python - <<'PY'
import json
from pathlib import Path
from openbiliclaw.config import load_config
from openbiliclaw.storage.database import Database

report = json.loads(Path('/tmp/openbiliclaw-extension-e2e-report.json').read_text())
db = Database(load_config().database.path)
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
```

- [ ] Accept the run only when each requested platform has at least one captured `click` or `share` action and no platform is fully failed.

### Task 8: State-Changing Optional Run

- [ ] Run this only after confirming the user explicitly allows changing account state in the current conversation:

```bash
curl -sS -X POST http://127.0.0.1:8420/api/extension/e2e/run \
  -H 'Content-Type: application/json' \
  -d '{
    "platforms": ["twitter"],
    "actions": {"twitter": ["like", "bookmark"]},
    "allow_state_changing": true,
    "timeout_seconds": 45
  }' | jq .
```

- [ ] Confirm report marks `like` and `bookmark` as executed or gives an explicit `target_not_found` execution error.

## Documentation Updates

### Task 9: Update Repository Docs

- [ ] Update `docs/modules/extension.md` with:

```markdown
| Extension E2E simulator | Local-only backend endpoint can ask the installed extension to open/reuse Douyin, Xiaohongshu, and X/Twitter tabs, execute whitelisted DOM actions, and verify naturally captured events. |
```

- [ ] Update `docs/modules/api.md` with endpoint references:

```markdown
### POST /api/extension/e2e/run

Local-only endpoint that publishes a signed `extension_e2e_run` runtime event to the extension background worker and returns a per-platform capture verification report.

### POST /api/extension/e2e/result

Local-only callback used by the extension background worker. The request must include the run token issued by `/api/extension/e2e/run`.
```

- [ ] Add a changelog entry at the top of `docs/changelog.md` current version block:

```markdown
- Added a local extension-driven E2E simulator for Douyin, Xiaohongshu, and X/Twitter behavior capture verification.
```

- [ ] Update `docs/architecture.md` and `docs/spec.md` only when the runtime event diagram already mentions extension control flows. Add this node text exactly:

```text
Backend /api/extension/e2e/run -> RuntimeEventHub -> Extension service worker -> Platform content executor -> Existing behavior collector -> /api/events
```

Verification:

```bash
rg "extension/e2e|extension_e2e_run|E2E simulator" docs
```

## Final Verification Matrix

- [ ] Python formatting and lint:

```bash
ruff format src/ tests/
ruff check src/ tests/
```

- [ ] Python types and tests:

```bash
mypy src/
.venv/bin/pytest tests/test_api_app.py -q
```

- [ ] Extension checks:

```bash
cd extension && npm test
cd extension && npm run typecheck
cd extension && npm run build
```

- [ ] Real browser verification:

```bash
curl -sS -X POST http://127.0.0.1:8420/api/extension/e2e/run \
  -H 'Content-Type: application/json' \
  -d '{"platforms":["douyin","xiaohongshu","twitter"],"allow_state_changing":false,"timeout_seconds":60}' | jq .
```

- [ ] Git cleanliness check:

```bash
git status --short
git diff --check
```

## Completion Criteria

- [ ] Backend rejects unsafe or non-local simulator requests.
- [ ] Backend publishes a signed runtime event and waits for the extension callback.
- [ ] Extension background runner opens/reuses one tab per platform and dispatches content execution.
- [ ] Content executor performs platform-specific DOM actions without directly posting behavior events.
- [ ] Backend report separates `executed` from `captured` for every requested action.
- [ ] Unit tests cover validation, action guards, background dispatch, and content execution.
- [ ] Real safe E2E produces captured events for Douyin, Xiaohongshu, and X/Twitter in the installed extension browser.
- [ ] Docs and changelog describe the new local E2E simulator.
