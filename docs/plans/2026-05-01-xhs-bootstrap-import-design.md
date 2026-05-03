# XHS Bootstrap Import Design

## Goal

During first-run initialization, OpenBiliClaw should build the initial profile from both Bilibili account signals and Xiaohongshu account/page signals. Xiaohongshu data must be collected by the browser extension inside the user's logged-in Xiaohongshu web session, not by backend crawling and not by reading Chrome browser history.

## Scope

The first version imports three Xiaohongshu signal scopes:

- `saved`: notes in the current user's profile state / collected tab
- `liked`: notes in the current user's profile state / liked tab
- `xhs_history`: notes shown by Xiaohongshu's own browsing-history/footprint state, when the web product exposes it

All three scopes are best-effort. If Xiaohongshu hides a scope, changes its DOM/state shape, or redirects to login/risk pages, initialization continues with the remaining Bilibili and Xiaohongshu signals.

## Architecture

The backend owns orchestration and profile building. The extension owns Xiaohongshu page access.

1. `openbiliclaw init` runs the current Bilibili history/favorites/following collection.
2. The backend enqueues an `xhs_tasks` row of type `bootstrap_profile`.
3. The extension task dispatcher opens an **active** Xiaohongshu tab with the user's existing browser login state. Init-time bootstrap is foreground for two reasons: (a) the user is running `openbiliclaw init` and explicitly expects to see the profile pull happen — transparency over silent automation; (b) Xiaohongshu's virtualised list paginates only when the tab is active, so headless background tabs return only the first window of items even when scrolling. Discovery tasks (search / creator) stay in background tabs because they run continuously and shouldn't disrupt active browsing.
4. The content script finds the current user's profile URL from the logged-in nav or `__INITIAL_STATE__.userInfo`, then the dispatcher navigates the same tab to that profile page.
5. The content script extracts notes from Xiaohongshu-rendered profile state. It may click the saved / liked profile tabs so the page loads its own state, but it does not call Xiaohongshu APIs directly.
6. The extension posts task results to the backend.
7. The backend converts returned notes into event-layer payloads:
   - saved notes -> `favorite`
   - liked notes -> `like`
   - xhs history notes -> `view`
8. `init` analyzes the combined Bilibili + Xiaohongshu event batch and builds the initial profile from the combined history summary.

This keeps the existing rule: Xiaohongshu content enters through the extension, while the backend never directly logs into or crawls Xiaohongshu.

## Data Shape

Extension task result:

```json
{
  "task_id": "uuid",
  "status": "ok",
  "urls": ["https://www.xiaohongshu.com/explore/..."],
  "notes": [
    {
      "url": "https://www.xiaohongshu.com/explore/...",
      "title": "笔记标题",
      "author": "作者",
      "cover_url": "https://...",
      "scope": "saved",
      "note_id": "...",
      "xsec_token": "..."
    }
  ],
  "scope_counts": {
    "saved": 20,
    "liked": 20,
    "xhs_history": 0
  }
}
```

Backend event conversion:

```json
{
  "event_type": "favorite",
  "title": "笔记标题",
  "url": "https://www.xiaohongshu.com/explore/...",
  "metadata": {
    "source_platform": "xiaohongshu",
    "note_id": "...",
    "xsec_token": "...",
    "author": "作者",
    "import_source": "xhs_bootstrap_saved",
    "signal_strength": 1.0
  }
}
```

Signal strengths are metadata hints for the preference prompt:

- saved: `1.0`
- liked: `0.85`
- xhs history: `0.35`

## User Experience

`openbiliclaw init` reports a new data-fetch line:

```text
1/4 拉取数据
  B站 浏览历史 300 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个（未暴露或未登录）
```

If the extension is not running or Xiaohongshu is not logged in, init continues:

```text
小红书初始化信号未导入：插件未连接或小红书未登录。继续使用 B 站数据初始化。
```

## Risks

- Xiaohongshu may not expose a stable web browsing-history page/state. `xhs_history` remains best-effort and ordinary `/explore` recommendation cards are not imported as history.
- Automatic scrolling can trigger risk controls. First version reads rendered items only, with a small configurable cap and no deep scrolling.
- Profile tabs and state indexes can change. The extractor must tolerate multiple state shapes and skip scopes it cannot prove.

## Testing

- Unit-test backend conversion from XHS bootstrap task result to events.
- Unit-test `XhsTaskQueue` support for `bootstrap_profile`.
- Unit-test extension task URL construction and task validation.
- Unit-test extension note extraction from `__INITIAL_STATE__.user.notes` grouped scopes.
- Run focused backend and extension tests for the XHS pipeline.
