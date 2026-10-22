# 蓝色大肥鱼（Blue Fish Lab）设计说明

日期：2026-10-19
状态：实现中
分支：`feat/blue-fish-lab`

## 1. 要解决什么

OpenBiliClaw 已经能发现内容、理解用户、写推荐语。缺的是**在 B 站评论区里说话**：
回复别人的评论，并在正文里 @ 对方。

「养蓝色大肥鱼」是一个**实验室功能**：给 OpenBiliClaw 配一个**独立的小号**，
让它以自己的人格出现在评论区，挑一条评论回复、@ 对方，并把每次行动记到账本里。
「养」体现在等级与经验上——但它们由账本推导，不是另一套记忆。

**鱼的反馈与用户的反馈是两条线。** 推荐池是共享的（复用主链路的评估结果），
但写回是单向隔离的：鱼的动作只落 `fish/ledger.jsonl`，不写推荐反馈、不动画像。

## 2. 非目标

- 不做自动点赞 / 收藏 / 转发 / 发动态
- 不做多只鱼、不做跨账号共享
- 不做自动私信（评论区之外的动作一律不做）
- 不让鱼的互动影响用户画像 / 推荐状态：这是独立的反馈线，不是「代替用户反馈」
- 不在 v1 做人工审批队列（用 `dry_run` + 配额代替）

## 3. 账号隔离（硬要求）

| 项 | 做法 |
|---|---|
| 凭证文件 | `<data_dir>/fish/bilibili_cookie.json`，与主账号 `bilibili_cookie.json` 完全分开 |
| 客户端 | 每次运行新建 `BilibiliAPIClient`，只吃鱼自己的 cookie |
| 回退 | **禁止**回退到 `config.toml [bilibili].cookie`；没有鱼 cookie 就是「未登录」 |
| 身份校验 | 鱼的 `mid` 必须与主账号 `mid` 不同，cookie 字符串也不能相同；否则拒绝运行 |
| 数据 | `fish/` 目录只放鱼自己的配置、状态、账本；删掉目录 = 重置 |

`AuthManager` 已经按 `data_dir` 存 `<data_dir>/bilibili_cookie.json`，
所以用 `AuthManager(cfg.data_path / "fish")` 即可拿到隔离实例，不需要改鉴权代码。

## 4. 配置

存在 `<data_dir>/fish/config.json`，**故意不写进 `config.toml`**：

- 这是实验室功能，字段还会变，塞进主配置会把 `config.toml` 的兼容面拉大；
- 主配置的读写路径有签名/热重载/迁移逻辑，不值得为一个实验特性动它；
- `fish/` 目录自带配置，天然支持「删目录即重置」。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 实验室总开关 |
| `dry_run` | `true` | 只生成不发帖；默认就这么开 |
| `persona_name` | `蓝色大肥鱼` | 对外的名字 |
| `persona_prompt` | 内置 | 人格设定，用户可改 |
| `reply_rules` | 内置 | 额外说话规矩，用户可改 |
| `audience_prompt` | 空 | 可选：只回哪类人；空 = 不限定 |
| `max_chars` | `80` | 单条评论字数上限（B 站上限 1000，我们主动收窄） |
| `daily_limit` | `3` | 每天最多发几条 |
| `min_interval_minutes` | `30` | 两次发言的最小间隔 |
| `per_video_limit` | `1` | 同一视频最多发几条 |
| `max_candidates` | `5` | 每轮看几个视频 |
| `comments_per_video` | `10` | 每个视频取几条评论 |
| `min_comment_likes` | `0` | 只回点赞数 ≥ N 的评论（0 = 不过滤） |
| `blocked_words` | `[]` | 命中即丢弃这条回复 |
| `allow_top_level` | `false` | 是否允许发顶层评论（默认只做楼中楼回复） |

写配置时逐字段钳制范围，非法值回落默认值并记一条 warning，**不抛异常**——
实验室功能不该因为一个数字把设置页打挂。

## 5. 运行数据

只有 `ledger.jsonl`：一次行动一行，追加写。**没有 `state.json`** —— 等级 / 经验 /
按天计数 / 查重 / 单视频上限全部由账本推导（`FishStore.view(today)` 一次扫描）。
鱼不维护自己的画像记忆：它的行动只留在账本里，不进入主链路的兴趣线。

账本字段：`ts`、`day`、`recommendation_id`、`bvid`、`video_title`、`mode`
（`reply` / `top_level`）、`target_rpid`、`target_uname`、`target_text`、`reply`、
`reason`、`status`（`preview` / `published` / `failed` / `skipped`）、`dry_run`、`error`、
`reply_rpid`（发布成功后 B 站返回的评论 id，可用于以后删除/追踪）。
`recommendation_id` 只是溯源用的 trace id，从不写回那一行。

## 6. 一轮怎么跑

```
守卫（启用 / 已登录 / 账号隔离 / 日配额 / 最小间隔，全部读账本）
  → 取候选视频（主链路已评估的推荐池，exclude_processed=True）
  → 逐个视频：翻页拉评论（cursor.next，≤5 页）→ 过滤（点赞门槛 / 已回过 / 太短太长）
  → 组 prompt（persona + audience + video + comments）→ LLM 选一条 + 写回复（一次调用）
  → 硬闸校验（字数 / 屏蔽词 / 空回复 / 只挑一条）
  → dry_run ? 记账为 preview : 调 post_comment 发出去
  → 发布成功：只写鱼的账本（含 reply_rpid；不写推荐反馈、不触发 cognition）
  → 等级 / 经验由账本推导
```

- 复用边界：爬取与内容评估不新增实现，候选直接来自推荐池；模型只做「挑一条 + 怎么写」。
  写回是隔离的：鱼的评论不构成推荐反馈，`update_recommendation_feedback` 与
  `record_immediate_feedback_cognition` 都不会被调用，用户画像不受影响。
- LLM 一次调用同时完成「挑哪条」和「怎么写」，失败就放弃这一轮，不重试轰炸。
- 回复写成楼中楼：`root` = 目标评论的根，`parent` = 目标评论本身；
  正文以 `@昵称` 开头（B 站的 @ 是纯文本）。
- 只有 `allow_top_level` 打开且模型明确选 `top_level` 时才发顶层评论。

## 7. 安全闸（代码级，不靠提示词）

1. `enabled` 关闭 → 不跑（不读 Cookie、不调 nav、不写账本）
2. 没有鱼 cookie / cookie 失效 → 不跑，返回明确原因
3. 鱼 mid == 主号 mid → 拒绝（隔离破坏）
4. 当日计数 ≥ `daily_limit` → 不跑
5. 距上次发言 < `min_interval_minutes` → 不跑
6. 同一 `rpid` 只回一次（账本查重）
7. 同一视频累计 ≥ `per_video_limit`（跨天累计）→ 换下一个视频
8. 回复为空 / 超 `max_chars` / 命中屏蔽词 → 丢弃并记 `skipped`
8b. 模型返回 `top_level` 但 `allow_top_level=false` → 记 `skipped`（代码级，不靠提示词）
9. B 站接口报错 → 记 `failed`，本轮停止，不自动重试
10. `dry_run` 默认开，要用户自己关掉才会真的发
11. 写回隔离：发布成功后唯一落盘的是账本；推荐行、画像、cognition 一律不动
12. 连续 3 条 `failed` 且最近一次在 60 分钟内 → 定时轮返回 `failure_cooldown`；
    手动触发绕过，便于修好账号后立刻验证

## 8. API

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/fish/status` | 开关、登录态、等级、今日计数、最近记录 |
| GET | `/api/fish/config` | 读鱼配置 |
| PUT | `/api/fish/config` | 改鱼配置（逐字段钳制） |
| POST | `/api/fish/account` | 校验并保存鱼 cookie |
| DELETE | `/api/fish/account` | 清掉鱼 cookie |
| POST | `/api/fish/run` | 跑一轮（`dry_run` 可覆盖） |
| GET | `/api/fish/ledger` | 读账本 |

## 9. UI

- **桌面 Web 设置页**：新增 Tab「养蓝色大肥鱼」（实验室徽标）
- **扩展 popup 设置浮层**：同样的 Tab，复用同一组接口
- 面板内容：状态行 / 总开关 / dry-run / 账号（cookie 输入 + 校验）/ 人格与规矩（可编辑）/
  配额 / 「预览一轮」「发一轮」/ 最近账本

两个前端都不把鱼 cookie 写进全局 `config.toml` 保存流程，单独调 `/api/fish/account`。

## 10. 后台循环

`BlueFishService.run_forever()`：每 5 分钟醒一次，交给引擎自己判断配额和间隔。
跟 `refresh_task` 一样挂在 `task_registry` 上，配置热重载时随其它后台任务一起重启。

## 11. 测试

- 配置读写与钳制（含坏 JSON）
- 守卫：未启用 / 未登录 / 账号相同 / 超配额 / 间隔不足
- 账本查重：同一 rpid 不回第二次
- dry-run 不发请求
- 发布路径：fake client 收到正确的 `root` / `parent` / `@`
- prompt builder：system 消息与调用参数无关（接进 `test_llm_prompts.py`）
- 账号隔离：绝不读主账号 cookie
- 反馈隔离：发布后推荐行保持未处理、cognition 不被调用、该行仍出现在
  `exclude_processed=True` 的候选池里；鱼的记录只存在于账本
- 真实账号联调（`@pytest.mark.integration`，默认跳过）：拉评论、账号隔离、
  发一条楼中楼再删除；评论翻页另有单测守护

## 12. 2026-10-20 增补：作为 B 站发现的一路

原设计把鱼完全放在推荐链路下游。实际使用中它应该同时是 B 站 discover 上的一路：
发现的内容要经过现有评估器，互动发生在同一条链路里。落地方式是「读」「说」分离：

- `discovery/strategies/comment_thread.py` 的 `CommentThreadStrategy` 是只读策略：
  从已评估推荐池取 1–3 个种子，读评论页，把评论里点名的 BV 号还原成
  `DiscoveredContent` 交给统一 batch evaluator；同时把「种子 + 评论快照」写进
  `data/fish/plans.json`（最多 3 条、45 分钟过期）。
- `BlueFishEngine.run_once()` 优先消费计划（复用评论快照，不重复抓取），
  没有计划时回退到原来的推荐池路径；隔离 / 熔断 / 配额 / 间隔 / 账本查重全部不变。
- 计划不在 `discover()` 里执行的原因：`discover()` 是被 refresh、初始化、候选流水线和
  CLI 多处调用的并发只读生产者，异常还会被 `asyncio.gather` 吞掉；公开发言必须留在
  有闸门、有账本、异常可诊断的动作层。
- 该路线只在 API runtime 注册（CLI 没有动作层），由鱼的 5 分钟 tick 驱动而不是主
  refresh 计划：读评论区必须用鱼的账号，且池满时主 refresh 会跳过发现。

## 13. 2026-10-20 增补：在评论区 @ 主人

第三件事：鱼每天可以在视频评论区 @ 主人一次，把一条推荐变成主人 B 站消息里的通知。

- 新配置 `notify_owner`（默认关）、`owner_name`（留空读主账号 nav 昵称）、
  `notify_daily_limit`（默认 1，0–3）。
- 发送形态：在推荐池第一条视频下发一条**顶层评论**，正文以 `@昵称 ` 开头。B 站从评论
  正文里解析提及（响应里的 `content.members`），所以昵称必须与主号完全一致；昵称里带
  空格 / 特殊字符时用 `owner_name` 手填。
- 一轮只做一件公开的事：通知到期时这一轮不发回复，下一轮再回；通知同样写进账本
  （`status="notified"`、`mode="notify"`），并计入最小间隔和 `per_video_limit`。
- 默认关闭的理由：评论是公开的，@ 主号等于公开把两个号关联；没配昵称或主号昵称读不到时
  不发、静默回落成普通回复。

## 14. 2026-10-20 增补：扫码登录小号

只支持粘贴 Cookie 让配置门槛偏高（要会从浏览器里复制 SESSDATA / bili_jct）。补上扫码：

- 后端 `POST /api/fish/account/qrcode` 调 `/x/passport-login/web/qrcode/generate`，
  把登录 URL 用 `qrcode` 渲染成 SVG data URL 返回；`GET /api/fish/account/qrcode/poll`
  轮询状态，`confirmed` 时从跨域 URL 提取 Cookie、`save_cookie`、再跑一遍隔离校验。
- 前端两个设置面板共用同一套流程：点「扫码登录」→ 显示二维码 → 每 2 秒轮询 →
  `scanned` / `expired` / `logged_in` / `rejected` 各自给一句人话。
- 隔离规则不变：扫到主号（mid 或 Cookie 相同）立刻回滚 Cookie 并拒绝；
  `pending` 阶段不写任何 Cookie。
- 二维码在深色主题下也要能扫，所以 `<img>` 固定白底。

## 15. 2026-10-20 增补：投递方式、触发条件、时段与日报

对齐同类插件（dsh-bili-subagent / dsh-bili-scroll）的四块能力：

- **投递方式** `notify_channel`：`comment`（默认）/ `dm` / `both`。私信走
  `BilibiliAPIClient.send_private_message()`（vc 域，文本消息）；`both` 沿用「评论优先」：
  评论失败就停在失败，评论成功但私信失败记 `error`、`delivery=comment`，不回滚。
- **触发条件** `trigger_prompt`：进 user 消息的 `<trigger>` 段，system 仍为字节静态常量；
  模型先按它判断该不该开口，不符合就返回 `skip`。
- **发言时段** `active_hours`：`HH:MM-HH:MM`，支持跨夜；按本机时区判断，只拦自动轮
  （手动「跑一轮」是用户的显式选择，不受限）；非法格式在保存时回落为空并记 warning。
- **日报**：每次尝试在 `data/fish/out/<日期>.md` 追加一行（时间 / 状态 / 视频 / 正文），
  账本仍是权威来源，日报只是人看的视图。

## 16. 2026-10-20 增补：质量门槛与日报视图

- **候选质量门槛**：`min_view_count` / `min_publish_age_days`，默认 0 关闭。开启后
  fail-closed：播放量未知（0）或发布时间不可解析的候选直接挡掉，而不是默认放行。
  这是对「搜到什么就回什么」类插件的最小子集，但我们不按播放量排序、也不因此改变
  主链路的推荐权重 —— 它只决定鱼这次去不去。
- **日报视图**：`FishStore.read_report()` 读 `out/<日期>.md`，由 `/api/fish/status`
  以 `today_report` 返回；两个设置面板都加了一个「今日日报」折叠块。账本仍是权威来源。
