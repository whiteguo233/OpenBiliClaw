# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。
>
> 当前 CLI 已统一使用 Rich 输出：
> - 页面标题采用统一标题面板
> - 状态反馈统一为成功 / 警告 / 失败 / 开发中几类状态块
> - 推荐列表使用卡片式展示
> - 用户画像使用分区块展示

## 全局选项

```bash
openbiliclaw [--log-level DEBUG|INFO|WARNING|ERROR] <命令>
```

## 命令一览

| 命令 | 说明 | 状态 |
|------|------|------|
| `config-show` | 显示当前配置、LLM 实例、全局调用链和最终默认实例 | ✅ |
| `config-export-legacy` | 导出可供旧二进制读取的固定 Provider 配置副本 | ✅ |
| `health-check` | 检查 LLM Provider 可用性 | ✅ |
| `auth login` | 设置并验证 B 站 Cookie | ✅ |
| `auth status` | 查看认证状态 | ✅ |
| `login codex` | 导入 / 探测 / 查看 / 删除 Codex CLI 的 ChatGPT OAuth 凭据（实验） | ✅ |
| `browser status` | 检查 agent-browser 安装 | ✅ |
| `browser open <url>` | 通过浏览器打开页面 | ✅ |
| `browser content <url>` | 获取页面文本内容 | ✅ |
| `start` | 启动本地 API 服务 | ✅ |
| `set-password` | 设置 / 修改局域网访问密码（`--disable` 关闭门禁 / `--logout-all` / `--rotate-secret`） | ✅ |
| `ext-key generate` | 生成并保存一个扩展设备访问密钥（明文只显示一次） | ✅ |
| `ext-key enable` | 开启远程扩展设备认证（默认关闭） | ✅ |
| `ext-key disable` | 关闭新会话交换但保留密钥摘要 | ✅ |
| `ext-key list` | 仅列出设备 key ID 和开关状态 | ✅ |
| `ext-key revoke <key-id>` | 撤销设备密钥并立即失效所有现有会话 | ✅ |
| `autostart status` | 查看开机自启动配置、系统注册和平台支持状态 | ✅ |
| `autostart enable` | 注册当前用户登录自启动并写入 `[autostart].enabled=true` | ✅ |
| `autostart disable` | 移除当前用户登录自启动并写入 `[autostart].enabled=false` | ✅ |
| `db-repair` | 检查、备份并修复本地 SQLite 数据库 | ✅ |
| `serve-api` | 启动容器友好的 API 服务 | ✅ |
| `tls-proxy enable [--san HOST_OR_IP]...` | 持久开启可选 LAN/self-managed HTTPS 入口 | ✅ |
| `tls-proxy disable` | 持久关闭 TLS 入口（不删除证书） | ✅ |
| `tls-proxy status` | 显示开关、端口、证书目录与 SAN | ✅ |
| `init` | 首次初始化 / `--force` 重新初始化 | ✅ | stage 1 的 B 站收藏事件补上 `bvid` / url / `fav_time`（2026-07-26+）：此前收藏没有身份，进不了 `seen_items`，收藏过的视频会被当新内容推回；历史事件同时补 `content_id` / 完播秒数 / 时长 / 分区，供偏好分析 prompt 与画像抽样权重区分满播与划走；2026-07-27 起 `view` 也参与满意度判定，但**只判正向**：完播 ≥80% 且观看 ≥15 秒 → `positive/finished_watch`，低完播保持 `unknown` 不判负。收藏还会带上播放量 / 发布时间 / 简介（截 200 字，仅入库不进 prompt），并按 `attr` 丢弃失效视频——它们的标题字面是「已失效视频」，占真实样本 6%，原样进画像等于凭空造出一个兴趣。导入前按 `(事件类型, 内容身份, 时间戳)` 跳过账本已有行：重跑 init 曾让账本 56% 变成重复行；键含时间戳让真实重看仍能落地，无身份的行一律保留 |
| `fetch-douyin` | 单独触发抖音 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-xhs` | 单独触发小红书 bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-youtube` | 单独触发 YouTube bootstrap 拉取（不重建画像；默认复用近期任务） | ✅ |
| `fetch-zhihu` | 单独触发知乎事件拉取（默认 smoke；可选写入 memory / 重建画像） | ✅ |
| `fetch-x` | 单独触发 X（Twitter）点赞 / 收藏拉取（服务端 cookie 重放，无扩展任务，不需 daemon；`--dry-run` 只打印不入库） | ✅ |
| `fetch-reddit` | 单独触发 Reddit 插件 bootstrap 或搜索 smoke（默认不写 memory、不重建画像） | ✅ |
| `fetch-linuxdo` | 单独触发 Linux.do 书签 / 点赞 / 阅读记录只读 smoke（默认不写 memory） | ✅ |
| `fetch-bangumi` | 读取 Bangumi 公开收藏（默认只读，不写 memory、不调用 LLM） | ✅ |
| `fetch-v2ex` | 只读验证 V2EX 发布、讨论、收藏主题、收藏 Node 四个 bootstrap scope（不写 memory、不调用 LLM） | ✅ |
| `import-youtube <path>` | 从 Google Takeout 导入 YouTube 历史 / 订阅 / 点赞 | ✅ |
| `setup-embedding` | 配置本地 Ollama 作为独立 embedding provider（可选） | ✅ |
| `embedding-cache-stats` | 查看 embedding L2 持久化缓存诊断（行数、载荷、文件/WAL 大小、namespace 分布、容量水位、最近维护） | ✅ |
| `embedding-cache-clean` | 清理 embedding L2 缓存：迁移旧 JSON 为二进制 + 回收失效 namespace + 物理回收磁盘（默认 dry-run；`--apply` 生效；`--keep-model` / `--keep-legacy` / `--no-compact` / `--batch-size`） | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment\|dismiss> [--request-id <stable-id>]` | 对推荐提交反馈；省略 ID 时生成并回显，跨命令重试必须复用 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `questions` | 只读查看对话确认入口的待聊假设与疑惑 | ✅ |
| `keyword-inspiration-dry-run` | 真实调用当前 LLM + inspiration 搜索 provider 链，预览关键词生成中间链路，不写关键词池；支持 `--persist-axes` | ✅ |
| `keyword-inspiration-preview` | `keyword-inspiration-dry-run` 的等价别名；支持 `--persist-axes` | ✅ |
| `keyword-inspiration-report` | 输出 inspiration / merged 关键词 cohort 对比和 replace 启用门禁判定 | ✅ |
| `profile-consolidate` | LLM 整理合并画像里重复的喜欢 / 讨厌主题；也支持一级分类词表迁移（默认 dry-run；`--apply` 写入；`--revert <run_id>` 回滚） | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `discover-douyin` | 单独调试抖音 search / hot / feed 内容发现 | ✅ |
| `discover-zhihu` | 单独触发知乎插件搜索 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-hot` | 单独触发知乎热榜 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-feed` | 单独触发知乎首页推荐 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-creator` | 单独触发知乎作者页 discovery，并把候选写入待评估池 | ✅ |
| `discover-zhihu-related` | 单独触发知乎相关内容 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit` | 单独触发 Reddit 搜索 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-hot` | 单独触发 Reddit 热门 discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-subreddit` | 单独触发指定 subreddit discovery，并把候选写入待评估池 | ✅ |
| `discover-reddit-related` | 单独触发 Reddit 相关内容 discovery，并把候选写入待评估池 | ✅ |
| `discover-linuxdo` | 按配置的 search / hot / feed / creator / related 分支执行 Linux.do discovery | ✅ |
| `discover-bangumi <keyword>` | 只读验证 Bangumi Subject 搜索 | ✅ |
| `discover-bangumi-ranked` | 只读验证 Bangumi 排名浏览 | ✅ |
| `discover-bangumi-latest` | 只读验证 Bangumi 按日期浏览（可能含未播条目） | ✅ |
| `discover-v2ex <keyword>` | 只读验证 V2EX Topic 搜索召回 | ✅ |
| `discover-v2ex-node <node>` | 只读验证 V2EX Node Topic 召回 | ✅ |
| `discover-v2ex-tab <tab>` | 只读验证 V2EX Tab Feed 召回 | ✅ |
| `discover-v2ex-hot` | 只读验证 V2EX 热门 Topic | ✅ |
| `discover-v2ex-latest` | 只读验证 V2EX 最新 Topic | ✅ |
| `search-douyin` | 通过浏览器插件调试抖音搜索召回 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |
| `ledger` | 查看画像更新台账（`--line` 逐行 / `--days` / `--write-point` 过滤） | ✅ |
| `delight` | 手动查看当前惊喜推荐候选 | ✅ |
| `probe` | 手动查看并确认猜测兴趣方向 | ✅ |
| `quality-gate suggest-patterns [--apply]` | 让 LLM 根据画像 `disliked_topics` 与近期低质内容标题生成 Clickbait 正则建议；`--apply` 直接写入 `config.toml` 的 `[quality_gate].clickbait_patterns` | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli capabilities` | Agent Bridge capability negotiation：协议版本、宿主名和完整 skill 清单 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe` | Agent JSON bridge：拉取下一条不喜欢领域探针 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe` | Agent JSON bridge：确认 / 否认 / 暂缓 / 多聊避雷探针 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli respond-interest-probe` | Agent JSON bridge：确认 / 否认 / 暂缓 / 多聊兴趣探针 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli respond-delight` | Agent JSON bridge：惊喜卡片 view / like / dislike / dismiss / chat | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli activity-feed` | Agent JSON bridge：读取活动流 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli platform-availability` | Agent JSON bridge：读取平台库存可用量 | ✅ |
| `python -m openbiliclaw.integrations.openclaw.cli save-local/list-saved/remove-saved/sync-saved` | Agent JSON bridge：本地优先保存与显式授权同步 | ✅ |

CLI/Agent Bridge 保持兼容但不新增卡片选择 UI，也不伪造 `reply_to_turn_id` 或
`dialogue_binding`。它们继续走显式 `legacy_direct` 对话入口；chat 现在会返回并持久化
自己的 `turn_id`，但不加入 API runtime 的 settlement queue；三端图形客户端的
server-owned binding、context preview 与卡片 action 不改变 CLI 的既有契约。

## 详细说明

### `openbiliclaw config-show`

显示当前加载的配置、已注册的 LLM 端点实例、全局调用链和最终生效的默认实例。旧格式仍显示等价结果；v2 会额外区分实例 ID 与 Provider 类型，因此两个同类型渠道不会被合并。
配置概览会直接显示「停止后台 LLM 请求」是否启用、「浏览器断开后暂停」是否启用和当前宽限秒数、「开机自启动」配置 / 系统注册状态、海外网络模式与自定义代理地址，以及默认关闭的「收藏自动同步」解析状态，方便确认实际网络路由和 `[saved_sync].auto_sync_enabled` 是否已经写入后端配置。
推荐引擎构造同样读取 `[discovery]` 的 `keyframe_max_frames`、`keyframe_fetch_limit`、
`danmaku_fetch_limit` 和 `danmaku_max_chars`；手动 `recommend` 的预热范围与 daemon/API 使用同一配置。
`config-show` 还会显示 B 站发布日期范围和权重，便于确认最终解析值；权重默认 `0.5`，设为 `1`
表示严格忽略范围外候选，范围外候选本身不会从池中删除。
CLI 组合根也会把 `[scheduler].copy_ready_target_count` 规范为
`min(max(copy_ready_target_count, 0), max(pool_target_count, 0))` 后注入推荐引擎，与 API 和
OpenClaw 的文案水位语义一致，同时注入 `pool_target_count`：正数 copy 水位会优先补齐当前
topic 展示窗口可兑现的公开库存缺口，再维持 unrestricted copy-ready 水位；显式设为 `0`
时保留 legacy drain-all 回滚行为。

```bash
$ openbiliclaw config-show
当前配置概览
配置项
  收藏自动同步  关闭
Provider 概览
  LLM 默认调用链      deepseek-cn → relay-hk
  已注册 Provider 实例  deepseek-cn (deepseek), relay-hk (openai_compatible)
  最终默认 Provider 实例 deepseek-cn
```

`config-show` 只读取并展示配置，不创建保存任务，也不会执行平台账号写入。当前没有默认执行
原生保存写入的 CLI smoke；Bilibili `favorite` / `watch_later` 的真实 E2E 通过平台中立
`/api/saved/*` 明确选择命名 BV ID，并且必须先取得当次用户授权或使用测试账号。

### `openbiliclaw config-export-legacy`

把当前有效配置投影为上一代固定 Provider schema，默认写到当前配置旁的
`config.legacy.toml`。源 `config.toml` 始终保持不变，也不允许把 `--output` 指向源文件；
目标已存在时默认拒绝，只有显式 `--force` 才覆盖。

```bash
# 默认输出 config.legacy.toml
openbiliclaw config-export-legacy

# 指定路径；确认覆盖已有的导出副本
openbiliclaw config-export-legacy \
  --output ./rollback/config.toml \
  --force
```

命令会写临时文件、用当前解析器按旧 schema 回读验证，成功后才替换目标。导出规则及告警如下：

- 全局链保留首个可用实例，并选后续第一个不同 Provider 类型作为唯一 fallback。
- 同一种 Provider 类型只能保留一个 Base URL / Token 端点；同类型主备会折叠并告警。
- 每个模块只保留链首 Provider 和 model；模块自己的后续 fallback 会截断并告警。
- 如果模块链首与该类型被保留的代表端点不同，只能保留模块 model；Base URL、Token 和协议参数会改用代表端点并告警。
- `[llm.embedding]` 独立配置保持不变。
- 全局链没有任何可用实例时拒绝导出，不产生目标文件。

建议的真实降级顺序：

1. 在仍运行新版本时执行导出，阅读全部兼容告警。
2. 停止 daemon；保留 v2 `config.toml` 和自动生成的
   `config.toml.pre-llm-routing.bak`。
3. 由操作者显式把 `config.legacy.toml` 放到旧版本读取的 `config.toml` 位置。
4. 启动旧版本并运行 `openbiliclaw config-show` / `health-check` 验证实际端点。

导出副本包含 API Key 等明文凭据。POSIX 下命令把权限设为 `0600`；Windows 下文件继承目标
目录 ACL，应选仅当前账户可访问的目录。自动迁移备份、表达能力边界与前后端版本搭配说明见
[配置文档](config.md#旧配置兼容与迁移)。

### `openbiliclaw health-check`

逐个检查已注册 chat 实例的连通性；输出名称是实例 ID，不是 adapter 类型。

```bash
$ openbiliclaw health-check
Provider 健康检查
  openai-official (default): 可用
  deepseek: 可用
  ollama: 不可用
    原因: connection refused
```

### `openbiliclaw auth login`

交互式或非交互式设置 B 站 Cookie。验证通过后才保存。

```bash
# 交互式
$ openbiliclaw auth login
请输入 B 站 Cookie: SESSDATA=abc; bili_jct=xyz
登录成功
  用户名: alice
  UID: 10086

# 非交互式
$ openbiliclaw auth login --cookie "SESSDATA=abc; bili_jct=xyz"
```

### `openbiliclaw auth status`

检查当前保存的 Cookie 是否有效。

```bash
$ openbiliclaw auth status
认证概览
认证信息
  状态: 已认证
  用户名: alice
  UID: 10086
```

### `openbiliclaw keyword-inspiration-dry-run`

真实跑一轮 query inspiration 关键词生成，但不写入 `discovery_keywords`。`openbiliclaw keyword-inspiration-preview` 是同一命令的等价别名。命令会临时启用 inspiration preview，读取当前 Soul 画像、`config.toml` 的 discovery LLM 路由和搜索 provider 链（默认 local cache / 已启用平台源 / Exa / You.com），输出 JSON report。平台源只做灵感 grounding，不写候选池；被抽中的二级兴趣会写入独立的 preview selection scope，用于连续 preview 验证兴趣冷却轮转，不影响正式 production 抽样：

```bash
$ openbiliclaw keyword-inspiration-dry-run --platform bilibili --platform reddit --kind regular --limit 6 --interest-limit 4
$ openbiliclaw keyword-inspiration-preview --platform bilibili --persist-axes
$ openbiliclaw keyword-inspiration-preview --platform v2ex --limit 4
```

输出包含：

- `selected_secondary_interests`：本轮从 like / accepted / profile-backed 兴趣里抽到的二级兴趣；
- `brainstorm_branches`：由轴库、二级兴趣标签和 pooled terms 确定性生成的 grounding probe query（字段名保留兼容旧 report）；
- `grounding_records`：搜索预览抽到的具体实体 / 社区词 / 证据标题；
- `grounding_ledger`：本轮 grounding 搜索次数、平台源命中分布、cooldown / risk budget / timeout，以及 Exa / You.com 等 fallback provider 的成功、失败、空结果和补充次数；
- `platform_keywords`：按平台生成并通过 quota / explore 校验后的最终搜索词；
- `materialize_telemetry`：coverage-first 装配过程中的 `deterministic_fill`、`coverage_shortfall`、硬闸拒绝和软分分布；
- `rejected_reasons`：按平台保留的硬闸拒绝明细；preview report 会继续过滤 `platform_style_mismatch`，因为平台 style 已改为软分，不再硬拒绝。

`--limit`（每平台关键词上限）和 `--interest-limit`（二级兴趣样本数）是**本次 preview 的一次性覆盖**（Phase 2 config 收敛后语义）：inspiration 的细粒度参数不再是 `config.toml` 字段，而是由 `[discovery].inspiration_breadth` 档位（默认 `medium`）派生成一个内部参数对象；不传这两个 flag 时该对象来自 `derive(breadth)`，传了则在派生对象上套一次性覆盖（`max_keywords_per_platform` / `interest_sample_size`），经 planner / pipeline 构造注入，**不写回 `config.toml`、不改四个兼容委托的签名**，用户可见行为与收敛前一致。真实画像很大时建议先用 `--interest-limit 2..4` 做 smoke，再放大窗口观察多样性。`--persist-axes` 会把本次 LLM 返回的新轴写入 / 合并到 `discovery_inspiration_axis`，但不增加 axis 使用计数，也不写关键词池；不传时 preview 只读轴库和 selection ledger。preview 永不触发 yield 回填 / 生命周期迁移（观测不改变被观测系统）。regular + explore 同轮触发时，runtime 会共用同一批 selected interests、grounding evidence 和单次 `discovery.keyword_inspiration` 输出；preview 单独预览指定 `--kind`。

### `openbiliclaw keyword-inspiration-report`

读取本地 `discovery_keywords`、`discovery_keyword_yield`、`content_cache` 和 `discovery_interest_selection_ledger`，按 `inspiration_id` 溯源把关键词分成 `inspiration` 与 `merged` 两组，输出认领率、每个被认领关键词的入池数、平均 delight、topic 多样性、production / preview 二级兴趣抽中分布和 replace 启用门禁：

```bash
$ openbiliclaw keyword-inspiration-report --window-days 14
```

报告内会同时输出本次使用的阈值。默认门禁要求：窗口至少 14 天、inspiration 组至少 200 个被认领关键词、准入率不低于 merged 的 `0.8x`、平均 delight 不低于 merged 的 `0.95x`，且 topic 多样性严格更高。未通过时不要开启 `[discovery].inspiration_replace_merged_keywords=true`，应只修改一个可测因素后继续附加模式观察。

### `openbiliclaw login codex`

管理实验性的 Codex OAuth 凭据。该命令不自建 OAuth 流程，而是复用官方 Codex CLI 的登录态：默认读取 `~/.codex/auth.json`，导入到 `~/.openbiliclaw/codex_auth.json`，供 `provider_type="openai"` 且 `auth_mode="codex_oauth"` 的实例使用。

```bash
# 默认：先尝试导入 ~/.codex/auth.json；没有时调用官方 `codex login` 后再导入
$ openbiliclaw login codex

# 只导入现有 Codex CLI 凭据
$ openbiliclaw login codex --import

# 从指定路径导入
$ openbiliclaw login codex --import --source ~/.codex/auth.json

# 查看状态；不会显示 token 明文，且会展示最近一次 LLM 能力探测结果
$ openbiliclaw login codex --status

# 查看状态并立即执行一次真实 LLM 能力探测（结果写回本地凭据文件）
$ openbiliclaw login codex --status --probe

# 删除 OpenBiliClaw 本地副本，不会删除 Codex CLI 自己的登录态
$ openbiliclaw login codex --logout
```

`login codex --import` 会在导入后自动执行一次真实 LLM 能力探测（模型取自当前
`[llm.openai].model`；留空时自动从 `chatgpt.com/backend-api/codex/models`
发现账号可用的 Codex 后端模型，发现失败则回退 `gpt-5.4`），并把结果持久化到
本地凭据文件；若令牌只能登录 Codex CLI、不能调用 LLM 传输层，CLI 会明确提示
改用 OpenAI Platform API Key，而不是等到 init 才遇到 401。

启用方式：

```toml
[llm.instances.openai-codex]
name = "OpenAI Codex OAuth"
provider_type = "openai"
enabled = true
auth_mode = "codex_oauth"
api_key = ""
base_url = ""
model = "gpt-5.4"
```

> Codex OAuth 通道要求 Codex 后端模型（如 `gpt-5.4` / `gpt-5.5` /
> `gpt-5.6-*` / `gpt-5.3-codex-spark`），Platform API 模型（如
> `gpt-5-nano`）会被该通道以 HTTP 400 拒绝。

这是非官方实验路径，OpenAI / Codex CLI 可能随时调整 token 权限或文件格式。`codex_oauth` 下 `base_url` 只能留空或指向官方 Codex 传输端点 `https://chatgpt.com/backend-api`；请求走官方 Codex CLI 同款 `backend-api/codex/responses` 通道，不会把 ChatGPT OAuth token 发给第三方代理或 `api.openai.com`。

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
浏览器集成状态
浏览器信息
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
目标地址
  URL: https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
╭─ 页面内容 ─╮
│ Example Domain ... │
╰──────────────╯
```

### `openbiliclaw start`

启动本地 API 服务。默认读取 `config.toml [api]`，新安装默认监听 `0.0.0.0:8420`，方便同局域网手机访问 `/m/`；也支持显式传入 host/port 覆盖配置。

```bash
$ openbiliclaw start

$ openbiliclaw start --host 0.0.0.0 --port 9000
```

适合本地直接运行或调试场景。若只希望本机访问，把 `[api].host` 改为 `127.0.0.1`，或启动时传 `--host 127.0.0.1`。

`start` 与 `serve-api` 都会先取得项目根和 canonical `data_dir` 的 migration runtime lock；如果存在已校验的 pending 或未完成 journal，会在任何业务数据库访问前完成应用或恢复。锁会持续到后端退出，另一个指向同一数据目录的受支持后端无法并发启动。迁移应用后会重新读取配置并补锁实际运行目录；无法取得任一锁时拒绝启动。

随后 `start` 会按固定顺序做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`；该冷备严格早于 guided-init / runtime 持久 SQLite 连接创建，避免普通文件复制干扰 POSIX WAL 锁

数据库健康后、API server 启动前，`start` 还会执行自启动相关的轻量 reconcile：

- 如果当前 LLM / embedding 配置需要本机 Ollama、`[autostart].manage_ollama=true` 且 endpoint 是默认 `localhost:11434`，会探测 `/api/version`；未运行时尝试后台执行 `ollama serve`。远端或自定义 loopback 端口只探测，不强行拉起。
- 如果 `[autostart].enabled=true` 但系统登录项缺失，会在没有环境变量管理风险时重新注册当前用户登录项；发现 `OPENBILICLAW_*` / provider API key 等环境变量覆盖时只告警并跳过，避免注册一个下次登录拿不到配置的启动项。
- 如果 `[autostart].enabled=false` 但系统登录项仍残留，会尝试移除该当前用户登录项，让手动编辑配置后的下一次启动也能回到关闭状态。
- 上述对账由 `runtime.autostart.reconcile()` 提供，冻结桌面包入口也调用同一实现；Windows 安装包不再因绕过 `openbiliclaw start` 而漏掉残留清理。

如果引导初始化从未完成（soul 层为空的 best-effort 检查，检查失败时保持沉默），`start` 会在 uvicorn 启动前打印一个 WARN 面板，给出 `/setup/` 引导地址和无浏览器环境的 `openbiliclaw init` 替代命令；`serve-api` 打印容器版变体（`/setup/` 只做配置与前置检查 + `docker exec -it openbiliclaw-backend openbiliclaw init`）。

如果 `scheduler.pause_on_extension_disconnect=true`，`start` 会在 uvicorn 启动前打印一行 WARN：

```text
WARN extension presence required; backend will pause background LLM work after grace period if no extension client connects
```

这表示 daemon-owned 后台 LLM / embedding 工作需要浏览器插件保持 `runtime-stream` 在线，或仍处于断开后的宽限窗口内；手动 CLI/API 操作不受这个 WARN 影响。

如果配置导致 LLM registry 无法构建，`start` 不会直接让 popup 完全失联，而是以降级模式启动本地 API，并在 uvicorn 启动前打印 `降级模式 / Degraded mode` 面板。面板会列出 `llm_registry_unavailable` 和 blocking issue，并提示打开扩展设置页保存修复配置；校验通过后当前进程会原地构建完整 runtime，无需重启 daemon。

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

当前 `start` 不只是提供静态接口，还会顺手启动候选池运行时：

- 监听插件上报的强信号行为
- 在阈值满足时自动刷新推荐候选
- 定时做榜单/探索补货
- 为插件 popup 和 service worker 提供 `/api/runtime-status` 与通知接口

启动后除了现有候选池刷新 loop，还会常驻一个低频账户同步 loop：
- 定期检查观看历史
- 定期检查收藏夹变化
- 定期检查关注 UP 主变化

这些账户侧长期信号会统一转成事件，再进入现有偏好/画像更新链。

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### `openbiliclaw serve-api`

启动更适合 Docker / 脚本调用的 API 服务入口。默认监听 `0.0.0.0:8420`。

```bash
$ openbiliclaw serve-api

$ openbiliclaw serve-api --host 0.0.0.0 --port 8420

$ openbiliclaw serve-api --tls-port 9443   # 覆盖 config.toml 的 [tls_proxy].port
```

`--tls-port` 覆盖 `[tls_proxy].port`（默认 8443），仅在 `enabled=true` 时生效。
TLS enabled 时，证书检查、SSL context 和 socket bind 在 uvicorn 前同步完成；任一失败会打印
原因并让 `serve-api` 非零退出，不会继续显示“HTTPS 已启动”。API wildcard host 会转换成
可连接的 loopback（`0.0.0.0 → 127.0.0.1`、`:: → ::1`）供代理连接。
推荐容器内使用该命令作为启动入口。
`serve-api` 与 `start` 共用上述 migration runtime lock、pending apply / journal recovery 和迁移后实际数据目录补锁流程；容器启动不会绕过迁移事务。它不执行 `start` 专属的 24 小时数据库冷备。
当 `scheduler.pause_on_extension_disconnect=true` 时，`serve-api` 与 `start` 一样会在 uvicorn 启动前打印 extension presence WARN，提醒容器后端若没有插件客户端连接，后台 LLM 工作会在宽限期后暂停。
当配置进入降级模式时，`serve-api` 也会打印同一张 `降级模式 / Degraded mode` 面板；容器或脚本可继续通过 `/api/config` 写入修复配置，成功响应会原地启用新 registry，不需要重启服务。

### `openbiliclaw tls-proxy`

管理默认关闭的局域网 / 自管 TLS 入口。它不是公网生产级反向代理，且只随 `serve-api`
运行。第一次启用时，应把远程客户端实际使用的 hostname/IP 作为 SAN：

```bash
# --san 可重复；推荐显式给出，避免交互输入歧义
$ openbiliclaw tls-proxy enable \
    --san 192.168.1.20 \
    --san openbiliclaw.lan

# 不传 --san 且尚无已存 SAN 时，会交互询问；直接回车只生成 localhost 证书
$ openbiliclaw tls-proxy enable

$ openbiliclaw tls-proxy status
TLS 反代配置
状态:   开启
端口:   8443
证书目录: (data/certs)
SAN:    192.168.1.20, openbiliclaw.lan

# 仅关闭下次启动，不删除/重签任何证书
$ openbiliclaw tls-proxy disable
```

`enable` / `disable` 通过 `save_config()` 持久化 `[tls_proxy]`。若开关由
`OPENBILICLAW_TLS_PROXY_ENABLED` 或 `config.local.toml` 管理，命令会以非零状态拒绝假成功，
应直接修改覆盖来源。SAN 变化不会自动覆盖旧证书；下一次启动会明确列出证书缺少的 SAN，
按 [`HTTPS 部署指南`](../https-deployment.md) 备份并重签。

### `openbiliclaw set-password`

管理局域网 / 远程访问的密码门禁（写入 `[api.auth]`，见 [配置参考](config.md#apiauth) 与 [api-auth 模块](api-auth.md)）。本机（loopback）默认始终免登录，只有手机 / 其他设备走局域网访问时才需要密码。

```bash
# 交互式设置 / 修改密码（自动开启门禁，scrypt 落盘，首次启用生成签名密钥）
$ openbiliclaw set-password
设置访问密码: ********
确认: ********
已设置局域网访问密码

# 关闭密码门禁
$ openbiliclaw set-password --disable

# 立即让所有设备登录态失效（不改密码 / 密钥）
$ openbiliclaw set-password --logout-all

# 轮换会话签名密钥（最强撤销，需重启后端生效）
$ openbiliclaw set-password --rotate-secret
```

选项：

- 无参数：交互式设置或修改密码（需交互式终端；非交互场景用 `OPENBILICLAW_API_AUTH_PASSWORD` 环境变量）。设置成功会顺带启用门禁。
- `--disable`：关闭门禁（`enabled=false`），重启后端后生效。
- `--logout-all`：自增 SQLite `auth_state` 的 `auth_epoch`，使此前签发的全部登录态（含被复制 / 嗅探走的 token）立即失效，所有设备需重新登录。
- `--rotate-secret`：轮换 `session_secret` 并撤销所有登录态；新密钥需重启后端进程才完全生效。

> 改密码（无论走本命令、`init`、直接改 TOML、env、还是 `PUT /api/config`）都会在下次启动 / 重载时按密码指纹变化自动撤销旧登录态。永不过期（`session_ttl_hours=0`，「记住登录」）的会话不会因重启被误撤销。

### `openbiliclaw ext-key`

管理跨设备浏览器扩展的设备访问密钥。配置只保存密钥摘要；完整密钥只在生成时显示一次，由用户填入目标扩展的设置页。该能力默认关闭。

```bash
# 生成密钥（完整密钥只显示一次，总开关仍关闭）
$ openbiliclaw ext-key generate
设备访问密钥已生成
  Key ID: a1b2c3d4e5f6
  obc_ext_a1b2c3d4e5f6.<secret>

# 至少有一个密钥后显式开启
$ openbiliclaw ext-key enable

# 暂停签发新短会话，保留密钥摘要
$ openbiliclaw ext-key disable

# 只查看 key ID，不打印摘要或 secret
$ openbiliclaw ext-key list

# 撤销设备；同时使全部 Web / 扩展会话立即失效
$ openbiliclaw ext-key revoke a1b2c3d4e5f6
```

子命令：

- `generate`：生成 256-bit 随机 secret，配置只写 `key_id:sha256(secret)`；不会自动开启总开关。
- `enable` / `disable`：控制 `/api/auth/extension-token` 是否签发新短会话，密钥摘要保留。
- `list`：只显示 key ID。
- `revoke <key-id>`：删除一个摘要并提升 `auth_epoch`。若运行库不可写，配置会回滚且命令失败。

所有写命令在 auth 配置受环境变量或 `config.local.toml` 覆盖时拒绝执行，避免显示成功但重启后失效。

### `openbiliclaw autostart`

管理当前用户作用域的登录自启动（macOS LaunchAgent / Windows HKCU Run / Linux XDG autostart）。该命令不写系统级服务，不需要 root / 管理员权限；Docker / 容器和未知平台会拒绝注册。

```bash
# 查看配置意图、系统注册状态和平台机制
$ openbiliclaw autostart status

# 开启：先权威写 [autostart].enabled=true，再注册 OS 登录项
$ openbiliclaw autostart enable

# 关闭：先移除 OS 登录项，再权威写 [autostart].enabled=false
$ openbiliclaw autostart disable
```

`enable` 会拒绝当前进程依赖环境变量管理的配置（例如 `OPENBILICLAW_*`、`GOOGLE_API_KEY` / `GEMINI_API_KEY`、抖音 Cookie env），因为桌面登录会话可能拿不到这些 shell 变量。请先把必要配置写入 `config.toml`。

CLI 与 API 使用同一套方向化事务规则：开启时写配置成功且未被 `config.local.toml` 覆盖后才注册 OS；关闭时先注销 OS，再写配置。任一步失败都会尽量把配置和 OS 注册恢复到操作前状态。

### `openbiliclaw delight`

手动查看当前可推送的惊喜推荐候选。

```bash
$ openbiliclaw delight
惊喜推荐
【意外契合】阿B 觉得这条你会意外喜欢
  标题: ...
  惊喜分: 0.72
  理由: ...
```

行为说明：

- 先补一次 delight backlog，再从当前池子里取一条“文案已就绪”的候选
- 运行时与 CLI 共用同一套 delight 阈值口径：默认 `0.70`
- 如果当前只有分数、还没生成 `reason/hook`，CLI 不会把它当成可展示候选

### `openbiliclaw probe`

手动列出当前最值得确认的猜测兴趣方向，并支持确认 / 否认 / 多聊聊。

```bash
$ openbiliclaw probe
猜测兴趣方向
1. 城市空间叙事
2. 复杂系统
```

### `openbiliclaw quality-gate suggest-patterns`

让 LLM 根据用户画像和近期不喜欢的内容，建议用于 `[quality_gate]` 的 Clickbait 正则模式。分析来源：近期被 dislike 的内容标题、画像中的 `disliked_topics`，以及画像兴趣关键词（避免 LLM 对用户真正感兴趣的领域误杀）。

```bash
# 只生成建议（不修改配置），LLM 输出的正则需用户确认
$ openbiliclaw quality-gate suggest-patterns

# 直接将建议写入 config.toml 的 [quality_gate].clickbait_patterns
$ openbiliclaw quality-gate suggest-patterns --apply
```

生成的建议默认只展示给用户确认；`--apply` 才会把正则写回 `config.toml`（追加到现有 `clickbait_patterns`）。QualityGate 过滤器本身在 `[quality_gate].enabled = true` 时生效，详见 `docs/modules/config.md` 的 `[quality_gate]` 一节。

### Agent JSON bridge: avoidance and current capabilities

不喜欢领域探针、兴趣探针、惊喜反馈和新一代多源推荐能力通过 Agent Bridge 暴露，而不是新增顶层 `openbiliclaw` 命令。它返回稳定 JSON，供 OpenClaw / Hermes / WorkBuddy / Codex / Claude Code 等 agent 调用。

宿主第一次启动或升级后先协商：

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli capabilities
{"ok": true, "data": {"protocol_version": "agent-bridge/v2", "host_names": ["openclaw", "hermes", "workbuddy"], "skill_names": ["..."]}}
```

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli next-avoidance-probe
{"ok": true, "data": {"probe": {"domain": "浅层热点复读", "question": "..."}}}

$ uv run python -m openbiliclaw.integrations.openclaw.cli respond-avoidance-probe \
  --domain "浅层热点复读" \
  --response confirm
{"ok": true, "data": {"ok": true, "action": "confirmed", "domain": "浅层热点复读"}}
```

`respond-avoidance-probe --response` 支持：

- `confirm`：用户确认“不喜欢 / 需要避开”，后端写入 `preference.disliked_topics`，同步 soul layer，并触发候选池清理。
- `reject`：用户否认“不排斥这个方向”，只进入 cooldown 和反馈历史，不写画像。
- `defer`：暂缓本次探针并返回 `deferred_until/defer_count`，不会直接写永久拒绝。
- `chat`：进入带 `avoidance_probe` scope 的上下文对话；明确确认或否认的聊天会转成对应反馈。

兴趣探针使用相同的四态协议：

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli respond-interest-probe \
  --domain "建筑美学" --response defer
```

`recommend` 还支持 `--source-platform`、重复传入的 `--exclude-item-id`，以及当前 UI
对应的 `reshuffle` / `append`。输出字段优先使用 `item_key/content_id/source_platform`，
同时保留 `bvid/up_name` 兼容别名。

`sync-saved` 是唯一会从 Agent bridge 触发外部账号 native-save 的入口，必须带
`--allow-state-changing`；`save-local`、`list-saved` 和 `remove-saved` 只操作本地 membership。

`listen` 默认转发 `delight.candidate`、`interest.probe` 和 `avoidance.probe`：

```bash
$ uv run python -m openbiliclaw.integrations.openclaw.cli listen
{"ok": true, "data": {"type": "avoidance.probe", "domain": "浅层热点复读", "...": "..."}}
```

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
用户画像概览
人格描述
这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人……

核心特质
  理性、谨慎、自驱

价值观
  成长、真实

当前阶段
  稳定积累阶段

深层需求
  被理解、持续成长
```

### `openbiliclaw ledger`

查看画像更新台账（`profile_update_ledger`，v0.3.174+）。每个画像写点（对话学习 / 反馈批 / 12h 整理 / init 建像 / 管线各层 / 推测确认 / 觉察同步 / 对话结算）在动作结束后追加一行，含 `outcome`（success/failed）、before/after 摘要与 `source_refs`。台账为只追加审计底座，写失败只 WARNING、不阻断画像写入；从 v0.3.174+ 开始记录，旧的画像更新不回填。

```bash
$ openbiliclaw ledger                    # 默认：近 30 天按写点聚合（成功/失败计数）
$ openbiliclaw ledger --line             # 逐行明细（时间 / 写点 / 来源 / 结果 / turn_id / source_refs）
$ openbiliclaw ledger --days 7           # 只看近 7 天
$ openbiliclaw ledger --write-point dialogue_preference_overwrite   # 只看某个写点
```

选项：`--days N`（窗口，默认 30）/ `--line`（逐行，默认按写点聚合）/ `--write-point <name>`（过滤单个写点）/ `--limit N`（逐行最多行数，默认 200）。写点清单见 `docs/modules/soul.md`。shadow 门控采数（Phase 3 上线后）可直接查 `gate_verdict LIKE 'shadow_%'`。

### `openbiliclaw questions`

只读展示对话确认入口当前最多 3 条高优先级待聊对象。命令从配置中的 `[api].port` 连接本机 `127.0.0.1`，只调用 `GET /api/chat/pending-confirmations`，因此假设/疑惑阈值、未结算过滤、排序、上限和 `count` 与 popup、桌面 Web 完全同口径，不在 CLI 复制筛选规则。

```bash
$ openbiliclaw questions
待聊确认
  猜测  你可能更看重一手证据  83%  event-7、event-9  hyp-ref
  疑惑  为什么最近跳过熟悉主题  61%  —                  42
```

输出只包含类型、话题、置信度、依据和 ref，不提供 confirm/reject/discuss/defer 动作，也不会写数据库；主动确认可在插件、移动 Web 或桌面 Web 的对话卡片中完成。运行前需先启动本地 API 服务；连接失败会显示实际 loopback URL 和启动提示。

### `openbiliclaw profile-consolidate`

用 LLM 整理合并画像里重复的喜欢 / 讨厌主题。兴趣标签和避雷主题会不断积累措辞变体（「智能体开发」vs「智能体开发与实现」），把进入内容 prompt 的 top-48 名额挤占掉；本命令按「规则合并 → embedding 聚类 → LLM 裁决 → 校验执行 → active 库存归档」流水线做同义合并，默认整理 likes 权重 top-512 + 全量避雷主题。后台默认每 12 小时自动跑一轮（见 `[scheduler].profile_consolidation_*`），本命令用于手动触发与预览。

```bash
$ openbiliclaw profile-consolidate            # dry-run：只打印建议
$ openbiliclaw profile-consolidate --apply    # 写入；自动备份 + soul_changelog.md 审计
$ openbiliclaw profile-consolidate --migrate-categories          # dry-run：预览分类 → 词表映射
$ openbiliclaw profile-consolidate --migrate-categories --apply  # 写入分类迁移；自动备份
$ openbiliclaw profile-consolidate --full           # dry-run：likes 边界开到全量标签库
$ openbiliclaw profile-consolidate --full --apply   # 写入全量二级清理；单 run 可整体回滚
$ openbiliclaw profile-consolidate --revert 20260612-031500   # 按 run_id 回滚
```

要点：

- LLM 只能输出 merge / keep 操作，代码侧校验（members 逐字存在、簇内全覆盖、canonical 禁裸大词）后才执行；任何校验不过整簇放弃
- `--migrate-categories` 是一次性运维入口：LLM 只产出现存分类到 `CATEGORY_VOCAB` 的映射，代码侧强制完整覆盖、目标在词表内、词表内分类恒等；默认 dry-run，`--apply` 后可用同一个 `--revert <run_id>` 回滚
- `--full` 把 likes 整理边界从默认 top-512 开到全量标签库；嫌疑簇按最多 32 个/批送审，所有成功批次汇入一个 run 记录，可整体 `--revert`
- `--full` 与 `--migrate-categories` 互斥；推荐先 `--migrate-categories --apply`，再 `--full --apply`
- active likes 超过 `profile_consolidation_like_target_upper` 时，定时整理会自动临时开 full boundary，并按 `upper -> soft` 水位压力降低 likes embedding 聚类阈值（CLI 输出 `likes 动态聚类阈值`）；合并后仍超上限时，会把低权重且非用户保护的长尾兴趣归档到 `archived_interests`
- dry-run 会显示预计归档数量和库存说明；`--apply` 写入后 run record 可同时回滚 active / archived inventory
- `--apply` 在 LLM 裁决后会重新核对完整 preference revision；若同期偏好分析写入了新证据，本轮放弃全部旧快照结果、不写 run/state，并由下一 tick 重试
- 避雷主题只合真同义、严禁向上泛化（canonical 不得比成员更宽泛）
- 用户在画像编辑里手动 remove/add 的条目会随改名同步（rename map 穿透 overrides），不会被合并「借尸还魂」
- 回滚会把被回滚的合并对记入 no-merge 记忆，下一轮定时整理不会重做同一合并

### `openbiliclaw init`

首次运行编排命令；已初始化后加 `--force` 即「重新初始化」（重新拉取所选平台数据、重建完整画像并补足首轮发现池，**现有事件、收藏与对话历史保留**；交互终端默认在检测到已初始化时先 y/N 二次确认，`--force` 跳过确认并把标题改为「重新初始化 OpenBiliClaw」，非交互终端保持直接重跑）。会顺序执行：

1. 检查运行时 LLM 配置
2. 检查 B 站认证（仅当包含 B 站来源时）
3. 拉取 B 站历史 / 收藏 / 关注（仅当包含 B 站来源时）
4. best-effort 等待插件导入小红书初始化信号
5. best-effort 等待插件导入抖音初始化信号
6. best-effort 等待插件导入 YouTube 初始化信号
7. best-effort 等待插件导入知乎初始化信号
8. best-effort 等待插件导入 Reddit 初始化信号
9. best-effort 等待插件导入 Linux.do 初始化信号
10. 若提供公开用户名，读取 Bangumi 公开收藏初始化信号
11. 写入事件层并分析偏好
12. 生成、校验并保存完整初始画像
13. 严格使用该画像执行发现、个性化评估和推荐文案生成，至少验证一条 canonical 推荐可直接浏览

> v0.3.118+：B 站不再是必选基座——`--no-bilibili`（或 `OPENBILICLAW_NO_BILIBILI=1`）可跳过 B 站，
> 但 init **至少需要一个数据来源**：全部来源都关闭时命令直接报错退出（exit 1）。
> 所有所选来源都没拉到任何信号时，流水线以 `empty_signals` 失败。

> v0.3.102+：来源采集步骤的核心抽成共享异步流水线 `cli.run_guided_init`，CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动（交互提示 / 摘要仍在命令里），后端图形化初始化 `POST /api/init` 复用同一协程。CLI 行为 / 输出 / 退出码不变。**也可以不进终端**：插件「推荐」tab 未初始化时直接点「开始初始化」，详见 [init 模块文档](init.md) 与 [extension 模块文档](extension.md)。

> Issue #113（v0.3.168+）：共享流水线仅在阶段 2 偏好分析和阶段 3 画像任务的 task-local scope 内绕过库存敏感的后台 admission，避免首次空库存与画像生成互相等待；阶段 4 只在完整画像落盘后开始且不继承 bypass，并同步完成发现、评估、推荐文案与 canonical 可用性校验。正向兴趣 / 避雷探针移到 init wrapper 恢复 runtime 后调度，普通后台任务、LLM 总并发 gate 及 Soul 公开 API 不变。

> 阶段 2 的 ETA 心跳会附带实时分片进度 `已完成 X/N 批` 和实际 LLM 并发上限，超过原始预估后明确显示“已超预估、仍在处理”，不再长期显示“预计还需 ~0s”。分片完成行在 CLI 与 API 初始化路径都会写到 stdout，便于桌面端 `desktop.log` 直接定位进度；更细的分片起止、耗时、限流重试和取消记录写入 `openbiliclaw.log`。
>
> **进度感知期限（v0.3.179+）**：阶段 2 不再使用开跑前算死的固定墙钟——墙钟分不清“卡死”和“慢但在出结果”，健康但慢的服务商会在出结果的途中被杀。`_await_with_progress_deadline` 改为同时施加两个限制：**空闲期限**（`_INIT_PROGRESS_IDLE_SECONDS = 600s`，只有真实分片完成回调会刷新“上次进展”时间戳，心跳 tick 不算）与**绝对上限**（`_INIT_PROGRESS_ABSOLUTE_SECONDS = 2700s`，与按并发波次自适应算出的旧预算取较大值）。两种超时抛出可区分的 `_InitIdleTimeoutError` / `_InitAbsoluteTimeoutError`（均为 `TimeoutError` 子类），对应两条不同的可操作文案：空闲超时指向 Base URL / 模型名 / 代理排查，绝对超时提示换更快的模型并附上已完成批次。显式传入 `profile_analysis_timeout_seconds` 时仍是精确的纯墙钟（`<=0` 表示不限）。阶段 4 有真实进度信号，同样启用双限制（空闲 900s / 绝对 2700s）；阶段 3 是单次 LLM 调用、无分片进度可读，空闲判定天然失真，因此**只有绝对兜底**（1800s）。所有上限均为**卡死兜底、不是性能预期**；`CancelledError` 一律向上传播，被中断的初始化仍记为 `cancelled`。GUI 的 `stages[].progress.max_seconds` 发布的就是这个绝对上限。

安装渠道里的首选路径是 `scripts/agent_bootstrap.py` 自动运行 init：Bash / PowerShell 人类一行安装会先在终端向导里按顺序确认 LLM、embedding、B 站 Cookie 和各来源 opt-in；Docker / AI agent / CI 非交互安装则通过显式 flags 和 `BOOTSTRAP_STATUS` 推进，不会阻塞读 stdin。bootstrap 随后会对默认 LLM provider 与 embedding 服务各做一次轻量真实调用；两者都可用才触发本命令。若 bootstrap 返回 `service_check_failed`，说明 `openbiliclaw init` 尚未运行，应先修 API key / base_url / model / Ollama，再重跑 bootstrap。直接执行 `openbiliclaw init` 仍保留为高级手动 fallback 和重复初始化入口。

默认初始化信号上限：B 站观看历史最多 500 条、收藏最多 500 条（跨收藏夹总预算，单个收藏夹会按页补齐）、关注 UP 最多 100 人；小红书 / 抖音 / YouTube 的 `bootstrap_profile` 每个 scope 默认最多 300 条；知乎 `bootstrap_events` 的浏览历史、收藏夹条目、动态点赞、动态收藏四个分支默认各最多 300 条；Reddit `bootstrap_events` 的 saved、upvoted、subscribed 三个分支默认各最多 300 条；Linux.do `bootstrap_events` 的书签、点赞、阅读记录三个分支默认各最多 300 条；Bangumi 公开收藏使用 `[sources.bangumi].bootstrap_limit`，默认 300、最大 1000。交互式 `init` 会让用户确认 B 站收藏 / 关注上限，收藏回车使用 500、关注回车使用 100；脚本化场景可传 `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`，传 `0` 表示跳过对应信号。

v0.3.95+：交互式 `init` 的 embedding 配置阶段（`_interactive_embedding_setup(auto_if_ready=True)`）会先探测本机 Ollama——若 Ollama 已运行且装有 `bge-m3`，直接写入 `provider=ollama, model=bge-m3` 并跳过选项菜单，避免「确认用 Ollama 当聊天模型、却把语义去重所需的 embedding 留空」导致推荐刷到换皮重复。显式 `setup-embedding` 命令不走自动跳过，始终展示完整菜单以便切换 provider。

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取数据
  浏览历史 500 条 / 收藏 128 个 / 关注 43 人
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
  知乎 浏览 80 条 / 收藏 42 条 / 点赞 16 条
2/4 分析偏好
3/4 生成并保存完整画像
4/4 建立首轮可用内容池
  首轮内容池：完整画像已就绪，准备发现候选内容（0/4）
补货阶段 1/3: search + related_chain
当前池子 0/15，本轮请求上限 15
阶段完成: 当前池子 8/15，本轮发现 8 条
补货阶段 2/3: trending
当前池子 8/15，本轮请求上限 7
阶段完成: 当前池子 15/15，本轮发现 7 条
  首轮内容池：已生成推荐文案，正在验证首轮内容可用性（3/4）
  首轮内容池：首轮内容池已就绪（15 条可直接浏览）（4/4）
初始化完成
初始化摘要
  B 站观看历史: 500 条
  小红书 入库事件: 40 条
  抖音 入库事件: 50 条
  YouTube 入库事件: 72 条
  知乎 入库事件: 138 条
  画像建模总事件: 590 条
  灵魂画像: 已生成
  首轮发现内容: 94 条
  本次画像综合了 428 条 B 站信号 + 40 条小红书信号 + 50 条抖音信号 + 72 条 YouTube 信号 + 138 条知乎信号。
```

小红书导入依赖浏览器插件在用户已登录的小红书网页里执行 `bootstrap_profile` 任务。后端只入队任务并短暂等待结果，不直接登录或爬取小红书。插件会先定位当前用户 profile，再读取 profile state 里的收藏 / 赞过分组；这里的“浏览记录”指小红书网页自己明确暴露的浏览记录/足迹 state，不是读取 Chrome 浏览器历史，也不会把普通推荐流当成浏览记录。如果后端任务显式设置 `max_scroll_rounds`，插件会按任务 payload 中的 `scroll_wait_ms` 和 `max_stagnant_scroll_rounds` 做有限滚动和停滞判断。如果插件未连接、未登录或页面没有暴露对应 scope，`init` 会继续使用已有 B 站数据完成初始化。

抖音导入同样依赖浏览器插件在用户已登录的 `https://www.douyin.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `dy_tasks`，插件依次访问 `dy_post / dy_collect / dy_like / dy_follow` 四个 scope，content script 结合 DOM、MAIN-world fetch tap 和 API harvester 采集发布 / 收藏 / 点赞 / 关注条目，以 `partial` 批次回写 `/api/sources/dy/task-result`。bootstrap 所需的当前账号 `sec_uid` 只接受同源只读 `/aweme/v1/web/user/profile/self/` 的正面确认或同一 tab 已确认缓存；页面 `#RENDER_DATA` 只有显式 `isLogin=true` 时才作未确认候选，避免把登出残留当成当前账号。常驻 fetch / XHR tap 不从被动请求 URL 提取或记录 `sec_user_id`，因此浏览推荐流或他人主页不会把作者 ID 送入后端诊断。后端会转换为统一事件：发布 → `view`，收藏 → `favorite`，点赞 → `like`，关注 → `follow`，并带 `metadata.source_platform="douyin"`。`init --yes-douyin` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；插件未连接、未登录或抖音风控返回空数据时，初始化继续使用已有信号完成。如果缺少权威 `sec_uid`、API 返回失败业务状态，或分页游标缺失 / 非法 / 停滞 / 成环 / 触顶，已采集的 `partial` 仍会保留，但任务终态会明确标记为 `degraded`。CLI 阶段提示、最终摘要和 API init 终态都会显示“部分完成”及 `dy_status=degraded`，已采事件仍参与画像建模；终态后的迟到回调不会覆盖该状态。后台会复用 6 小时内近期正常 / 在途抖音 bootstrap 任务，但不会复用已 `degraded` 的 completed 结果，下一次会重新入队补齐分页；`source_bootstrap_state.json` 继续跳过跨任务旧视频 / 关注 identity key。

YouTube 导入依赖浏览器插件在用户已登录的 `https://www.youtube.com` 页面里执行 `bootstrap_profile` 任务。后端入队 `yt_tasks`，插件依次访问 `/feed/history`、`/feed/channels`、`/playlist?list=LL` 三个 scope，读取观看历史、订阅频道和点赞视频，以 `partial` 批次回写 `/api/sources/yt/task-result`。后端会转换为统一事件：观看历史 → `view`，订阅 → `follow`，点赞 → `like`，并带 `metadata.source_platform="youtube"`。`init --yes-youtube` 会把这些事件加入 `analyze_events()` 和 `build_initial_profile()`；非交互式终端默认跳过，`OPENBILICLAW_NO_YOUTUBE=1` 会压过 `--yes-youtube`，避免脚本环境误触发浏览器前台 tab。后台会复用 6 小时内近期 YouTube bootstrap 任务，并用 `source_bootstrap_state.json` 跳过跨任务旧条目。

知乎导入复用 `bootstrap_events` 任务。后端入队 `zhihu_tasks(type="bootstrap_events")`，插件在用户已登录的 `https://www.zhihu.com` 页面里读取最近浏览、收藏夹条目、个人动态点赞和个人动态收藏，以任务结果回写 `/api/sources/zhihu/task-result`。`init --yes-zhihu` 会把同一批任务结果转换为统一事件并加入 `analyze_events()` / `build_initial_profile()`，同时把 `[sources.zhihu].enabled=true` 写回配置。`fetch-zhihu` 默认仍只打印 smoke 计数；需要把本次抓取写入 memory 可显式加 `--write-memory`，需要写入后立即重建画像可加 `--rebuild-profile`。非交互式终端默认跳过知乎，`OPENBILICLAW_NO_ZHIHU=1` 会压过 `--yes-zhihu`。后台会复用 6 小时内近期知乎 `bootstrap_events` 任务；动态点赞和动态收藏各自独立使用单分支上限，不共享额度。

Reddit 导入也复用 `bootstrap_events` 任务。后端入队 `reddit_tasks(type="bootstrap_events")`，插件在用户已登录的 `https://www.reddit.com` 页面里先读取 `/api/me.json` 识别当前用户，再读取 saved、upvoted 和 subscribed subreddit，同步回写 `/api/sources/reddit/task-result`。`init --yes-reddit` 会把 saved → `favorite`、upvoted → `like`、subscribed → `follow` 的统一事件加入 `analyze_events()` / `build_initial_profile()`，同时把 `[sources.reddit].enabled=true` 写回配置；Reddit 可以作为唯一初始化来源，只要真实拉到至少一条信号。后台会复用 6 小时内近期 Reddit `bootstrap_events` 任务；三个分支各自独立使用单分支上限 300。

Linux.do 导入复用 `bootstrap_events` 任务。后端入队 `linuxdo_tasks(type="bootstrap_events")`，插件在真实 `https://linux.do` 任务 tab 内先用只读 `GET /session/current.json` 正面确认当前账号，再读取书签、点赞和阅读记录。三类信号分别映射为 `favorite`、`like`、`view`，并加入本轮画像。所有上游请求都是同源 GET；`_t` Cookie 只用于“是否可能登录”的布尔判断，其值、其他 Cookie、CSRF 数据和原始响应都不会上传。未登录只影响个人 bootstrap，公开 search / hot / feed / creator / related discovery 仍可使用。

上述六个扩展账号来源共享 enqueue 核心并供 runtime 使用，但周期自动入队默认由 `scheduler.source_incremental_enabled=false` 全局关闭。只有显式开启后，完整画像已存在、引导初始化不在运行且扩展 runtime-stream 在线时，runtime 才按默认 24 小时的全局串行 round-robin 重新入队一个已启用来源；这不是无浏览器账号同步。该开关不影响这些显式 CLI 命令，完整配置见 [配置模块](config.md)。
V2EX 导入使用独立的 `v2ex_tasks(type="bootstrap_profile")`。`init --yes-v2ex` 会入队 `public_topics`、`public_replies`、`favorite_topics`、`favorite_nodes` 四个只读 scope；扩展在 V2EX 页面读取渲染后的公开行，后端按 Topic 聚合回复并转换为 `publish`、`discussion_reply`、`favorite`、`follow` 事件，同时更新账号隔离的 Node affinity。未登录时公开 scope 仍可尝试，收藏 scope 会返回 `login_required`；任务的 `partial` 结果会保留已经成功的 scope。PAT 实测身份、浏览器观察身份和用户接受身份不一致时返回 `identity_mismatch`，暂停本轮账号画像投影但不影响公开 discovery。浏览器心跳只发送登录布尔值，不上传 Cookie、HTML、私信或 CSRF。

上述六个扩展账号来源在 CLI 行为保持不变；共享 enqueue 核心同时供 runtime 使用，但周期自动入队默认由 `scheduler.source_incremental_enabled=false` 全局关闭。只有显式开启后，完整画像已存在、引导初始化不在运行且扩展 runtime-stream 在线时，runtime 才按默认 24 小时的全局串行 round-robin 重新入队一个已启用来源；这不是无浏览器账号同步。V2EX 只有完整收藏快照才推进缺失计数，连续两次完整快照确认后才通过持久 outbox 生成 `retraction`；重新出现会生成恢复事件。真实登录浏览器 E2E 状态见 [V2EX 模块](v2ex.md)。周期与逐源覆盖项见 [配置模块](config.md)。

Bangumi 初始化不依赖插件或登录态。`init --yes-bangumi --bangumi-username <name>` 会通过官方匿名 API 读取该用户名的公开收藏，转换为统一事件后纳入 `analyze_events()` / `build_initial_profile()`，并写回 `[sources.bangumi].enabled=true` 与用户名。`init --yes-bangumi --bangumi-token <token>` 走个人令牌通道：令牌先经 `GET /v0/me` 实测校验（被拒绝时当场退出并指引到 https://next.bgm.tv/demo/access-token 重新生成），解析出的用户名覆盖显式填写值（不一致时提示），随后带 Bearer 读取该账号收藏（含私密行）；校验通过的令牌与用户名一并写回 `[sources.bangumi]`。令牌与显式用户名皆缺时，init 会回退浏览器扩展自动识别的账号（扩展在已登录的 bgm.tv 页面读取公开 `CHOBITS_UID` + 导航 `/user/<username>` 链接并上报后端持久化；优先级：令牌 `/v0/me` > 显式用户名 > 扩展上报用户名），命中时输出"使用浏览器扩展识别到的账号"。Bangumi 作为唯一来源时三者至少满足一个（报错文案："提供 --bangumi-token（推荐，自动识别当前用户）或 --bangumi-username（公开用户名），或先在浏览器登录 bgm.tv 让扩展自动识别"）；与其它画像来源混用但全部缺失时，初始化会明确警告并只启用后续 Bangumi discovery。匿名路径私有收藏不可见，`updated_at` 不作为收藏行为时间。

微博现在是可选的 capability-specific 初始化来源：`init --yes-weibo` / `--no-weibo` 只控制本轮是否请求微博登录态 bootstrap，不接受 Cookie 或 UID 参数。选择微博后，扩展必须在已登录微博的同源任务页确认当前 uid，再读取收藏、关注和 mentions；后端只接收规范化事件，账号切换会被拒绝。公开热搜 / 搜索结果仍只进入 discovery，不会冒充用户行为。未连接已登录扩展时，微博单独作为画像来源会在预约前返回 `no_profile_signal_sources`；与其它来源混用则降级并保留公开 discovery。

X (Twitter) 与其它平台不同：init 阶段**没有 bootstrap 导入任务**。X 的发现走服务端 cookie 重放，行为采集走浏览器扩展 MAIN-world tap，两者都在 init 之后才生效，所以 `init --yes-x` **只翻转 `[sources.twitter].enabled = true`**，不会在 init 期间打开 x.com 前台 tab 或拉取数据。启用后，用户登录 x.com → 扩展自动把 `auth_token` + `ct0` 同步到 `data/x_cookie.json` → 后台 `XDiscoveryProducer` 在下一个 refresh tick 按预算补 X 候选。非交互式终端默认跳过 X。

源开关：

- `--no-bilibili`：跳过 B 站数据接入（v0.3.118+，默认包含；至少需保留一个数据来源）。同时把 `[sources.bilibili].enabled` 持久化为 `false`，后台发现也不再跑 B 站。
- `--yes-xhs` / `--no-xhs`：跳过小红书交互式提问，直接启用或跳过。
- `--yes-douyin` / `--no-douyin`：跳过抖音交互式提问，直接启用或跳过。交互式提问默认 No；非交互式终端默认跳过抖音，脚本化 init 应显式传其中一个。
- `--yes-youtube` / `--no-youtube`：跳过 YouTube 交互式提问，直接启用或跳过。交互式提问默认 No；非交互式终端默认跳过 YouTube，脚本化 init 应显式传其中一个。
- `--yes-x` / `--no-x`：跳过 X (Twitter) 交互式提问，直接启用或跳过。只翻转 `[sources.twitter].enabled`，不在 init 期间拉取数据；非交互式终端默认跳过 X，脚本化 init 应显式传其中一个。
- `--yes-zhihu` / `--no-zhihu`：跳过知乎交互式提问，直接启用或跳过。`--yes-zhihu` 会执行 `bootstrap_events` 并把结果纳入本轮首版画像；非交互式终端默认跳过知乎，脚本化 init 应显式传其中一个。
- `--yes-reddit` / `--no-reddit`：跳过 Reddit 交互式提问，直接启用或跳过。`--yes-reddit` 会执行 `bootstrap_events` 并把 saved / upvoted / subscribed 结果纳入本轮首版画像，同时开启后续 Reddit discovery；非交互式终端默认跳过 Reddit。
- `--yes-linuxdo` / `--no-linuxdo`：跳过 Linux.do 交互式提问，直接启用或跳过。`--yes-linuxdo` 会执行 `bootstrap_events` 并把书签 / 点赞 / 阅读记录纳入本轮首版画像，同时开启后续 Linux.do discovery；非交互式终端默认跳过 Linux.do。
- `--yes-bangumi` / `--no-bangumi`：跳过 Bangumi 交互式提问，直接启用或跳过；非交互式终端默认跳过 Bangumi。
- `--bangumi-username <name>`：本次初始化读取的公开用户名，并在启用时写回配置；不提供时可回退 `[sources.bangumi].username`。
- `--bangumi-token <token>`：Bangumi 个人令牌（推荐，自动识别当前用户并可读私密收藏）；不提供时可回退 `[sources.bangumi].access_token`。经 `/v0/me` 校验通过后写回配置；坏令牌当场拒绝。
- `--bilibili-favorite-limit N` / `--bilibili-follow-limit N`：覆盖 B 站收藏 / 关注初始化信号上限，默认各 `300`；`0` 表示跳过对应信号。
- `--force`：已初始化时仍强制重新初始化。默认已初始化时，交互终端会先二次确认（`检测到系统已初始化` + y/N，默认 No，选 No 直接退出、不做任何改动）；`--force` 跳过确认，并按「重新初始化」语义执行——重新拉取所选平台数据、重建完整画像并补足首轮发现池，**现有事件、收藏与对话历史全部保留**，仅覆盖画像与推荐池（**旧推荐池会被清空并按新画像重建**）。非交互（脚本化）终端不弹确认，保持原有「直接重跑」行为（不传 `--force` 时也不清池）；只想基于已有事件重跑画像可优先用 `rebuild-profile`（不重新拉数据，更省）。
- `--reset-cognition`：重新初始化时同时清空长期 awareness / insight 认知层（换账号或大改兴趣时建议），仅配合 `--force` 有意义；不清空时旧 LLM 观察继续作为新画像的构建上下文。
- `--no-backup`：force 重初始化前跳过自动备份。默认 `init --force` 会先把数据库与 `data/memory/` 全部画像/认知层快照到 `data/backups/reinit-<时间戳>/`，再开始重建（覆盖的画像、`--reset-cognition` 删除的认知层因此可恢复）。
- `OPENBILICLAW_NO_BILIBILI=1` / `OPENBILICLAW_NO_XHS=1` / `OPENBILICLAW_NO_DOUYIN=1` / `OPENBILICLAW_NO_YOUTUBE=1` / `OPENBILICLAW_NO_X=1` / `OPENBILICLAW_NO_ZHIHU=1` / `OPENBILICLAW_NO_REDDIT=1` / `OPENBILICLAW_NO_LINUXDO=1` / `OPENBILICLAW_NO_BANGUMI=1`：永久跳过对应源；作为持久禁用开关，它优先于同一来源的 `--yes-*`。
- `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS`：小红书 `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS` / `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS`：抖音 / YouTube `bootstrap_profile` 近期任务复用窗口，默认 `6` 小时；抖音已 `degraded` 的 completed 结果不参与复用；设为 `0` 可关闭复用。
- `OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS`：知乎 `bootstrap_events` 近期任务复用窗口，默认 `6` 小时；设为 `0` 可关闭复用。
- `OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS` / `OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS`：控制 `fetch-zhihu` 每个数据分支最多读取的条目数和最多扫描收藏夹数，默认分别为 `300` / `20`。知乎当前分支是浏览历史、收藏夹条目、动态点赞、动态收藏；动态点赞和动态收藏各自独立使用 300 条上限，不共享额度。

如果当前终端是交互式，且缺少 provider API Key 或 B 站 Cookie，`init` 会直接进入用户友好的引导（v0.3.5+）：

```bash
$ docker exec -it openbiliclaw-backend openbiliclaw init
初始化前配置引导 · 选 LLM、配 Embedding、填 B 站 Cookie

OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。
请选一个 LLM 服务：

 #   名称                                  说明
 1   DeepSeek 官方 ★默认推荐                默认 deepseek-v4-flash (V4)。¥0.001/千 token 几乎免费,国内可直连
 2   ★ 第二推荐 — 中转站 / OpenAI 协议兼容服务 买了中转站 Key 选这个。也覆盖 Kimi / 通义 / 智谱 / Yi / MiniMax 官方 / Azure / vLLM
 3   OpenAI 官方                           默认 gpt-5-nano (最便宜的 GPT-5)。api.openai.com,需要 sk- 开头的 Key
 4   Gemini 官方                           默认 gemini-2.5-flash (稳定 / 便宜)。Google AI Studio 申请 Key,免费档每天 1500 次够用
 5   Claude 官方                           默认 claude-sonnet-4-6。Anthropic console,按 token 付费,质量高
 6   OpenRouter 聚合                       默认 openai/gpt-5-nano。一个 Key 跑多家模型,按调用计费
 7   OrcaRouter 聚合                       默认 openai/gpt-4o。一个 Key 跑 150+ 模型,网关级零信任安全

Tip:不确定就选 1 (DeepSeek),¥0.001/千 token 几乎免费,月度通常 ¥0.5-2。已经买了中转站 / OneAPI Key 选 2 (协议兼容)。本地 Ollama 仅用于向量检索(embedding),不作为聊天服务商;如需本地聊天模型请到设置页手动配置。

请输入序号或名称（默认 1=DeepSeek） [1]:

# (随后只问被选中那一项实际需要的字段——
#  例如选 1/3/4/5/6/7: 只问 API Key + 模型名；
#  选 2: 进协议兼容 preset 子菜单，按需问 Base URL + API Key + 模型名)
#
# 注意（v0.3.176+）：本地 Ollama 已不再出现在聊天 provider 菜单里——随装的
# Ollama 只带 embedding 模型（bge-m3），小体积本地聊天模型达不到内容管线质量线。
# 后端注册表 / 桌面设置页仍支持 ollama 聊天；既有配置或显式 flag 选择
# `ollama` 依然被接受，只是不再交互式「提供」它。

Embedding(向量化)服务
把视频标题/简介压成向量,跨视频做相似度对比 —— 决定"这条和你之前喜欢的那条是不是同一类"。和聊天 LLM 是分开的。

 #   方案                                  说明
 1   本地 Ollama bge-m3 ★默认推荐           免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)
 2   云端 Gemini embedding                 质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key
 3   暂不启用 embedding                    保留独立配置为空;不会跟随主 LLM,也不会自动 fallback
 4   (高级)自定义 OpenAI 兼容服务           vLLM / OneAPI / 自建网关 —— 自填 base_url
 5   (高级)指定其他 provider               手动选 provider + 模型 + 可选 base_url
 0   跳过(不修改当前 embedding 配置)

Tip:不确定就选 1。日常推荐质量已经够用且不消耗主 LLM 配额。想再准一点选 2(Gemini),需要去 https://aistudio.google.com/apikey 拿 Key。
请选择 embedding 方案 [1]:

最后是分模块实例链（高级，默认继承全局链）
（高级，可跳过）是否为单个模块单独指定 provider/model？[y/N]:

初始化前认证引导 · 补齐 B 站认证
为什么需要 B 站 Cookie？
OpenBiliClaw 需要你的 B 站登录态来：
  • 拉你的观看历史（用来训练画像）
  • 以你的身份调 B 站 API 拿视频详情
Cookie 只存在你本机 data/bilibili_cookie.json，不会上传任何地方。

怎么获取：
  1. 用 Chrome / Edge / Firefox 登录 https://www.bilibili.com
  2. 按 F12 打开开发者工具 → 切到 Network（网络）标签
  3. 刷新一下 B 站页面 → 在请求列表点任意一条 bilibili.com 的请求
  4. 右侧 Headers（请求头）区域，找到 cookie: 这一行，右键复制整行的 value
  5. 把那一长串（包含 SESSDATA=...; bili_jct=...; DedeUserID=... 等）粘进来

请粘贴 B 站 Cookie:
```

引导完成后会继续当前初始化流程，不需要再单独执行 `auth login` 或手动改配置。

交互式 `init` 在询问「是否允许局域网设备访问」之后，**仅当启用了局域网访问时**会追加一次「是否为局域网访问设置密码」（默认 `No`）。选 `Yes` 即走与 `set-password` 相同的交互设置流程，写入 `[api.auth]`；选 `No` 可随后再用 `openbiliclaw set-password` 设置。

> **「OpenAI 官方」≠「OpenAI 协议兼容服务」**：向导把这俩拆成独立菜单项。选 3 时只问 API Key，base_url 走 `https://api.openai.com/v1`；选 2 时进入协议兼容 preset 子菜单（中转站 / Kimi / MiniMax / 通义 / 智谱 / Yi / Azure / vLLM / 自定义）。向导会复用同类型且配置一致的现有实例，否则创建新的 `[llm.instances.<id>]`，并把它提升到 `default_chain` 首位；不会删除用户已经配置的其他渠道。
>
> 当 `agent_bootstrap` 通过 `--provider` 或 `--llm-preset` 选择非 DeepSeek provider 时，会自动停用样例中无 API Key 的 DeepSeek 实例并将其从 `default_chain` 移除，避免 `init` 因未配置的默认实例失败；已填写 API Key 的 DeepSeek 备选实例会保留。
> **DeepSeek 排第一**是有意为之：它是当前最低摩擦路径，国内可直连且费用接近忽略不计。
>
> **本地 Ollama 不再作为聊天 provider 出现在菜单里（v0.3.176+）**：随装的 Ollama 定位是 embedding（bge-m3），聊天模型需自行 `ollama pull` 且小模型跑内容管线质量不达标。后端注册表与桌面设置页仍支持 Ollama chat 实例，供进阶用户使用；旧 `default_provider = "ollama"` 或显式 flag 也仍被接受，交互式向导只是不再主动提供它。同一口径也适用于 `scripts/agent_bootstrap.py` 的人类安装菜单。

Provider 选择只决定本次创建或提升哪个全局实例，不会把 `default_chain` 压缩成单项。高级的模块 `provider/model` 问答会写成 `inherit=false` 的模块实例链；若模型不同于现有实例，会创建一个派生实例来保留覆盖，而不会修改共享实例。

首次 `init` 的 discover 阶段可能持续几分钟，因为它会真实访问 B 站接口并调用当前 provider 进行候选打分与表达生成。
当前实现已经对首轮 discover 做了保守受控并发优化，但默认并发上限仍偏保守，优先减少 B 站和 LLM 限流风险。
首轮补货会按 `search + related_chain`、`trending`、`explore` 的顺序推进，并尽量把 fresh 候选池补到至少 `100` 条后再结束。
运行时后台则会继续以 `scheduler.pool_target_count` 为目标持续补货；当前默认目标是 `300`，到达后停止 discover，直到候选池掉回目标以下再继续补货。
运行中会直接打印每一阶段的策略名、当前池子进度和该轮请求上限，便于你判断首轮补货是在持续推进还是确实失败。

如果当前终端不是交互式，`init` 不会等待输入，而是直接报出明确错误；这适合服务器脚本和 CI 场景。

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw setup-embedding`

重新进入 embedding 选择向导。`init` 阶段会自动问；只有当时跳过、或要切换方案时才需要主动跑：

```bash
$ openbiliclaw setup-embedding
配置本地 embedding · Ollama + bge-m3

 #   方案                                  说明
 1   本地 Ollama bge-m3 ★默认推荐           免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)
 2   云端 Gemini embedding                 质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key
 3   暂不启用 embedding                    保留独立配置为空;不会跟随主 LLM,也不会自动 fallback
 4   (高级)自定义 OpenAI 兼容服务           vLLM / OneAPI / 自建网关 —— 自填 base_url
 5   (高级)指定其他 provider               手动选 provider + 模型 + 可选 base_url
 0   跳过(不修改当前 embedding 配置)
请选择 embedding 方案 [1]:
```

每个选项对应的写入路径：

| 选项 | 行为 | 写入字段 |
|---|---|---|
| 1 | 本地 Ollama，自动探测 + 拉取 `bge-m3` | `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://localhost:11434/v1"` |
| 2 | 云端 Gemini embedding，可复用已有 Gemini Key | `[llm.embedding] provider="gemini" model="gemini-embedding-001" api_key="..."` |
| 3 | 暂不启用 embedding | `[llm.embedding] provider="" model=""`；运行时不会跟随主 LLM |
| 4 | 自填 base_url + api_key + model | `[llm.embedding] provider="openai" model="..." base_url="..." api_key="..."` |
| 5 | 选另一个已知 provider 走 embedding | `[llm.embedding] provider="<target>" model="..." base_url="..." api_key="..."` |
| 0 | 跳过 | 不主动写入新 embedding 配置 |

选项 1 时向导会按顺序：

1. 探测 `localhost:11434/api/version`，确认 Ollama 服务在跑
2. 通过 `/api/tags` 检查 `bge-m3` 是否已 pull
3. 没拉就流式 `POST /api/pull`，进度直接打到终端
4. 把 `[llm.embedding] provider="ollama" model="bge-m3" base_url="http://localhost:11434/v1"` 写入 `config.toml`

适合：

- embedding API Key 用完了
- 离线 / 没外网
- 不想再额外申请一份 embedding 服务密钥
- 跨平台一致体验（Mac/Win/Linux 同一 HTTP API）

CPU 即可跑，单次 embedding 约 100-200ms，配合后台 prewarmer 实际"换一批" 仍能稳在 600ms。

如果 Ollama 没安装：

```bash
检测不到 Ollama 服务（localhost:11434）。
  Mac:     安装并启动官方 Ollama.app：https://ollama.com/download/mac
  Windows: 从 https://ollama.com/download 下载安装包
  装好后重新运行本命令即可启用。
```

### `openbiliclaw embedding-cache-stats`

查看 embedding L2 持久化缓存（`data/embedding_cache.db`）的诊断信息，用于确认
provenance namespace 隔离是否生效、旧 JSON 行是否已迁移为二进制、磁盘占用是否
在预算内（issue #153）：

```bash
$ openbiliclaw embedding-cache-stats
Embedding L2 缓存诊断 · data/embedding_cache.db
缓存概况
  数据库文件      …/data/embedding_cache.db
  总行数          14,419
  逻辑载荷        230.0 MiB
  SQLite 主文件   221.0 MiB
  WAL / SHM       12.0 MiB / 2.0 MiB
  legacy 行（无 namespace）  0 行 / 0 B
  namespaced 行   14,419 行 / 230.0 MiB
  active 行       12,000 行 / 192.0 MiB
  inactive 行     2,419 行 / 38.0 MiB
  容量预算        不设上限
  最近维护        已删除 5,000 行 / 701.8 MiB
Namespace 分布
  model  namespace  行数  载荷     状态
  bge-m3#namespace=abc123  …  12,000  192.0 MiB  active
  bge-m3#namespace=dead  …     2,419   38.0 MiB  inactive
```

命令会顺带执行与 daemon 相同的一次性运行时准备（legacy JSON → 二进制迁移，幂等）。

### `openbiliclaw embedding-cache-clean`

手动清理 embedding L2 缓存，默认 dry-run 只报告，加 `--apply` 才执行。三个阶段的
目的对应 issue #153 的三条整改：

1. **JSON → 二进制迁移**：把 `encoding=0` 的旧 JSON 向量迁移为紧凑 float32 BLOB
   （幂等、小批量提交、中断可续跑；损坏行标记后跳过）。
2. **回收失效 namespace**：删除不在当前 active namespace 的行（默认含 legacy 行；
   `--keep-legacy` 保留 legacy 行，`--keep-model m1,m2` 额外保护指定 L2 model key）。
3. **物理回收**：WAL checkpoint + `VACUUM INTO` 新文件 + `integrity_check` + 原子替换，
   让磁盘占用实际下降（仅 `DELETE` 只会进 freelist，主文件不缩小）。

```bash
$ openbiliclaw embedding-cache-clean            # 预览：将迁移/删除哪些行
$ openbiliclaw embedding-cache-clean --apply    # 执行迁移 + 删除 + 物理回收
$ openbiliclaw embedding-cache-clean --apply --keep-legacy --no-compact
```

清理前请先停止 daemon：物理替换需要独占文件。缓存可重建，删除的只是冷数据，
不影响推荐正确性。

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
本轮推荐
推荐 1
  标题: 讲透城市与建筑的空间叙事
  UP 主: 城市观察局
  发布时间: 3 天前
  话题标签: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

`发布时间` 复用后端统一 formatter：精确 `published_at` 按本地时区显示为“刚刚 / N 小时前 / N 天前 / 月日 / 年-月-日”，精确值缺失时回退到来源 `published_label`；两者都为空时整行不输出。CLI 不展示原始 UTC 字符串，也不以发现时间或推荐生成时间代替发布时间。

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```

### `openbiliclaw feedback <id> <like|dislike|comment|dismiss>`

为一条已展示的推荐记录写入结构化反馈，可附带备注；`comment` 必须带 `--note`，`dismiss` 走软移除语义不要求备注。

```bash
$ openbiliclaw feedback 7 dislike --note "太浅了"
反馈已记录
反馈详情
  推荐ID: 7
  反馈: dislike
  请求ID: 8f4...（实际输出为完整 ID）
  备注: 太浅了

$ openbiliclaw feedback 7 comment --note "方向对，但我想看更深一点。" \
  --request-id feedback-7-comment-20260802
```

`--request-id` 会 trim，最长 400 字符。省略或只传空白时命令生成 UUID hex，并在「反馈详情」打印出来；如果终端在 durable commit 后丢失响应、或要在另一次命令中重试，必须把第一次输出的 ID 传回 `--request-id`。同一次进程内盲目再生成新 ID 会被视为新的反馈动作。显式 ID 超过 400 字符会在构造 runtime/写库前退出。

每次反馈执行以下两个写入操作：

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用

durable event 的首写与 recommendation 投影是命令成功边界；后续即时认知记录、owner drain 或摘要刷新属于可恢复的 follow-up。它们暂时失败时命令仍以退出码 `0` 返回，并明确提示「反馈已记录，画像处理稍后重试」，不会把已经提交的反馈误报为失败。相同 request identity 的重试读取首写 payload；若身份已被不同反馈占用则报冲突，不覆盖原记录。CLI 的 producer namespace 会把该 ID 持久化为 `cli:<request-id>`，用户仍只传/保存未加前缀的输出值。

### `openbiliclaw fetch-douyin`

`fetch-douyin`、`fetch-xhs`、`fetch-youtube` 共用同一单源任务 runner：任务明确回报 `timeout` 或 `failed` 时会先打印平台专属原因/计数，再以退出码 `1` 结束，供脚本和真实 smoke 正确判失败；`ok` / `empty` 保持退出码 `0`。抖音还可能返回 `degraded`：命令会保留并打印已经写入的事件，同时给出“结果不完整”警告；它属于完成但降级的部分成功，退出码仍为 `0`。CLI 自身等待超时不会伪称已取消浏览器里的任务，后端若稍后收到扩展终态仍会按任务协议保存结果。

单独触发抖音 `bootstrap_profile` 拉取，适合 smoke 测试扩展和补拉抖音信号。它只执行“入队 → 唤醒扩展 → 等结果 → 打印 scope counts”，不跑 B 站认证检查、不跑 `analyze_events()` / `build_initial_profile()` / discovery。事件由 daemon 在接收 `/api/sources/dy/task-result` partial 时写入 memory，CLI 自身不会再传播一次，避免重复入库。

```bash
$ openbiliclaw fetch-douyin
抖音 数据拉取
  抖音 发布 24 条 / 收藏 13 个 / 点赞 12 个 / 关注 1 人
  共 50 条事件已由 daemon 写入 memory。
```

默认最多等待扩展回传 `180s`；需要更长排查窗口时可显式加 `--wait-seconds 240`。命令默认复用 6 小时内已有的 pending / in-progress / 非降级 completed / failed 抖音 `bootstrap_profile` 任务，避免反复打开前台抖音 tab 全量拉发布 / 收藏 / 点赞 / 关注；已 `degraded` 的 completed 结果会自动重新入队补齐分页。需要无条件重新拉取时可设 `OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0`。

前提：

- `openbiliclaw start` 或 `serve-api` 后端正在运行。
- Chrome 扩展已安装并在线。
- 浏览器已登录 `https://www.douyin.com`。

### `openbiliclaw fetch-xhs`

单独触发小红书 `bootstrap_profile` 拉取，定位与 `fetch-douyin` 相同：用于单源验证 / 补拉，不隐式重建画像。

```bash
$ openbiliclaw fetch-xhs
小红书 数据拉取
  小红书 收藏 20 个 / 点赞 20 个 / 浏览记录 0 个
```

默认最多等待扩展回传 `180s`，与 `init --yes-xhs --yes-douyin` 的单源 collect 窗口保持一致，降低两源连续初始化时小红书未结束就启动抖音的概率。命令默认复用 6 小时内已有的 pending / in-progress / completed / failed `bootstrap_profile` 任务，避免重复打开前台小红书 tab 抓收藏 / 点赞；排查时需要强制重拉可加 `--force`，或用 `OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS=0` 关闭复用窗口。

### `openbiliclaw fetch-youtube`

单独触发 YouTube `bootstrap_profile` 拉取，用于验证浏览器扩展、登录态和 `/api/sources/yt/*` 后端任务桥是否联通。采集范围与 init 相同：观看历史、订阅频道、点赞视频。

```bash
$ openbiliclaw fetch-youtube --wait-seconds 240
YouTube 数据拉取
  YouTube 观看历史 40 条 / 订阅 12 个 / 点赞 20 个
  共生成 72 条事件。
```

这条命令只做单源 smoke / 补拉，不会隐式重建画像。profile 已初始化后，daemon 接收新增 partial 事件时会写入 memory 并进入增量画像更新链路。命令默认复用 6 小时内已有的 YouTube `bootstrap_profile` 任务，避免反复打开前台 YouTube 页面滚动历史 / 订阅 / 点赞；需要重新拉取时可设 `OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0`。

### `openbiliclaw fetch-v2ex`

只读触发 V2EX `bootstrap_profile`，依次验证本人发布、本人回复、收藏主题和收藏 Node 四个 scope。命令会等待真实安装扩展使用当前浏览器登录态读取页面，把结果转换为 canonical 事件后只打印计数；任务显式携带 `smoke_only=true`，因此除 canonical task result 与身份 / 登录心跳外，不会写入 memory、Node Affinity、收藏快照或 Soul，也不会调用 LLM。

```bash
$ openbiliclaw fetch-v2ex --force --wait-seconds 300
V2EX 数据拉取
  V2EX 发布 42 条 / 讨论 186 个 Topic / 收藏主题 128 条 / 收藏 Node 16 个
  共转换 372 条 canonical 事件；未写入 memory，未触发画像或 LLM。
```

用户名默认从已登录 V2EX 页面顶部导航观察；需要固定公开账号路径时可传 `--username <name>`。任务默认复用 6 小时内的近期结果，真实回归应加 `--force`。任一 scope 达到条目 / 页数上限或解析失败时命令显示 `partial`，保留已读取数据但明确不把本次结果作为完整收藏快照；身份冲突、无可归属信号、超时和任务失败使用非零退出码。CLI 的 `/kick` 会读取 `[api].port`，因此扩展与后端使用非默认端口时也能立即唤醒 dispatcher。

### `openbiliclaw fetch-zhihu`

单独触发知乎 `bootstrap_events` 拉取，用于验证浏览器扩展、知乎登录态和 `/api/sources/zhihu/*` 后端任务桥是否联通。默认采集最近浏览记录、收藏夹条目和当前知乎用户主页动态里的点赞 / 收藏动作；扩展会通过 `/api/v4/me` 自动识别当前用户，传入 `--profile-slug` 时可手动覆盖。

```bash
$ openbiliclaw fetch-zhihu --wait-seconds 240
知乎 数据拉取
  知乎 浏览 300 条 / 收藏 423 条 / 点赞 16 条
  共抓取并转换 739 条事件；未触发画像生成。
```

这条命令只做事件爬取 smoke，不会写入 memory，也不会触发画像初始化或增量画像更新。CLI 会把扩展回传的 `zhihu_read_history`、`zhihu_collection`、`zhihu_activity` 条目转换成统一事件并打印计数，方便先确认浏览 / 收藏 / 点赞链路是否真实可用。命令默认复用 6 小时内已有的知乎 `bootstrap_events` 任务，避免反复打开前台知乎 tab；排查时可加 `--force`，或设置 `OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0` 强制新建任务。

需要把真实抓到的知乎事件落到本地 memory 时加 `--write-memory`；命令会按 `source_platform + event_type + content_id / url / title` 做本地去重，只写入本次新增事件。需要在写入后立刻触发画像重建时加 `--rebuild-profile`，该选项隐含 `--write-memory`，会调用真实 LLM 完成偏好分析和初始画像生成，适合端到端验证，不适合只做登录态 smoke。

默认分支上限为：浏览历史 300、收藏夹条目 300、动态点赞 300、动态收藏 300；理论最大事件数为 1200 条，实际数量会受知乎接口返回、去重和收藏夹数量影响。

### `openbiliclaw fetch-x`

单独触发 X（Twitter）点赞 / 收藏拉取，对应 `fetch-xhs` / `fetch-douyin` / `fetch-youtube`，但 X 是**服务端 cookie 重放**（无扩展 bootstrap 任务、**不需要 daemon**）：直接用已同步的 `x.com` cookie（`data/x_cookie.json` 或 `OPENBILICLAW_X_COOKIE`）拉取你自己的点赞 + 收藏，经 `_x_tweet_to_event` 转成统一事件写入 memory，用于在不重跑完整 `init` 的情况下验证 X 历史偏好回填链路。

```bash
$ openbiliclaw fetch-x -n 50
拉取 X 点赞 / 收藏
  X 点赞 50 条 / 收藏 23 条 → 共 73 条事件。
  已写入 memory：73 条事件。 跑 `openbiliclaw rebuild-profile` 让画像吃进新信号。
```

`--limit/-n` 控制每类最多拉取条数（默认 50，`init` 回填用 200）；`--dry-run` 只拉取并打印、不写 memory。点赞 → `event_type="like"`、收藏 → `event_type="favorite"`（均为显式正向信号）。cookie 未同步时静默跳过（0 条事件、退出码 0），不报错；拉取本身 best-effort，单类失败（cookie 过期 / 限流 / 偶发 TLS）只打印告警、不中断。每个真实请求无论成功或失败都会更新共享 `XSourceHealthStore`（证据绑定当前 Cookie 指纹），所以 dry-run 成功后来源状态能立即显示「请求反馈」验证，401/403/429 也会给设置页留下可定位的健康状态；dry-run 仍然不会写画像事件。

### `openbiliclaw import-youtube <path>`

从 Google Takeout 导出的 `.zip` 或解压目录导入 YouTube 观看历史、订阅和点赞数据，适合扩展无法读取旧历史或用户想一次性补齐冷启动信号的场景。

```bash
$ openbiliclaw import-youtube ~/Downloads/takeout.zip --dry-run
导入 YouTube Takeout
  解析完成：
    观看历史  1200 条
    订阅频道  88 个
    点赞视频  320 个
    合计      1608 条事件
```

不带 `--dry-run` 时，命令会把解析出的 YouTube 事件传播到记忆层，并调用 `analyze_events()` 更新偏好画像；它不会重新跑完整 init，也不会自动补推荐池。

### `openbiliclaw discover`

读取当前画像并触发一次内容发现。默认跑 Bilibili 的全部策略并将结果写入 `content_cache`，支持通过 `--source` 切换到 xiaohongshu 关键词生产流程、douyin discovery、知乎插件 discovery、Reddit discovery、Linux.do discovery 或 Bangumi 官方 API discovery，或通过 `--strategy` 限定只跑部分 Bilibili 策略。知乎正式流程会复用 runtime `ZhihuDiscoveryProducer`，按配置页 / `config.toml` 的 `[sources.zhihu].source_modes` 入队 search / hot / feed / creator / related 任务；Reddit 正式流程复用 `RedditDiscoveryProducer`，默认用 `[sources.reddit].backend="rdt"` 的 rdt-cli 登录态命令后端，按 `source_modes` 抓 search / hot / subreddit / related 候选，命令后端不可用时自动 fallback 到 OpenBiliClaw 插件任务；Linux.do 正式流程复用 `LinuxdoDiscoveryProducer`，按 `[sources.linuxdo].source_modes` 在真实站点 tab 内执行 search / hot / feed / creator / related 同源 GET；Bangumi 正式流程复用 `BangumiDiscoveryProducer`，按 `[sources.bangumi].source_modes`、subject types、分支预算、cursor 与 cooldown 直连官方匿名 API。这些非 B 站来源的候选都只写 `discovery_candidates`，评估由后台统一 evaluator 处理。
读取当前画像并触发一次内容发现。默认跑 Bilibili 的全部策略并将结果写入 `content_cache`，支持通过 `--source` 切换到 xiaohongshu 关键词生产流程、douyin discovery、知乎插件 discovery、Reddit discovery、Bangumi 官方 API discovery 或 V2EX discovery，或通过 `--strategy` 限定只跑部分 Bilibili 策略。知乎正式流程会复用 runtime `ZhihuDiscoveryProducer`，按配置页 / `config.toml` 的 `[sources.zhihu].source_modes` 入队 search / hot / feed / creator / related 任务并进入统一待评估池；Reddit 正式流程复用 `RedditDiscoveryProducer`，默认用 `[sources.reddit].backend="rdt"` 的 rdt-cli 登录态命令后端，按 `source_modes` 抓 search / hot / subreddit / related 候选，命令后端不可用时自动 fallback 到 OpenBiliClaw 插件任务；Bangumi 正式流程复用 `BangumiDiscoveryProducer`，按 `[sources.bangumi].source_modes`、subject types、分支预算、cursor 与 cooldown 直连官方匿名 API；V2EX 正式流程复用 `V2EXDiscoveryProducer`，按 `[sources.v2ex].source_modes`、Node/Tab 配置、分支预算和 cooldown 读取官方公开 API / Feed。Reddit、知乎、Bangumi 和 V2EX 候选都只写 `discovery_candidates`，评估由后台统一 evaluator 处理。

手动 `discover` 是一次性进程，其 candidate pipeline 固定 `eval_min_batch_size=1`、`eval_max_wait_seconds=0`，立即 drain 本次已入队候选；只有常驻 API daemon 才读取 `[scheduler]` 的默认 15 / 90 秒聚合策略。这样 CLI 不会在退出时遗失只存在内存里的凑批等待状态。

```bash
# 默认：Bilibili 全策略
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache
  来源: bilibili
  策略: 全部

# 只跑 search + trending
$ openbiliclaw discover --strategy search,trending --limit 20

# 触发 xiaohongshu 关键词生产（由扩展在后台抓取）
$ openbiliclaw discover --source xiaohongshu
小红书关键词生产
生产摘要
  入队关键词数: 5
  尝试关键词数: 5
  今日预算: 20
  节流开关: 4 小时节流

# 忽略 4 小时节流
$ openbiliclaw discover --source xiaohongshu --force

# 触发 douyin discovery
# Cookie 可由扩展自动同步；下面的环境变量仅用于调试时显式覆盖
$ export OPENBILICLAW_DOUYIN_COOKIE='msToken=...; ttwid=...; ...'
$ openbiliclaw discover --source douyin --limit 20
抖音内容发现
发现摘要
  发现条数: 8
  入池候选: 8
  来源: douyin
  分支: search, hot

# 触发知乎正式 discovery（使用设置页选中的 source_modes）
$ openbiliclaw discover --source zhihu --limit 20
知乎内容发现
发现摘要
  发现条数: 20
  入池候选: 20
  来源: zhihu
  来源分布: zhihu-feed:5, zhihu-hot:5, zhihu-related:10
  分支: search, hot, feed, creator, related

# 触发 Reddit 正式 discovery（使用设置页选中的 source_modes）
$ openbiliclaw discover --source reddit --limit 20
Reddit 内容发现
发现摘要
  发现条数: 31
  入池候选: 6
  来源: reddit
  来源分布: reddit-hot:2, reddit-related:15, reddit-search:4, reddit-subreddit:10
  分支: search, hot, subreddit, related
  后端: rdt

# 触发 Linux.do 正式 discovery（使用配置的 source_modes）
$ openbiliclaw discover --source linuxdo --limit 20
Linux.do 内容发现
发现摘要
  发现条数: 20
  入池候选: 20
  来源: linuxdo
  分支: search, hot, feed, creator, related

# 触发 Bangumi 正式 discovery（匿名只读取数，候选进入待评估池）
$ openbiliclaw discover --source bangumi --limit 20
Bangumi 内容发现
发现摘要
  发现条数: 20
  入池候选: 20
  来源: bangumi
  分支: search, ranked, latest

# 触发 V2EX 正式 discovery（匿名公开读取，PAT 可选）
$ openbiliclaw discover --source v2ex --limit 20
V2EX 内容发现
发现摘要
  发现条数: 20
  入池候选: 20
  来源: v2ex
  分支: search, node, tab, hot, latest
```

显式 Bangumi 或 Linux.do discover 要求对应 `[sources.<slug>].enabled=true`，但即使 `[scheduler].enabled=false` 也会执行；scheduler 总开关只暂停后台自动任务。producer 返回 disabled 或画像尚未初始化时，CLI 会显示对应修复提示，而不是回落为通用“未产出内容”。

选项：

- `--source, -s`：`bilibili`（默认）、`xiaohongshu`、`douyin`、`zhihu`、`reddit`、`linuxdo` 或 `bangumi`
显式 Bangumi / V2EX discover 要求对应 `[sources.<source>].enabled=true`，但即使 `[scheduler].enabled=false` 也会执行；scheduler 总开关只暂停后台自动任务。producer 返回 disabled 或画像尚未初始化时，CLI 会显示对应修复提示，而不是回落为通用“未产出内容”。

选项：

- `--source, -s`：`bilibili`（默认）、`xiaohongshu`、`douyin`、`zhihu`、`reddit`、`bangumi` 或 `v2ex`
- `--strategy, -S`：仅对 Bilibili 生效，可多次传或逗号分隔，取值 `search` / `trending` / `explore` / `related_chain`
- `--limit, -n`：发现结果条数上限，默认 `30`
- `--force`：xiaohongshu / Bangumi / V2EX 可用；忽略本地最小调度间隔，但仍遵循持久化远端 cooldown

抖音 discovery 需要 `[sources.douyin].enabled = true`。`discover --source douyin` 现在直接调用与 daemon 相同的正式 `DouyinDiscoveryProducer`：统一关键词 claim、插件 search / hot / feed、`DiscoveryCandidatePipeline` 待评估入池和关键词终态都与后台一致；显式手动命令只绕过 `[scheduler].enabled` 这个后台总开关，来源开关、source mode、预算、候选池上限和 producer cadence 仍然生效。Cookie 解析顺序是：先读 `cookie_env` 指向的环境变量（默认 `OPENBILICLAW_DOUYIN_COOKIE`，适合调试覆盖），再读浏览器扩展同步的 `data/douyin_cookie.json`。初始化画像的 `init --yes-douyin` 不受这个配置影响，仍走浏览器扩展任务桥。知乎 discovery 需要 `[sources.zhihu].enabled = true`，并依赖已登录知乎的浏览器扩展；`discover --source zhihu` 会读取 `[sources.zhihu].source_modes`，不会使用 `--strategy`。Reddit discovery 需要 `[sources.reddit].enabled = true`；默认 `backend="rdt"`，优先使用 rdt-cli 登录态命令后端，不使用 CDP/临时浏览器；rdt / opencli 不可用时自动复用 OpenBiliClaw 插件所在浏览器的 Reddit 登录态，也可在配置页显式切到 `extension`。Linux.do discovery 需要 `[sources.linuxdo].enabled = true` 和在线扩展；公开分支不要求登录，个人 bootstrap 才要求 `/session/current.json` 返回正面账号身份。

`search` 子来源走浏览器插件 DOM-first 链路：CLI 入队 `dy_tasks(type="search")`，扩展后台 tab 先打开抖音首页，再在已登录页面里模拟搜索框输入 / 提交，候选以 `dy-plugin-search` 进入 discovery；fetch tap 兼容 `/general/search/single/`、`/search/item/` 和新版 `/general/search/stream/` chunked JSON。`hot` 子来源同样走插件：后端取 hot board 的 `sentence_id`，并把可用的 `group_id` 作为 `seed_aweme_id` 透传给扩展；扩展从首页点击热榜 / 热点入口和目标热词，靠页面自身加载与被动响应监听回传 `dy_hot`，不足时用已登录页面的 related API bridge 按 seed 拉相关视频，候选以 `dy-plugin-hot-related` 进入 discovery；小批量 hot 请求会展开一个小窗口并优先执行带 seed 的 hot item，在累计达到 `--limit` 后提前结束。`feed` 子来源会入队 `dy_tasks(type="feed")`，扩展在首页推荐流滚动触发加载，候选以 `dy-plugin-feed` 进入 discovery。三条链路都不主动跳 `/search/...`、`/hot/...` 快捷 URL；终态区分真实空结果、超时、失败与预算耗尽，正式命令遇到后三者会返回非零退出码，direct-cookie fallback 只保留给显式诊断路径。search 若真实响应为 `search_nil_info.search_nil_item="hit_shark"` 且没有 `data/aweme_list`，属于抖音反爬空结果，CLI 会显示“完成但没有候选”，并保持成功退出。

需要调试抖音 discovery 子来源时，使用独立命令 `openbiliclaw discover-douyin`。它直接调用 `DouyinDiscoveryService`，可以显式指定关键词、分支、是否写缓存和是否跳过 LLM 评估；它是源接口诊断，不 claim 统一关键词，也不经过正式 producer 的候选 pipeline。正式手动补池应使用 `discover --source douyin`：

```bash
# 调试 search + feed，直接看源接口召回，不写 content_cache
$ openbiliclaw discover-douyin \
  --keyword 猫咪,机械键盘 \
  --source search,feed \
  --limit 20 \
  --no-cache \
  --no-evaluate
```

`discover-douyin` 的 `--source` 只接受 `search` / `hot` / `feed`；不传时默认三者都跑。`--keyword` 不传时从 Soul 画像兴趣生成搜索词；`hot` 会自动取 hot board 热词，不需要手动传关键词；`feed` 直接从抖音首页推荐流召回，不需要关键词。插件链路的一次命令共享一个 wall-clock wait budget：首个 search / hot / feed 等待到期后，剩余分支不再串行各等 180 秒。超时任务会原子落成 `failed + wait_timeout`（上层取消为 `wait_cancelled`），CLI 返回非零并提示检查扩展在线状态或调大 `OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS`；该任务不会继续留在 `pending`。

xiaohongshu 渠道并不直接抓取内容，而是调用 `XhsTaskProducer.produce_if_due()` 将 Soul 画像改写成关键词写入 `xhs_tasks` 表，由浏览器扩展的后台调度器在隐藏 Tab 中抓取。新配置默认每日搜索预算 20、producer 间隔 20 分钟；pending + in-progress 搜索任务达到 5 条时返回 `backlog`，不会继续生成关键词。若返回 `throttled` 可加 `--force` 跳过本次 producer 时间闸，但 `--force` 不会绕过积压门、每日预算、领取端 ±25% 抖动或平台风控冷却；若返回 `no_profile` 需先执行 `openbiliclaw init`。

### `openbiliclaw discover-zhihu`

通过浏览器插件执行知乎搜索 discovery，适合真实端到端测试已登录知乎浏览器路径。CLI 会入队 `zhihu_tasks(type="search")`，唤醒已安装插件，等待扩展在真实 `zhihu.com` 登录态里拉取 `zhihu_search` 候选，再把候选转换为统一 `DiscoveredContent` 并写入 `discovery_candidates(pending_eval)`。这条命令不会写 memory，也不会触发画像初始化；正式手动补池优先使用 `discover --source zhihu`，它会按配置的 `source_modes` 跑完整 producer 并接入统一 evaluator。

```bash
$ openbiliclaw discover-zhihu "AI 工程化" "数据库" --limit 10 --wait-seconds 240
知乎搜索发现
  知乎搜索 20 条候选
  已写入待评估候选池：20 条
```

选项：

- 位置参数 `keywords`：一个或多个知乎搜索关键词；也可以用逗号分隔。
- `--limit, -n`：每个关键词最多回传的搜索候选数，默认 `20`。
- `--wait-seconds, -w`：等待插件任务完成的最长时间，默认 `180`。
- `--no-enqueue`：只看插件搜索结果，不写入 `discovery_candidates`。

如果返回 `login_required`，先在安装了 OpenBiliClaw 插件的 Chrome 里正常登录知乎；这条链路不使用 CDP，也不需要另开调试浏览器。

同一插件任务桥还提供四个非搜索 smoke 命令：

```bash
openbiliclaw discover-zhihu-hot --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-feed --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-creator https://www.zhihu.com/people/<slug> --limit 10 --wait-seconds 240
openbiliclaw discover-zhihu-related https://www.zhihu.com/question/<id> --limit 10 --wait-seconds 240
```

它们分别入队 `zhihu_tasks(type="hot"|"feed"|"creator"|"related")`，回写 `zhihu_hot` / `zhihu_feed` / `zhihu_creator` / `zhihu_related` 候选，source strategy 对应 `zhihu-hot` / `zhihu-feed` / `zhihu-creator` / `zhihu-related`。

### `openbiliclaw fetch-reddit`

单独触发 Reddit 事件 / 搜索 smoke，用于验证 Reddit 后端、登录态和归一化是否联通。默认 `--backend rdt`，`rdt-cli` 已随后端默认安装；已连接插件会把 `reddit_session` 自动同步到 rdt-cli credential store，插件不可用时才需要在本机已登录 Reddit 的浏览器环境里运行 `rdt login`。`--mode search|hot|subreddit|related` 优先通过 rdt-cli 读取候选并转换为低权重 view 事件用于终端预览；命令后端不可用、未登录或显式 `--backend extension` 时会改走插件任务桥。`--mode bootstrap` 会自动使用插件后端，入队 `reddit_tasks(type="bootstrap_events")` 并拉 saved / upvoted / subscribed。默认不会写 memory，也不会触发画像初始化或增量画像更新；需要真实落库时必须显式传 `--write-memory`，需要写入后重建画像时传 `--rebuild-profile`。`bootstrap` 只支持 extension 后端，因为它必须运行在已登录浏览器同源页面内。

```bash
$ openbiliclaw fetch-reddit "open source ai" --limit 10 --wait-seconds 180
Reddit 数据拉取
  Reddit 搜索 10 条 / 统一事件 10 条

$ openbiliclaw fetch-reddit --mode bootstrap --wait-seconds 180
Reddit 事件拉取
  收藏(saved) 12 条 / 点赞(upvoted) 31 条 / 订阅 subreddit 18 个
  写入 memory 未写入 memory
  画像生成 未触发画像生成
```

### `openbiliclaw discover-reddit*`

Reddit discovery smoke 命令会把 rdt-cli（默认安装）、OpenCLI 或插件后端返回的候选转换为 `DiscoveredContent(source_platform="reddit")` 并写入 `discovery_candidates(pending_eval)`；rdt / opencli 不可用或未登录时会自动 fallback 到插件任务。它们只验证取数和入池，不写 memory、不重建画像、不直接写 `content_cache`。正式补池优先使用 `openbiliclaw discover --source reddit`，它会按配置页保存的 `source_modes`、后端和来源比例进入 runtime producer。

```bash
openbiliclaw discover-reddit "open source ai" --limit 10
openbiliclaw discover-reddit-hot --subreddit all --limit 10
openbiliclaw discover-reddit-subreddit LocalLLaMA --limit 10
openbiliclaw discover-reddit-related https://www.reddit.com/r/LocalLLaMA/comments/<id>/<slug>/ --limit 10
```

`discover-reddit` 默认走 search；`discover-reddit-hot` 默认 `r/all`，rdt 路径实际调用 `rdt all --json`；`discover-reddit-subreddit` 需要一个或多个 subreddit 名，rdt 路径实际调用 `rdt sub <name> --json`；`discover-reddit-related` 需要一个或多个 Reddit 内容 URL，rdt 路径会抽取 `/comments/<id>/` 后调用 `rdt read <id> --json`。命令默认 `--backend rdt`，优先使用插件同步的 rdt credential；插件不可用时可手动运行 `rdt login`。需要强制插件登录态链路时加 `--backend extension --wait-seconds 180`。若 rdt 路径不可用或未登录，CLI 会自动 fallback 到插件；若插件路径返回 `login_required`，请在安装了 OpenBiliClaw 插件的浏览器里正常登录 Reddit。

### `openbiliclaw fetch-linuxdo`

只读验证 Linux.do 个人书签、点赞和阅读记录采集。命令入队 `linuxdo_tasks(type="bootstrap_events")`，等待扩展在真实 `linux.do` tab 内完成同源 GET，然后展示三个 scope 的计数。默认不写 memory、不会重建画像，也不会修改 Linux.do 上的任何状态；只有显式传 `--write-memory` 才会把归一化事件写入本地账本。

默认端到端总等待为 32.5 分钟：pending 阶段最多约 3 分钟让在线扩展领取；任务进入 `in_progress` 后，按页数、scope 宽度和请求间隔给予最宽约 29 分钟执行窗口，再留 30 秒结果余量。显式 `--wait-seconds` 是从入队开始计算的总硬上限，设为 `180` 秒可能截断已经领取的任务。guided init 的 stage 1 基础总预算仍是 30 分钟；仅选择 Linux.do 且使用默认预算时至少给 32.5 分钟，同时选择 Linux.do 与至少一个其它来源时给 62.5 分钟，显式 timeout override 原样生效、不扩容。

```bash
openbiliclaw fetch-linuxdo
openbiliclaw fetch-linuxdo --force --write-memory
```

- `--wait-seconds, -w`：从任务入队到终态结果的总硬上限，默认 `1950` 秒（32.5 分钟）；pending 领取仍最多约 3 分钟，进入 `in_progress` 后执行期限还会受任务形状上限约束。
- `--force`：忽略近期任务复用窗口，强制重新拉取。
- `--write-memory`：将本批 `favorite` / `like` / `view` 事件写入本地 memory；不会自动重建画像。

个人 bootstrap 要求已登录浏览器内的只读 `GET /session/current.json` 返回 `current_user.username`。`_t` 只参与登录布尔判断，其值以及其他 Cookie、CSRF 数据、原始 JSON/HTML 响应均不会上传。三个 scope 全部失败时以结构化 `failed` 结束；部分 scope 失败时显示 `degraded` 与已采计数，保留有效项但不冒充完整成功。默认 6 小时复用只接受在途或 `ok/empty` 任务；`failed/degraded` 下次自动重新入队。

### `openbiliclaw discover-linuxdo`

按 `[sources.linuxdo].source_modes` 执行一次正式 Linux.do discovery；与 `openbiliclaw discover --source linuxdo` 使用相同的 `LinuxdoDiscoveryProducer`、关键词 claim、候选管线和预算。显式命令即使 `[scheduler].enabled=false` 也执行，只绕过 daemon 后台总开关。五个可配置分支是 `search`、`hot`、`feed`、`creator`、`related`，返回项统一为 `content_id="topic:<topic_id>"`、`content_type="post"` 后写入 `discovery_candidates(pending_eval)`。

```bash
openbiliclaw discover-linuxdo --limit 30
openbiliclaw discover --source linuxdo --limit 30
```

该命令只有 `--limit, -n` 选项（默认 `30`）；关键词、作者页和相关主题 seed 由共享 producer 根据画像、关键词池与已有候选选择。producer 的默认端到端等待同样是 32.5 分钟：pending 最多约 3 分钟，领取后按任务形状最多约 29 分钟执行，并留 30 秒结果余量。公开 discovery 不强制登录，但依赖在线扩展和 Linux.do host permission。全部站点请求均为同源 GET，插件只回传归一化字段或结构化错误，不回传 Cookie 或原始响应。发现摘要的计数来自本轮 producer；紧随其后的候选预览还会按 `linuxdo-*` strategy 再过滤一次，避免长驻共享候选管线中其它平台的旧条目混入 Linux.do 输出。自动化测试覆盖任务协议、分页、预算、超时和错误映射；2026-08-11 的真实 Chrome E2E 进一步验证了正式 discover、后台任务、候选入池与配置中的真实 LLM 评估。

### `openbiliclaw fetch-bangumi`

通过 Bangumi 官方 API 读取收藏。默认只打印统计，不写 memory、不重建画像、也不调用 LLM：

```bash
openbiliclaw fetch-bangumi --username sai --limit 20
openbiliclaw fetch-bangumi --token <personal-access-token> --limit 20
openbiliclaw fetch-bangumi --username sai --limit 100 --write-memory
openbiliclaw fetch-bangumi --username sai --limit 100 --write-memory --rebuild-profile
```

- `--username, -u`：公开用户名；省略时读取 `[sources.bangumi].username`。
- `--token`：个人令牌；省略时读取 `[sources.bangumi].access_token`。命中令牌时优先于用户名：先经 `GET /v0/me` 自动识别当前用户，再带 Bearer 读取该账号收藏（含私密行）；令牌被拒绝（401）时报"个人令牌被拒绝（缺失、错误或已过期）"并指引到 https://next.bgm.tv/demo/access-token 重新生成。
- `--limit, -n`：最多读取条目数；`0` 使用配置的 `bootstrap_limit`。
- `--write-memory`：显式把转换后的收藏事件写入本地 memory。
- `--rebuild-profile`：在写入后用本批事件重建画像，会真实调用当前 LLM；该选项会隐含启用 `--write-memory`。

令牌与用户名皆缺时报错提示"通过 --token（推荐，自动识别当前用户）或 --username 提供访问方式"。该命令始终只读，不会修改用户的 Bangumi 收藏、评分或进度。

### `openbiliclaw discover-bangumi*`

三个独立命令是官方 API 的安全只读 smoke，不写候选池、memory 或画像：

```bash
openbiliclaw discover-bangumi "攻壳机动队" --limit 10
openbiliclaw discover-bangumi-ranked --limit 10
openbiliclaw discover-bangumi-latest --limit 10
```

搜索使用配置中的 `subject_types`；ranked/latest 会按配置类型分配结果窗口。`discover-bangumi-latest` 对应官方 `sort=date`，响应可能含未来或未播条目，因此 CLI 不把它表述为实时更新。单次 `--limit` 限制在 `1..50`。

要让结果进入正式推荐链，使用 `openbiliclaw discover --source bangumi`；它会写 `discovery_candidates(pending_eval)`，由共享 evaluator/admission 决定是否进入可推荐池。每日分支预算按跨分支去重和最终 limit 后实际保留的候选计数，重复/截断条目不扣额度。

### `openbiliclaw discover-v2ex*`

五个独立命令是 V2EX 的只读公开 discovery smoke，不需要配置 PAT，也不写画像或 V2EX 站内状态：

```bash
openbiliclaw discover-v2ex "agent" --limit 10
openbiliclaw discover-v2ex-node programmer --limit 10
openbiliclaw discover-v2ex-tab tech --limit 10
openbiliclaw discover-v2ex-hot --limit 10
openbiliclaw discover-v2ex-latest --limit 10
```

正式来源补货使用 `openbiliclaw discover --source v2ex`。该命令要求 `[sources.v2ex].enabled=true`，使用 `search / node / tab / hot / latest` 配置和共享候选 pipeline；PAT 配置后会优先使用 API 2.0，401/403 自动回落匿名。`search` 优先复用已配置的 Exa / You provider 做 `site:v2ex.com/t` 召回，再用官方 Topic 详情补全；provider 不可用时回退 latest/hot，先做整句精确匹配，再对 planner 长词做最多 8 个非通用核心词的有界匹配。Search provider 是否启用不受 legacy / hybrid / inspiration 关键词生成模式影响。所有 V2EX 结果都是 Topic 文字卡，Reply 不单独进入候选池；浏览器登录态仅用于四个只读 bootstrap / incremental scope，不参与公开 discovery 鉴权。

### `openbiliclaw search-douyin`

通过浏览器插件执行抖音搜索 smoke，适合排查真实登录浏览器 DOM-first 路径能否召回视频候选。

```bash
$ openbiliclaw search-douyin -k 猫 --max-items-per-keyword 10 -w 180
抖音搜索发现
  抖音搜索 10 条候选
  1. 盘点全网那些叛逆的猫咪... 迷惑菌呀
     https://www.douyin.com/video/7219607743328537915
```

行为边界：

- CLI 入队 `dy_tasks(type="search")`，唤醒扩展 dispatcher，等待 `dy_tasks.result_json`。
- 扩展会在已登录抖音浏览器会话的后台 tab 先打开首页，再模拟真实搜索框输入和提交；MAIN-world fetch tap 只被动收集页面自己发出的搜索响应，content script 同时解析已渲染 DOM，再把 `dy_search` 候选回传。
- 默认等待窗口为 `180s`；如果调试机上搜索页首开很慢，可显式加 `--wait-seconds 240`。
- 结果只作为搜索 discovery 候选保存在任务结果中；后端不会把它转换成 memory event，也不会重建画像。独立 `search-douyin` smoke 不写 `content_cache`；`discover-douyin --source search` 可用 cache 模式直接写 `content_cache`，而正式 `discover --source douyin` 会把同一候选写入 `discovery_candidates(pending_eval)`。
- smoke 的每日 search 预算读取 `[sources.douyin].daily_search_budget`，`0` 表示不设上限，不再使用硬编码 20。真实空结果保持退出码 0；插件 timeout / failed 或预算耗尽返回非零退出码，便于脚本和运维区分“确实没内容”与“任务没完成”。
- 如果返回 0 条，优先检查是否有多个加载扩展的 Chrome 实例抢任务、当前浏览器是否登录抖音、页面搜索入口是否可见，以及 debug 中 `ui_triggered / api_items_harvested / dom_items_harvested`。若 direct / 页面响应的 `search_nil_info.search_nil_item` 为 `hit_shark`，说明当前 Cookie / 会话被抖音搜索风控空 200 拦截。

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw chat`

进入持续对话模式，复用 `SocraticDialogue` 的多轮历史。CLI 构造点显式固定为
`legacy_direct`：得到回复后仍按既有 detached direct learning 学习，既不提交 API
runtime 的 `DialogueSettlementQueue`，也不持有 worker guard permit；因此行为不变，
但不享受队列串行/receipt/guard 保证。Wave 3 的 HTTP `202 processing` 与 30 秒
卡片轮询只服务 popup、移动 Web 与桌面 Web 卡片，CLI 没有 action HTTP 入口，不新增 poll。输入
`exit`、`quit` 或空行可结束。聊天内容
仅在得到真实回复后以受控方式积累到长期理解候选中，不会因为一句话立刻改写画像。
单轮 LLM 失败会打印安全、可操作的错因（不显示上游异常原文），REPL 继续接受下一轮输入。

```bash
$ openbiliclaw chat
苏格拉底式对话
你：我最近总在刷讲结构的视频。
阿花：我听见你在说，你现在在意的可能不只是内容本身，而是想把事情看得更透一点。
你：exit
阿花：对话结束。
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw start`

启动本地后端 API 服务，默认监听 `127.0.0.1:8420`，供浏览器插件或本地调试调用。

启动前会先做两件事：

1. 检查 `data/openbiliclaw.db` 是否完整；如果检测到损坏，会拒绝启动并提示先执行 `openbiliclaw db-repair`
2. 在数据库健康且距离上次冷备超过 24 小时时，自动生成一份冷备到 `data/backups/`

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

如果数据库已损坏：

```bash
$ openbiliclaw start
数据库损坏
检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。
```

### `openbiliclaw db-repair`

显式检查并修复本地 SQLite 数据库。命令遵循”先检查、先备份、后修复”的顺序：

1. 运行完整性检查
2. 若数据库正在被进程占用则拒绝继续
3. 备份 `openbiliclaw.db` 与可选的 `openbiliclaw.db-wal`
4. 尝试恢复到新的 repaired 副本
5. 验证 repaired 副本通过后，再切换正式库

```bash
$ openbiliclaw db-repair
数据库已恢复并完成切换。
备份文件: data/backups/openbiliclaw-20260315-020000.db
恢复副本: data/openbiliclaw.repaired.db
```

如果数据库本来就是健康的，命令会直接退出并提示无需修复；如果仍被运行中服务占用，会返回非零退出码并列出占用进程。

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用”开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。
