# Docker Init Gap 修复方案

> **问题来源**: 用户日志分析（2026-05-19），Docker 部署后 soul 层永远为空，xhs producer 卡死
>
> **根因**: `serve-api` 只启动 API 服务器，不调用 `build_initial_profile`。
> `account_sync` 后台只跑 `analyze_events`（写 preference 层），不写 soul 层。
> 用户未手动跑 `openbiliclaw init`，导致 `is_profile_ready()` 永远返回 False。

---

## Spec：四项修复

### Fix 1 — Docker 文档增加醒目警告

**目标**: 让 Docker 用户不可能跳过 init 步骤。

**改动文件**: `docs/docker-deployment.md`

**改动内容**:
- 在「快速开始」代码块内，第 2 步和第 3 步之间插入注释警告
- 内容：不跑 init = 后端是空壳，不会生成画像也不会有推荐
- 在「常见问题」新增一条 Q&A：「Q: 后端启动了但没有推荐？」→ 答：先确认跑过 init

### Fix 2 — README 快速开始补充 init 提示

**目标**: 让选择 Docker 手动部署的用户知道 init 是必做步骤。

**改动文件**: `README.md`、`README_EN.md`

**改动内容**:
- 在 `<details>` 折叠区域（不用 AI 助手：一句话安装/Docker 手动部署）补一句：Docker 手动部署需额外跑 init
- 不改变"三步"结构（AI 助手路径确实自动跑 init，三步描述是正确的）

### Fix 3 — Health endpoint 报告 soul profile 状态

**目标**: 让 healthcheck / 用户 / AI agent 能从 `/api/health` 知道画像是否就绪。

**改动文件**:
- `src/openbiliclaw/api/models.py` — HealthResponse model
- `src/openbiliclaw/api/app.py` — health endpoint
- `tests/test_api_app.py` — 更新精确断言
- `tests/test_api_degraded_mode.py` — 更新精确断言
- `docs/modules/runtime.md` — 更新降级模式 health 文档

**改动内容**:
- `HealthResponse` 新增 `profile_ready: bool | None = None`
- health endpoint 用 `response_model_exclude_none=True`：注入测试里 `soul_engine=object()`（无 `is_profile_ready`）时不输出该字段；生产路径构造真实 `SoulEngine` 时会输出 `profile_ready: true/false`
- 正式运行环境中 soul_engine 有 `is_profile_ready()` 时才输出 `profile_ready: true/false`
- 不影响 HTTP status code（仍返回 200），不影响 Docker healthcheck 的通过/失败判断
- 更新 `docs/modules/runtime.md` 降级模式 health 响应描述，提及新字段

### Fix 4 — 后台自动 bootstrap soul 层

**目标**: `account_sync` 首次成功写入 preference 层后，如果 soul 层为空，自动触发 `build_initial_profile`。

**改动文件**:
- `src/openbiliclaw/runtime/account_sync.py` — sync_now 方法 + auto-bootstrap helper
- `docs/modules/runtime.md` — 新增 auto-bootstrap 功能描述
- `docs/changelog.md` — 新增变更条目

**关键设计决策**:

1. **Protocol 兼容 mypy strict**: `SupportsSoulAnalyzer` 当前只声明 `analyze_events()`。
   `AccountSyncService` 的 `soul_engine` 类型标注不变（仍为 `SupportsSoulAnalyzer`），
   auto-bootstrap 逻辑全程用 `getattr()` + `callable()` 做鸭子类型检查，
   **不新增或扩展 Protocol**——避免破坏现有测试桩和严格 mypy 约束。

2. **events 格式不兼容 build_initial_profile**: `build_initial_profile` 期望 history items
   （含 `title`、`author_name` 等字段），而 `account_sync` 的 events 是行为事件
   （click、scroll、follow 等）。**解决方案**: 传 `history=[]`。
   `build_initial_profile` 的核心输入是 `preference_layer.data`（它从 memory 层直接读），
   `history` 只被 `_summarize_history` 用来提取 titles/authors 作为辅助上下文。
   传空列表时 `_summarize_history` 返回 `{count: 0, titles: [], authors: []}`，
   画像质量略低但 preference + awareness + insight 三层数据已足够生成可用画像。

3. **防止重复触发**: 用实例级 flag `_auto_bootstrap_attempted: bool` 持久化到内存。
   一旦尝试过（无论成功或失败），同一进程生命周期内不再触发。
   不需要持久化到磁盘——进程重启时如果 soul 层仍为空会再尝试一次，这是期望行为。
   失败时只 log warning，不影响后续 sync 循环。

4. **避免昂贵 LLM 重复调用**: `_auto_bootstrap_attempted` flag 确保每个进程生命周期最多触发一次。
   即使 build_initial_profile 失败（如 LLM 不可用），也不会在下一次 sync tick 重试。
   用户仍可手动 `openbiliclaw init` 或等进程重启后自动重试。

---

## Plan：执行步骤

### Step 1: Fix 1 — Docker 文档

编辑 `docs/docker-deployment.md`:

1. 在快速开始代码块的第 2 步和第 3 步之间插入警告注释：

```
# ⚠️ 重要：第 3 步（init）是必须的！
#    不跑 init，后端只是一个空壳——不会生成用户画像，也不会有任何推荐。
#    容器启动后能通过健康检查（/api/health 返回 200），
#    但这只代表 API 服务在运行，并不代表系统已就绪。
```

2. 在「常见问题」末尾新增一条 Q&A：

```
**Q: 后端启动了、健康检查也通过了，但插件里没有推荐？**

最常见原因是没有执行过 `init`。容器启动只运行 API 服务器，
用户画像需要通过 init 命令生成：

    docker exec -it openbiliclaw-backend openbiliclaw init

也可以检查 health endpoint 确认画像状态：

    curl -s http://127.0.0.1:8420/api/health | python -m json.tool
    # 看 "profile_ready" 字段——false 或缺失都表示还需要跑 init

v0.3.80+ 后端会在首次同步到行为数据后自动尝试生成画像，
但手动 init 能获得更完整的初始画像（包含历史标题、作者等上下文信息）。
```

### Step 2: Fix 2 — README

编辑 `README.md`——在 `<details><summary>高级：Docker 部署</summary>` 折叠块内，
在 Docker 部署指南链接后补一段手动 init 提示：

```markdown
> 💡 **Docker 用户注意**：容器启动后还需要执行 `docker exec -it openbiliclaw-backend openbiliclaw init` 生成画像。不跑 init，后端能正常启动但不会有推荐。
```

编辑 `README_EN.md` 对应位置同步英文版本：

```markdown
> 💡 **Docker users**: after the container starts, also run `docker exec -it openbiliclaw-backend openbiliclaw init` to generate your profile. Without init, the backend can start normally but will not produce recommendations.
```

### Step 3: Fix 3 — Health endpoint

1. 编辑 `src/openbiliclaw/api/models.py`:

```python
class HealthResponse(BaseModel):
    """Health-check response."""

    status: str
    service: str
    profile_ready: bool | None = None
```

2. 编辑 `src/openbiliclaw/api/app.py` health endpoint，使用 `response_model_exclude_none=True`：

```python
@app.get("/api/health", response_model=HealthResponse, response_model_exclude_none=True)
def health() -> HealthResponse | JSONResponse:
    profile_ready: bool | None = None
    try:
        se = getattr(ctx, "soul_engine", None)
        if se is not None:
            is_ready_fn = getattr(se, "is_profile_ready", None)
            if callable(is_ready_fn):
                profile_ready = bool(is_ready_fn())
    except Exception:
        pass

    if bool(getattr(ctx, "degraded", False)):
        body: dict[str, object] = {
            "status": "degraded",
            "service": "openbiliclaw-api",
            "reason": str(getattr(ctx, "degraded_reason", "")),
            "issues": _degraded_issues_payload(),
        }
        if profile_ready is not None:
            body["profile_ready"] = profile_ready
        return JSONResponse(status_code=200, content=body)
    return HealthResponse(
        status="ok",
        service="openbiliclaw-api",
        profile_ready=profile_ready,
    )
```

3. **测试更新**：
   - `test_api_app.py:597` 断言 `{"status": "ok", "service": "openbiliclaw-api"}`
     — 测试传 `soul_engine=object()`，`object()` 没有 `is_profile_ready` 方法，
     所以 `profile_ready` 保持 `None`，`response_model_exclude_none=True` 将其排除，
     响应 JSON 与现有断言完全一致。
   - `test_api_degraded_mode.py` 走生产 `create_app()`，会构造真实 `SoulEngine`；
     未跑 init 时 health 应返回 `profile_ready: false`，因此精确断言需要同步更新。

4. 编辑 `docs/modules/runtime.md` 降级模式 health 响应描述，加一句：
   > `profile_ready`（可选）：当 `SoulEngine` 可用时返回，表示 soul 画像是否已生成。

### Step 4: Fix 4 — Auto-bootstrap

1. 编辑 `src/openbiliclaw/runtime/account_sync.py`:

   a. **不扩展 `SupportsSoulAnalyzer` Protocol**——auto-bootstrap 全程用 `getattr`。

   b. 在 `AccountSyncService` 的 `__post_init__` 或类属性中加：
   ```python
   _auto_bootstrap_attempted: bool = False
   ```

   c. 在 `sync_now` 方法的 `await self.soul_engine.analyze_events(events)` 之后插入：

   ```python
   # Auto-bootstrap: preference 层刚写入，如果 soul 层仍为空
   # （用户未手动跑 init），尝试自动构建初始画像。
   # 每个进程生命周期最多尝试一次，避免 LLM 失败时重复调用。
   if not self._auto_bootstrap_attempted:
       is_ready_fn = getattr(self.soul_engine, "is_profile_ready", None)
       if callable(is_ready_fn) and not is_ready_fn():
           self._auto_bootstrap_attempted = True
           build_fn = getattr(self.soul_engine, "build_initial_profile", None)
           if callable(build_fn):
               try:
                   logger.info(
                       "Auto-bootstrapping soul profile (preference layer "
                       "ready but soul layer empty, %d events in this sync)",
                       len(events),
                   )
                   # 传空 history：build_initial_profile 的核心输入是
                   # preference_layer.data（从 memory 直接读），history
                   # 只提供 titles/authors 辅助上下文。preference + awareness
                   # + insight 三层已足够生成可用画像。
                   await build_fn([])
               except Exception:
                   logger.warning(
                       "Auto-bootstrap of soul profile failed; user can "
                       "manually run 'openbiliclaw init' for a richer profile",
                       exc_info=True,
                   )
   ```

2. 编辑 `docs/modules/runtime.md` 已实现功能表格，新增一行：

   ```
   | Soul 画像自动 bootstrap | ✅ | 首次 `analyze_events` 成功后若 soul 层为空，自动调用 `build_initial_profile` 生成画像。每进程最多尝试一次。 |
   ```

3. 编辑 `docs/changelog.md` 在顶部新增版本条目：

   ```markdown
   ## v0.3.80: Docker 部署体验补强（2026-05-19）

   - 后台 account_sync 首次同步成功后，如果 soul 画像层为空（典型场景：Docker 部署未跑 init），自动触发 `build_initial_profile` 生成初始画像；每进程生命周期最多尝试一次，失败不影响后续同步。
   - `/api/health` 新增可选 `profile_ready` 字段，返回 soul 画像是否已生成；不影响 HTTP 状态码和 Docker healthcheck 判定。
   - Docker 部署文档和 README 补充 init 步骤的醒目警告，新增「后端启动但无推荐」FAQ。
   ```

---

## 风险评估

| 修复项 | 风险 | 缓解 |
|--------|------|------|
| Fix 1-2 文档 | 无 | 纯文档改动 |
| Fix 3 Health | 极低 | `response_model_exclude_none=True` + `None` 默认值确保无 `is_profile_ready()` 的注入路径保持旧响应；生产路径会显式返回 `profile_ready: true/false`，对应精确断言已更新 |
| Fix 4 Auto-bootstrap | 低 | 全程 `getattr`/`callable` 不碰 Protocol 定义；`_auto_bootstrap_attempted` flag 防重复调用；`history=[]` 不会炸（`_summarize_history` 处理空列表）；try/except 兜底 |

## 验证方式

1. **Fix 1-2**: 人工审阅文档
2. **Fix 3**: `pytest tests/test_api_app.py tests/test_api_degraded_mode.py -x` 全绿；`curl http://127.0.0.1:8420/api/health` 看到 `profile_ready` 字段
3. **Fix 4**: 启动 serve-api（不跑 init），等 account_sync 拉取历史后：
   - 日志出现 `Auto-bootstrapping soul profile`
   - soul 层文件 `data/memory/soul.json` 非空
   - `is_profile_ready()` 返回 True
   - xhs producer 不再打 "soul profile not ready yet"
4. **mypy**: `mypy src/openbiliclaw/runtime/account_sync.py` 无新 error
5. **ruff**: `ruff check src/openbiliclaw/runtime/account_sync.py src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py` 无新 warning

## 文档同步 checklist

- [x] `docs/docker-deployment.md` — 快速开始警告 + FAQ
- [x] `README.md` — Docker 手动部署提示
- [x] `README_EN.md` — 同步英文
- [x] `docs/modules/runtime.md` — auto-bootstrap 功能行 + health 字段说明
- [x] `docs/architecture.md` + `docs/spec.md` + README 架构图 — account sync -> Memory/Soul bootstrap 数据流
- [x] `docs/changelog.md` — v0.3.80 条目
