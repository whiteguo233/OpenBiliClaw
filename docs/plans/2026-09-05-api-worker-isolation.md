# API / Worker 隔离架构改造计划

> 目标：让后台重活（LLM、embedding、池维护、对话结算、发现/评估）不再影响
> `/api/recommendations` 等用户热路径。
>
> 日期：2026-09-05
> 仓库：OpenBiliClaw
> 分支：`arch/api-worker-isolation`

## 1. 背景

当前 `openbiliclaw start` 在同一个 Python 进程里同时运行：

- FastAPI / WebSocket API；
- 发现、候选评估、embedding；
- 对话结算队列；
- 池维护；
- 推荐 precompute / delight；
- 各类定时后台任务。

日志已经证明后台负载会传导到推荐接口：

```text
pool_maintenance total_ms=57697.5
DialogueSettlementQueue depth=21 — settlement is falling behind.
Provider openai_compatible is cooling down after rate limit.
```

根因不是“异步 I/O 阻塞”，而是：

1. CPython GIL 让 CPU 密集段互相排队；
2. 后台 SQLite 长事务与推荐写入争抢写锁；
3. `serve_with_result()` 仍然串行化且依赖 DB worker；
4. 后台队列没有严格背压，LLM 限流后不断积压重试。

## 2. 目标架构

```text
┌──────────────────────────────┐
│ API 进程                      │
│ FastAPI / WebSocket / serve   │
│ 只读内存推荐快照 + 轻量 API     │
│ 不运行 LLM / embedding / 维护  │
└───────────────┬──────────────┘
                │
                │ 共享 SQLite + 任务表 + 快照文件
                │
┌───────────────▼──────────────┐
│ Worker 进程                  │
│ discovery / eval / LLM       │
│ embedding / settlement       │
│ pool maintenance / precompute│
│ 可被 nice/容器限流             │
└──────────────────────────────┘
```

### 原则

- **热路径只读，慢路径只写**；
- **推荐接口不依赖后台任务完成**；
- **后台任务失败只降级后台质量，不影响 API 可用性**；
- **所有 heavy CPU 工作不在 API 进程执行**；
- **队列有界、可持久化、可退避、可观测**。

## 3. 建议分期实施

### Phase 0：建立 worker 进程骨架
- 新增 `openbiliclaw worker` 入口；
- 新增 `src/openbiliclaw/worker/` 包；
- worker 从独立进程启动，持有自己的 DB connection；
- 先运行“安全”的后台循环（pool maintenance / precompute），不迁移所有逻辑。

### Phase 1：推荐热路径去 DB
- 服务端维护一个 `PoolServeSnapshot` 内存/文件快照；
- Worker 定期构建并原子替换；
- `serve()` 只读快照 + 排序，不主动查 SQLite、不触发写操作；
- 失败时回退到当前 DB 路径作为保护。

### Phase 2：SQLite 写锁隔离
- Worker 拥有唯一 writer；
- 维护事务拆成短事务（每批小步提交）；
- API 进程只读；
- 如果仍要写（shown/feedback），走独立 outbox / 队列，API 不等待。

### Phase 3：后台任务有界化
- 对话结算队列改为持久化有界队列；
- LLM 限流使用指数退避，不堆积；
- 队列深度、worker 延迟暴露到 `/api/runtime-status`；
- 高优先级任务（用户主动）与低优先级任务（后台）分离。

### Phase 4：完全拆分
- 逐步把 discovery、eval、soul settlement 移到 worker；
- API 进程可独立重启；
- 部署形态：单机两进程，或容器双进程；
- 保留单机 SQLite 兼容，不强制引入外部队列/DB。

## 4. 验收指标

- 后台 `pool_maintenance` 长时间运行时，`/api/ping` 和 `/api/recommendations` 延迟稳定在 <100ms；
- Worker 崩溃不影响 API 返回；
- 后台 LLM 限流时推荐接口不受影响；
- `DialogueSettlementQueue` 不再无限积压；
- 全量测试通过。

## 5. 风险

- 数据一致性：推荐快照可能短暂落后；
- 进程间通信复杂度；
- 迁移成本：需要把当前 RuntimeContext 拆开；
- 旧测试依赖单进程行为，需要逐步适配。

## 6. 当前进度

- [x] 创建 worktree / 分支；
- [x] Phase 0：worker 进程骨架；
- [x] Phase 1（首版）：worker 发布推荐 serve snapshot，serve 优先读快照，缺失时回退 DB；
- [x] Phase 1（完整）：推荐 shown/history 写入通过 outbox 异步发到 worker；
- [x] Phase 2（首版）：API 热路径不再同步等待 SQLite 写，worker 负责 drain outbox；
- [ ] Phase 2（完整）：维护事务进一步拆短 + API 进程完全零写；
- [x] Phase 3（首版）：DialogueSettlementQueue 有界，低优先级后台任务超限丢弃；
- [ ] Phase 3（完整）：LLM 限流指数退避 + 队列延迟/深度暴露到 runtime-status；
- [ ] Phase 4：完全拆分。
