# 🏛️ 架构总览图

> 本文档从 README 中拆出，集中存放架构总览 ASCII 图，避免 README 过长。
> 完整架构细节见 [架构设计](architecture.md) 与 [可视化架构图](index.md#可视化架构图)。


```text
interactive（对话 / 配置探测）───────────────────────┐
                                                    ├─ runtime total gate (default 4) ─ 有序实例链 ─ Provider 适配
background ─ background admission (default 3) ──────┘
             ├─ refill: expression > evaluation > supply
             │  ├─ 低库存 supply 含探索词 / 来源抽取
             │  └─ while queued: guarantee 2, may borrow all 3
             │     expression owner: 8 immediate / 3s fixed tail / 60 drain / 30×2 provider
             └─ maintenance: at most 1 while refill waits;
                parked when canonical available = 0

引导初始化：信号 → 偏好 → 完整画像提交 → 发现 → 评估 → 推荐文案 → canonical 内容可用
                                                     └→ 终态后再调度可选探针

Agent 宿主（OpenClaw / Hermes / WorkBuddy）
         → capabilities(agent-bridge/v2) + JSON CLI / skill descriptors
         → integrations.agent 别名 / integrations.openclaw 兼容适配器
         → runtime / soul / recommendation / saved_sync 业务所有者

配置恢复草稿（正常或降级；业务 API 仍阻断）
         ├→ /api/config/probe-service → 临时 registry → 总并发 gate
         └→ /api/config/discover-models → 精确实例 GET /models（不写配置）
                                      → 可编辑模型下拉 + 本地 Effort 建议
抖音来源补货：daemon presence 门（显式手动调用绕过）→ 单轮共享插件等待预算
             → dy_task 终态 → pending_eval；离线零入队，失败有界退避
本机数据迁移：导出 → 去除 api.auth 的配置 + SQLite 在线快照 + 可移植文件 → 明文 .obcbackup
            导入(request_id) → processing(上传/校验) → 私有暂存 ↔ status / cancel
                               ↘ 每次打开通用设置强制对账；applied 偏好按 migration_id 每浏览器一次
                               → 重启取得项目 + canonical data-dir lock → 替换成功 | 回滚原数据
持久对话回复：reply_to_turn_id + 固定时间/payload → POST-time frozen binding → pending SQLite → rowid 串行 reply worker → 可见 completion CAS（app-stable 对话 lease）
回复后学习/对象结算：独立 11-kind typed 结算单队列 → actual worker + guard
确认入口（待聊列表/卡片）→ 单锚(kind+ref+generation) → 全入口 frozen admission / 归属矩阵
                       ├→ 待聊≤3 · 主动零冷却 / 系统12h+对象72h · 确认先于用户附着
                       ├→ worker忙：dialogue_busy + Retry-After → 三端等待态自动重试
                       ├→ 已澄清疑惑：只展示当前持有者；当前 session 已有 turn 则隐藏
                       ├→ frozen kind/ref/generation → worker-only apply → event/object/derived/marker → applied
                       │                                                └→ publication-only retry → 跨 session 投影 / 精确解锚
                       ├→ one context digest → prompt/history/event/learn/settlement provenance
                       ├→ action 本地≤1s：完成 200 / 阻塞 202 → popup/移动/桌面 1/2/5s 轮询≤30s
                       └→ 疑惑 FIFO≤5 / 队头 fencing / 12h 补扫
配置保存：事务落盘后统一 HTTP 202 queued/apply_revision → latest-wins 后台应用队列 → apply-status / 成功失败回执；data_dir 仅持久化，完整重启后切换
配置热重载：保持接单并排空旧 worker → 原子暂停/revoke → 新 worker；安全窗25分钟
实时连接：runtime-stream 20s idle 心跳 → 短暂 close 显示重连中并自动续连
封面：proxy 前台 + refresh 预取 → app-stable lane（总4/后台3、前台优先）
                               → cache-key singleflight → 白名单抓取 → 原子缓存
```

```
┌────────────────────────────────────────────────┐
│    浏览器插件（Chrome / Firefox / Safari）     │
│   行为采集 · MAIN-world tap（评论/弹幕·xhs强信号）│
│   Cookie 同步 · 平台任务 · 侧边栏推荐             │
└──────────────────────┬─────────────────────────┘
                       │ HTTP 默认：IPv4 0.0.0.0 + IPv6 [::] → REST / WebSocket
                       │ HTTPS 可选：公网 Caddy :443 / LAN TLS Proxy :8443 → loopback HTTP → 同一 API
                       │ + 桌面 Web (/web) · 移动 Web (/m) · QR LAN-IP
                       │ + ping 预检降级 → /web · /setup · /m → 配置后原地恢复
┌──────────────────────▼─────────────────────────┐
│                  Agent 编排层                    │
│ Skill · 对话 · Runtime · 反馈 10s 可撤销提交屏障    │
├─────────┬──────────┬───────────┬───────────────┤
│  Soul   │  Memory  │ Discovery │ Recommendation │
│ 灵魂画像 │ 五层记忆  │多源发现+准入│   推荐与表达     │
├─────────┴──────────┴───────────┴───────────────┤
│ 普通事件/推荐点击 → generic durable cursor ─┐    │
│ 内容反馈 → content_feedback durable cursor ─┴→ buffer+cursor 同一原子 checkpoint │
│ 30天历史：click events + recommendations + saved_item_removals → 三端分页/lazy │
│ dislike：单卡同步隐藏；主题写入后 effective snapshot → 推荐历史/换批/通知最终复核 │
│ discovery 可继续宽搜；异步语义清池只优化库存，不作为展示正确性边界 │
│ 首启 fence+task admission → listener；后台 owner recovery → tick_if_buffered │
│ 热重载 pause/drain/recover 后 rebind；周期画像维护才调用 tick │
│ 对话 → typed settlement worker → learning          │
│ 旧反馈批：unified_interest_line=false 时启用   │
│ 初始化屏障：完整画像落盘 → 发现/评估/表达 → 可浏览推荐 │
│ B站供给：普通相关性搜索 + 预算内 1×5 pubdate recent lane → 统一评估 │
│ 候选评估：时间中性相关性 + Agent 结构化时效证据 → eligible / review hold / expired + 发布时间 bonus │
│ 推荐时效 shadow：含 bonus vs 无 bonus Top10/50/100 聚合 → class/source/age 审计（不改 serving）│
│ 封面：proxy前台 + refresh预取 → app-stable 4/3 lane → singleflight/原子缓存 │
│ Soul 认知纪律：待聊双轨冷却 · 单对话锚 · worker-only 结算 · 轻量 winner receipt · 疑惑 FIFO · 台账 · 深层门控 │
│   LLM 适配层 · 多平台源适配（SourceAdapter）        │
│   模块路由 → LLM 实例链 → Provider 适配 · 多平台源适配（SourceAdapter） │
│   可选视觉预热：封面 / 画像质心 / 关键帧 + 弹幕 document embedding     │
│   provenance（provider/model/dim/采样）→ 成功空 / 瞬时失败 → 下轮重试   │
│   配置恢复草稿（正常/降级）→ 临时探测 / 精确实例 /models（不写盘）│
│   本机迁移：checksummed .obcbackup → request-id pending ↔ status/cancel → 重启 replace/rollback │
│  来源族注册表：alias · strategy · URL host             │
│             → pool 统计 · seen_items 持久化已看账本     │
│ Bangumi 官方匿名 API → search/ranked/latest producer → shared eval │
│ V2EX 匿名 API/Feed → 有界 Topic/Reply 增强 → 五分支 producer → shared eval │
│ V2EX 身份梯级：PAT verified > browser observed > user accepted；冲突时只暂停账号画像写入 │
│ 时效生命周期：正文逐字证据 + code-owned 复审时钟 → 可展示 / temporal_review_hold / 过期 │
│ evaluator prefilter 默认 shadow → 隐私安全决策/原始分数 join → 只读质量 gate（不自动 enforce）│
│ eval_scorer：llm 默认；shadow/learned hybrid 并跑完整 LLM → 隐私安全对照 → 只读 gate │
│ cognition named views → task-scoped gate：仅 awareness_confusions compact；其余 legacy │
│ token diet：偏好逐段真实装箱；洞察 近期/裁决保底 + 相关/重要/多样性加权≤40 → 完整历史 merge │
│ keyword planner → 24h 安全跨 digest pending 整理 → 缺口/生成/领取（0=硬过期）│
│ admitted backlog → copy 水位 ∪ 可换 topic 空位缺口 → eligible-first 补文案（0=legacy drain-all）│
│ API projected=available+eligible copy-pending+evaluated → 3×30 worker → 串行入池 → 四端 │
│ API raw 断供 → 欠份额来源即时并行补给 → 真实新增清退避 / 重复空转阶梯退避 │
│ 惊喜就绪门：正式推荐词/主题就绪 + seen_items 硬过滤 → 打分并原子快照 → 四端 × 写回已看账本 │
│ 库存 API/OpenClaw 启动钩子 → 历史恢复/原子维护 → 再暴露 LLM │
│ 换屏快路：当前卡硬排除 → hold/stale 清退 + PoolServeSnapshot → 最终时效复核+recommendation/shown → reshuffle 事件 │
│ 平台定向（仅 PC Web Tab）：source_platform → 平台候选（不跨平台补位）→ 同一排序/文案/持久化 │
│ 平台库存：platform-availability → 同一 canonical 可推集合 → total == Σ by_platform │
│ 后台维护：独立 DB worker → ≤50 行/批 → 释放写锁；未变化跳过 / 10min 巡检 │
│ /api/saved/* · 保存 Router · B 站原生保存 Adapter      │
│ 六平台 Adapter → ExtensionNativeSaveBroker → extension_native_save_jobs │
│ 七平台 source task multiplex：xhs / dy / yt / x / zhihu / reddit / linuxdo │
│ 七源 source task multiplex：xhs / dy / yt / x / zhihu / reddit / v2ex  │
│ 扩展在线周期回拉（默认关闭，显式 opt-in）：Runtime → 六源 bootstrap task（全局串行）→ installed extension │
│ task-result → staged durable ingress → 原子有界 seen keys（每源5000）→ terminal │
│ V2EX 完整收藏快照 → 连续两次缺失确认 → durable retraction/restore outbox → account-scoped Node affinity │
│ XHS 自动任务：来源/调度领取门 → SQLite 节流/风控冷却 → 关闭或限流时不开新 tab │
│ XHS 搜索：inactive tab → MAIN 搜索响应归一化 → isolated replay / DOM 兜底   │
│ Linux.do：isolated task tab → 同源 GET → 五路 discovery / 三路 bootstrap │
│ extension_native_save_jobs -> /api/sources/<slug>/next-task -> installed extension │
│ exact OpenBiliClaw / YouTube Watch Later 目标 → 安全 task-result          │
│ trusted-local E2E 精确授权 → 单 item saved sync → 六字段安全 callback      │
│ unsupported_adapter_missing 可重试 · unsupported_content_type local-only │
│ Canonical ID · Local-first SavedSync · Task Poll · SQLite（事件 · 已看账本 · 候选池 · 推荐 · 保存/任务 · 移除快照）│
│ 六平台 adapter → broker → shared MV3 recovery barrier → Reddit/X/YT/XHS/DY/Zhihu executor（6/6 fixture + real-account）│
└────────────────────────────────────────────────┘

Web/API durable → rowid 顺序回复 worker → app-stable 对话 lease(max active 1) → SocraticDialogue(queued) → 可见 CAS
惊喜/legacy/兴趣探针/避雷探针 chat ────────────────────────────────┘（回复与必要副作用同 lease）
回复完成后的 11-kind learning/settlement → 独立 typed 结算单 worker（不属于 reply backlog）
CLI/OpenClaw → SocraticDialogue(legacy_direct) → user+agent 历史 → 队列/guard 外 direct learning
学习 → 绕过后台门禁、保留总并发 ── 新避雷：共享清池 → content_cache
瞬态/provider/超时/取消 → 回滚临时历史 → durable pending + 队头有界重试；显式空/无效响应 → failed CAS
durable turn → 固定时间/payload → 确认入口（待聊列表/卡片） → frozen anchor admission → relation matrix
                                                   └→ 卡片/锚/chat/probe/confusion/replay/legacy 全部 worker-only
卡片 action → 同步 200（空队列快路）| 202 processing → popup/移动/桌面轮询；CLI 无 action

桌面首屏：推荐 hydration │ runtime hydration │ health/profile/activity/config 次级 hydration（三分支独立）
桌面后台恢复（已有卡片）：跳过可能补池的推荐 GET │ 只同步 runtime / 库存状态

海外请求：设置页 `[network].mode` → 系统代理（默认）/ 直连 / 自定义代理 → LLM、YouTube、X/Reddit CLI、Bangumi、更新、GitHub 项目统计；国内平台（含 V2EX）保持独立直连
手动抖音发现：CLI discover → daemon 同款 producer → 统一关键词终态 → 插件 search/hot/feed → 待评估池
```
