# Issue #352 待聊确认收件箱：真实环境验收（2026-11-17）

## 目的

验证 `fix/pending-confirmation-backlog` 在**真实实例数据 + 真实服务进程 + 真实 HTTP** 下
修复「待聊确认 99+ 只增不减」：

- 疑惑在生成时写 30 天 TTL，历史 `expires_at IS NULL` 行按 `created_at + 30 天` 过期；
- TTL 扫描并入每日清理并读取全部 open/clarifying 行；
- 待聊候选跳过已裁决（`user_verdict`）与超过 30 天未活跃的假设，排序与窗口共用
  `InsightHypothesis.last_seen_at`。

## 环境与方法

- 数据源：本机真实实例 `data/openbiliclaw.db`（243 条 open 疑惑、243/243 `expires_at` 为 NULL）
  与 `data/memory/`（478 条假设）用 `sqlite3 .backup` 做一致性副本，**线上库全程只读**。
- A/B 两个独立 data dir + 独立 `config.toml`（仅改 `data_dir` / `port`，关闭 TLS / tailnet / scheduler）：
  - 补丁版：`OPENBILICLAW_PROJECT_ROOT=/tmp/obc352/patched` + 分支源码，`serve-api --port 19421`；
  - 对照版：`OPENBILICLAW_PROJECT_ROOT=/tmp/obc352/unpatched` + `main` 源码，`serve-api --port 19422`。
- 两进程均以 `OPENBILICLAW_WORKER=0` 启动（真实 API 进程、真实 SQLite、真实路由），
  不发任何 LLM / 发现请求。
- 清理触发：用生产入口 `CognitionCycle.run_if_due()`（`memory` 为真实
  `MemoryManager`，读取真实 `cognition_cycle_state.json`）对各自 data dir 各跑一次。
  运行前把 `last_awareness_at` / `last_insight_at` 置为当前时间（该轮 `ran=false,
  throttled=true`，不产生 LLM 调用），`last_cleanup_at` 置为 3 天前，使该轮只执行每日清理——
  正是 issue 描述的「安静日」路径。
- 观测：真实 HTTP `GET /api/chat/pending-confirmations`（含 `?count_only=1`）+ SQLite 状态查询。

## 结果

| 观测项 | `main`（对照，19422） | 补丁（19421） |
|---|---|---|
| 清理前 `total` / 列表长度 | 325 / 10 | 14 / 10 |
| 清理后 `total` / 列表长度 | **325 / 10** | **1 / 1** |
| 清理后 `confusions` 状态 | open 243 / expired 0 | **open 0 / expired 243** |
| 已裁决假设（`user_verdict="rejected"`，置信度 0.88） | 出现在列表首位 | 不出现 |
| 列表项 `last_seen_at` 字段 | 不存在 | 存在（新行 `2026-11-17`，历史行 `""` 回落 `created_at`） |
| 列表首项 | 已裁决假设（2026-11-17，`<absent>`） | 新假设（2026-11-17，`last_seen=2026-11-17`） |

补丁版清理后 `total` 与列表长度一致（1/1→3/3），角标不再统计用户点不到的历史积压。

## 关键证据

- 插入侧 TTL（真实 DB 副本，走 `ConfusionManager.create_from_awareness_candidates`）：

  ```text
  inserted_id 292
  status open created_at 2026-11-17 11:33:32
  expires_at 2026-12-17T19:33:32.468779
  days_from_now 30.0 expected 30
  ```

- 清理轮次返回（两个 data dir 相同）：`{"ran": false, "throttled": true, "errors": []}`，
  即无 awareness/insight 时也执行了清理。
- 清理后 SQL：`select status,count(*) from confusions group by status` →
  `expired|243, dismissed|31, resolved|17`（对照版仍为 `open|243`）。
- 已裁决过滤：把真实 `InsightAnalyzer._build_hypothesis` 生成的两条假设写入副本
  （一条 `user_verdict="rejected"`、置信度 0.88；一条正常、0.72），补丁版 HTTP 只返回正常那条，
  对照版两条都返回且已裁决那条排第一。
- 线上实例未被触碰：验收前后 `GET :18420/api/chat/pending-confirmations?count_only=1`
  仍为 `{"count":10,"total":322}`。

## 未覆盖 / 说明

- 桌面 Web、移动 Web、扩展 popup 的**真实浏览器渲染**未跑：本机 `ego-browser` 需要先完成
  用户侧 onboarding。三端角标取的是同一 `total ?? count` 字段
  （`web/desktop/assets/js/app.js:7849`、`web/js/views/chat.js:1816`、`extension/popup/popup.js:6676`），
  后端契约已按真实 HTTP 验证，前端无需改动。
- 清理通过生产 `CognitionCycle.run_if_due()` 入口触发；`serve-api` 本身不拥有 24h 调度器
  （那是 full worker 的 soul-pipeline loop），故未等待自然到期。
- 遗留口径：窗口内候选仍可能多于 10 条（本实例清理后为 1–3 条，已一致）。若要彻底保证
  `total ≤ 可见数`，仍需按 issue 建议做容量上限或分页（B/C 档）。
