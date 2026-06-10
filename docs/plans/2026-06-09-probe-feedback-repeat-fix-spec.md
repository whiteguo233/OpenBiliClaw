# 2026-06-09 - Probe Feedback Repeat Fix Spec

## 0. Scope

修复用户点击兴趣探针 / 避雷探针后,同一内容又重复出现的问题。反馈入口包括:

- 插件 popup 的消息卡片和画像页 speculative row。
- 桌面 Web `/web` 的消息抽屉和画像 speculative row。
- 移动 Web `/m` 的消息面板和画像 speculative row。
- OpenClaw adapter / CLI 的 next-probe / respond-probe 路径。
- 后端 WebSocket proactive push。

本期目标是让用户已经处理过的探针不会因为后台任务、热重载、安装包常驻进程或前端刷新而重新出现。

Out of scope:

- 不重新设计兴趣探针的推荐策略、distance quota 或 LLM prompt。
- 不改变 confirm / reject 的产品语义。
- 不迁移用户历史画像结构。
- 不解决普通推荐卡重复推送,只处理兴趣探针和避雷探针。

## 1. Current Behavior

用户反馈:

```text
用户点击 兴趣探针: 喜欢 / 不喜欢
或点击 避雷探针: 确实不喜欢 / 不是
UI 先移除卡片
过一会儿或重新打开插件后,同一方向又出现
```

安装包用户更容易遇到,因为桌面包会长期常驻:

- proactive push 循环持续运行。
- post-reload speculator 会后台生成新探针。
- popup / Web / WebSocket 会同时读写 runtime state。
- 用户可能在后台 LLM 任务还没结束时点击反馈。

## 2. Root Cause

单线程路径本身基本正确:

- `/api/interest-probes/respond` confirm 会调用 `InterestSpeculator.user_confirm_speculation()`,把 active item 改成 `status="confirmed"`。
- `/api/interest-probes/respond` reject 会调用 `user_reject_speculation()`,把 item 移出 active 并写 cooldown。
- `/api/avoidance-probes/respond` confirm / reject 也会改 `avoidance_state.json`。
- `/api/profile-summary` 只返回 `status == "active"` 的 speculative items。

真正问题是 JSON state 的并发 lost update:

| 文件 | 当前写法 | 风险 |
|------|----------|------|
| `data/memory/speculative_state.json` | `load -> mutate -> open("w") json.dump` | 后台 tick 先读旧 active,用户点击写 confirmed,后台 tick 后保存旧 active,覆盖用户点击 |
| `data/memory/avoidance_state.json` | 同上 | 避雷确认 / 拒绝可能被后台 avoidance tick 覆盖 |
| `data/memory/discovery_runtime.json` | 整份 dict 保存 | `probe_feedback_history` / `avoidance_probe_feedback_history` 可能被 proactive push 的旧 runtime snapshot 覆盖为空 |

最小复现:

```text
speculator_confirm_ok= True
status_after_click= confirmed
status_after_stale_background_save= active
runtime_feedback_history_after_stale_save= []
```

这说明用户点击确实生效过,随后被后台旧快照覆盖。

## 3. Product Requirements

### 3.1 User-visible guarantees

1. 用户对正向兴趣探针点击 `confirm` 后:
   - 当前 domain 立即从 active 探针中消失。
   - `/api/profile-summary` 不再返回该 domain 的 active speculative interest。
   - `/api/interest-probes/pending` 不再返回该 domain。
   - WebSocket proactive push 不再推同一 active item。
   - 后续 `force_tick()` 可以把它 promotion/writeback,但不能恢复为 active。

2. 用户对正向兴趣探针点击 `reject` 后:
   - 当前 domain 从 active 中消失。
   - domain 进入 cooldown。
   - `probe_feedback_history` 追加 reject 记录。
   - 后续生成和选择必须避开该 domain / axis。

3. 用户对避雷探针点击 `confirm` 后:
   - 当前 domain 从 active avoidance 中消失。
   - 写入 dislike writeback 任务。
   - `avoidance_probe_feedback_history` 追加 confirm 记录。

4. 用户对避雷探针点击 `reject` 后:
   - 当前 domain 从 active avoidance 中消失。
   - domain 进入 cooldown。
   - `avoidance_probe_feedback_history` 追加 reject 记录。

5. 如果前端响应接口返回 `ok=false`,前端必须把它视为 stale / already handled,不能显示"已记住"成功态。
6. proactive WebSocket push 的读侧只能发布 `status == "active"` 且未被 latest cooldown / feedback history / profile 覆盖的探针；`status in {"confirmed", "rejected", "user_rejected"}` 的条目不能被推送。

### 3.2 Persistence guarantees

1. JSON 文件写入必须是 atomic replace,避免半写文件。
2. 同一 state 文件的 read-modify-write 必须串行化。
3. 长耗时 LLM 任务不能持有文件锁。
4. 长耗时 LLM 任务完成后,必须基于最新 state 合并结果,不能保存任务开始时的旧快照。
5. feedback history 是 append-only 审计数据。后台刷新和 proactive push 不能把已有 feedback history 覆盖掉。
6. append-only history 不在本修复里做 100 条之类的隐式截断；如果未来要做 retention,必须是单独、可追踪、已文档化的数据保留策略。

### 3.3 Frontend guarantees

1. 插件 popup 和移动 Web `/m` 都需要有 session-level `handledProbeKeys`,和桌面 Web 一样防止已点击卡片被 profile refresh 立刻 hydrate 回来。
2. `hydrateInboxFromSpeculations()` / mobile `loadNotifications()` / mobile profile speculative sections 必须跳过 session 内已处理的 `(type, domain)`。
3. profile row / message card / top probe card 三个入口都要处理 `ok=false`。
4. WebSocket 收到同 domain 的 stale `interest.probe` / `avoidance.probe` 时,如果该 key 已在 session handled set 中,不能重新展示；该要求同时适用于插件 popup、桌面 Web 和移动 Web。

## 4. Technical Design

### 4.1 Atomic JSON state helper

新增一个小型 JSON state 工具,支持:

- per-process lock: `threading.RLock` by path。
- cross-process lock: lock file + `fcntl.flock` on Unix,`msvcrt.locking` on Windows。
- atomic write: write temp file, fsync, `os.replace()`.
- update API: `update_json_state(path, default_factory, normalize, serialize, mutate)`.
- `normalize` 只处理磁盘读出的 raw JSON；`serialize` 负责把 typed state 转回 JSON payload。禁止对 mutate 后的 typed state 再调用只接受 raw dict 的 `normalize`。
- Windows byte-range lock 必须在 lock/unlock 前都 `seek(0)`,不能依赖 lock file 永远为空。

该 helper 不应该引入第三方依赖。

### 4.2 Speculator state update API

为正向兴趣 state 增加:

```python
update_speculative_state(data_dir, mutator) -> SpeculativeState
```

为避雷 state 增加:

```python
update_avoidance_state(data_dir, mutator) -> AvoidanceState
```

所有 `user_confirm_*` / `user_reject_*` / `observe()` / `ingest_seeds()` 必须通过 update API 修改最新 state。

`tick()` / `force_tick()` 需要拆成两阶段:

1. 快速阶段: 在 update API 中 expire / promote /读取生成所需 snapshot。
2. LLM 阶段: 锁外调用 LLM。
3. 合并阶段: 再次 update 最新 state,基于最新 active/cooldown/profile/feedback history 过滤并追加新 candidates。

合并阶段必须显式丢弃以下候选:

- normalized domain 已存在于最新 `state.active` 的任何未终结条目中,包括 `status in {"active", "confirmed", "user_rejected", "rejected"}`。
- normalized domain 仍在最新 cooldown 中。
- normalized domain 已经是 profile 中的 confirmed like / dislike。
- normalized domain 在最新 runtime `probe_feedback_history` / `avoidance_probe_feedback_history` 中出现过,且其 response 对当前探针类型意味着用户已经处理过该方向。

因此,LLM 在用户点击后返回了一个同 domain 的新 candidate 时,也不能重新加入 active。
合并阶段读取 feedback history 时必须使用一个 concrete loader 重新读取最新 runtime state；不能静默接受 `None` 并复用 LLM await 前的旧 history。

禁止在 LLM await 后直接 `_save_state(old_state)`。

### 4.3 Discovery runtime state update API

为 `MemoryManager` 增加:

```python
def update_discovery_runtime_state(
    self,
    mutator: Callable[[dict[str, object]], dict[str, object] | None],
) -> dict[str, object]:
    ...
```

所有 `load_discovery_runtime_state()` 后又 `save_discovery_runtime_state()` 的探针相关写路径改成 update API:

- `_record_probe_feedback_history()`
- `_record_exploration_buffer_event()`
- `RefreshRuntime._publish_interest_probe_if_available()`
- `RefreshRuntime._publish_avoidance_probe_if_available()`
- `RefreshRuntime._publish_probe_if_available()`
- `OpenClawAdapter._record_probe_history()`

如果保留 `save_discovery_runtime_state()` 给测试和整份初始化使用,它仍需 atomic write,但并发业务路径不得用它做 read-modify-write。
legacy full save 必须在同一个文件锁内读取最新磁盘状态、合并传入 payload、再 atomic replace。它不能截断或覆盖 append-only history；需要保留的 append-only 字段包括 `probe_feedback_history`、`avoidance_probe_feedback_history` 和 `short_term_exploration_buffer.entries`。它也不能用旧 payload 覆盖最新 probe runtime maps,包括 `probed_domains`、`probed_axes`、`probed_distance_bands`、`probed_avoidance_domains`、`probed_avoidance_axes` 和 `last_probe_kind`。其中 `last_probe_kind` 是 scalar: legacy full-save merge 中 latest disk 非空时 latest wins；生产更新必须通过 `update_discovery_runtime_state()` 写入。
生产路径中的 runtime read-modify-write 不应继续调用 legacy full save；所有修改现有 runtime state 的路径都必须使用 `update_discovery_runtime_state()`。

### 4.4 Duplicate defense

后端是主防线,前端只做体验兜底:

- 后端保证已处理 active 不会因为 stale save 恢复。
- 前端 `handledProbeKeys` 保证一次 session 内不会闪回。
- API `ok=false` 清理 stale 卡片。

## 5. Acceptance Criteria

### 5.1 Backend

- 一个 `force_tick()` 在 LLM await 中挂起时,用户 confirm 同一 interest domain。`force_tick()` 完成后,该 domain 仍不是 active。
- 一个 `force_tick()` 在 LLM await 中挂起时,用户 confirm `建筑美学`;随后 LLM 返回一个新的 `建筑美学` candidate。`force_tick()` 完成后,该 domain 仍不是 active。
- 一个 `force_tick()` 在 LLM await 中挂起时,用户 reject 同一 interest domain。`force_tick()` 完成后,该 domain 仍在 cooldown,不在 active。
- avoidance confirm / reject 在同样并发下不被恢复为 active。
- real `MemoryManager` 上,proactive push 保存 `probed_domains` 或 legacy full save stale snapshot 时,不会清空刚写入的 `probe_feedback_history`。
- `mark_notification_sent` / `mark_delight_sent` / refresh bookkeeping 等非探针 runtime 写入,不能覆盖并发写入的 `probed_domains` / `probed_axes` / `last_probe_kind`。
- proactive push 读侧遇到 `status="confirmed"` / `status="rejected"` 的 interest 或 avoidance item 时不发布 WebSocket payload。
- OpenClaw `get_next_probe()` 记录 history 时,不会清空 runtime feedback history。

### 5.2 Frontend

- 插件 popup 点击 message card 后,即使随后 profile-summary 仍临时返回同一 active item,当前 session 不再 hydrate 该卡片。
- 插件 popup 点击 profile row 后同上。
- 插件 popup 收到 `ok=false` 时显示 stale 文案并刷新,不显示成功文案。
- 桌面 Web 现有 `handledProbeKeys` 行为不回归。
- 移动 Web 点击 probe 后,即使 pending/profile refresh 或 WebSocket 又返回同一 `(type, domain)`,当前 session 不再展示该卡片或画像 row。
- 插件 popup / 桌面 Web / 移动 Web 的 WebSocket stale push 都被 handled set 拦截。

### 5.3 Package / runtime

- 桌面安装包的数据根仍为 `~/OpenBiliClaw` / `%USERPROFILE%\OpenBiliClaw`,不引入新的迁移。
- 单实例锁行为不变。
- 后台常驻状态下的并发回归测试通过。

## 6. Test Matrix

| Layer | Tests |
|-------|-------|
| Pure state helper | atomic replace, corrupt JSON fallback, lock serializes update, typed state object serializes without re-normalizing |
| Interest speculator | confirm/reject vs paused force_tick lost-update regression, fresh same-domain LLM candidate after confirm is dropped |
| Avoidance speculator | confirm/reject vs paused force_tick lost-update regression, fresh same-domain LLM candidate after confirm/reject is dropped |
| Runtime state | real MemoryManager feedback history vs stale proactive push / concurrent legacy save / OpenClaw history update |
| API | `respond` then profile-summary/pending no longer returns handled domain |
| Extension popup | hydrate skips handled probe key, WebSocket skips handled key, `ok=false` stale handling |
| Mobile Web | pending/profile/WebSocket skips handled probe key, `ok=false` stale handling |
| Existing suites | `tests/test_speculator.py`, `tests/test_avoidance_speculator.py`, `tests/test_api_app.py`, `tests/test_openclaw_adapter.py`, extension node tests |

## 7. Documentation Impact

Because this changes state persistence and probe behavior, update:

- `docs/modules/soul.md`: 探针反馈并发保证。
- `docs/modules/memory.md`: JSON runtime state update API and atomic write behavior。
- `docs/modules/runtime.md`: proactive push writes no longer use stale runtime snapshots。
- `docs/modules/extension.md`: popup handled-probe session dedupe and stale response handling。
- `docs/changelog.md`: add one bullet under current version block.
