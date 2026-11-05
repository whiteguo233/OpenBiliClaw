# 抖音账号身份与初始化采集修复验证（2026-11-05）

## 范围与证据来源

- Integration level：`capability-increment`，仅修复浏览器扩展本人身份及作品 / 喜欢 / 收藏 / 关注初始化采集。
- 基线：`f4af7744995fe053df6cb771738a88d58c419b4a`（已发布 `0.3.233`）。实现分支：`fix/douyin-account-bootstrap`；独立 worktree：`.worktrees/douyin-account-bootstrap`。
- 上游接口证据来自用户提供的《OpenBiliClaw 0.3.233：抖音账号识别与个人记录采集修复说明》：2026-11-04 在 EGO 已登录环境对照本人查询参数、接口、分页及四类记录，2026-11-05 整理归档。该现场记录属于提供方证据，此前离线阶段未独立重跑真实登录请求；追加真实验收见下文，未将提供方记录计作本轮 E2E PASS。
- 离线回归阶段使用合成账号 / 响应，不读取真实 Cookie。随后追加隔离真实测试，使用本机既有抖音凭据和独立测试浏览器，仅对现有后端做健康检查，不运行 LLM / embedding。用户进一步选定正式 `18420` 后端后，又执行了下文的正式服务 / 原模型测试；两个阶段分别记账。
- 复用既有 [扩展账号任务契约](../modules/extension.md#抖音任务桥)；未新增来源注册、配置项、依赖、权限或后端 API，因此不复制整份平台 contract，不把静态接线 audit 当作账号采集证明。

## 修复边界

1. `profile/self` 在页面 MAIN world 内携带 `device_platform=webapp`、`aid=6383`、`channel=channel_pc_web`、`cookie_enabled=true`；保留 `credentials=include`、`status_code=0` 校验、同 tab 正面身份缓存与消息来源校验。
2. 喜欢改为 `favorite` GET；收藏改为 `listcollection` POST，URL 与 JSON 请求体共用数值游标；关注改为 `following/list` GET，补充 `offset=0`。旧接口仅保留被动识别兼容。
3. 收藏优先使用响应 `cursor`，兼容 `max_cursor`；喜欢保持 `max_cursor`，关注保留 `min_time / max_time / cursor` 兼容。收藏游标不能损失 JSON 数值精度；缺失 / 非法 / 重复 / 成环和页数上限仍报错。
4. `runScope` 的账号结果仅来自本人 API。正常空列表保持为空；后续页失败只保留前面有效的 API 条目；身份或首屏失败时返回零条账号记录并报告降级。页面推荐卡片既不能成为账号行为，也不能先占满预算挤掉 API 记录。搜索 / 热榜 / 推荐流继续使用自己的 DOM / 被动 collector。
5. 缺失或错误类型的 item-list 返回 `api_items_invalid`；零页、无错误的 bridge 返回 `api_response_unobserved`，不伪报正常空列表。最终 `scope_count` 与 API 条目一致。
6. 默认每类 300 条、最多 50 页不变，`ok` 表示在本次任务额度内成功，不表示已经采集全部历史。旧 `max_scroll_rounds` payload 继续兼容，但账号采集不再滚页补入 DOM。历史误导入数据和用户手动反馈均不自动清理。

## 验证台账

| Gate | 适用性 | 状态 | 证据 |
| --- | --- | --- | --- |
| 独立分支与来源路径 | required | PASS | worktree 基于固定基线；Python package 与 CLI 的 `__file__` 都指向该 worktree 的 `src/`，测试显式使用 `PYTHONPATH="$PWD/src"`。Node 从该 worktree 的 `extension/` 运行。 |
| 请求与分页回归 | required | PASS | 修改前 `dy-fetch-tap.test.ts` 61 项中 9 项失败、52 项通过，覆盖查询参数、分类、POST / cursor、关注路径及假空；修复后通过。 |
| 账号归属与降级 | required | PASS | 四 scope 分别覆盖有数据、正常空与 API partial；另测身份缺失、首屏失败、异常和零页。默认 `runScope → postMessage → installApiHarvester → fetch → postMessage → runScope` 使用真实桥接代码与合成传输，期间插入推荐卡片消息仍返回正确空列表。 |
| 扩展全量回归 | required | PASS | `cd extension && npm test`：1,879 passed，0 failed，exit 0；桥接用例改为真实 harvester 后再次运行两份受影响测试：111 passed，exit 0。 |
| 后端账号事件回归 | required | PASS | `PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_dy_tasks.py tests/test_api_dy_ingest.py tests/test_douyin_login_probe.py -q`：41 passed，exit 0；既有 FastAPI lifespan 弃用 warning 不影响结果。数据库由测试 fixture 隔离。 |
| 静态检查 | required | PASS | `npm run typecheck` exit 0；同一 Python 环境运行 `mypy src/`：366 files 无错误；`ruff check src/ tests/`：PASS。 |
| Chrome / EGO 构建与资源 | required | PASS | `npm run build`、`npm run verify:assets`、`npm run package:only` exit 0；22 项 manifest script / WAR 资产存在。ZIP CRC 通过，两个抖音 bundle 与本 worktree 的 `dist/` 字节一致。 |
| Firefox 构建与资源 | required | PASS | `npm run build:firefox`、`npm run package:firefox:only` exit 0；22 项资产通过。Firefox 构建后重新检查 Chrome 资源仍通过；ZIP CRC 通过，两个抖音 bundle 与 `dist-firefox/` 字节一致。 |
| 已安装浏览器当前账号 → 持久事件 E2E | required | PASS（有界） | Chrome for Testing 实装脚本 SHA 匹配；正式队列 / dispatcher / MAIN 请求 / HTTP 回调完成四类采集，23 / 4 / 7 / 8 条，42 条事件逐项一致。作品两页；其他三类真实数据各一页。模型画像生成未运行。 |
| 真实首屏 + 后续页传输故障 | required | PASS | 在独立浏览器对作品第二页注入一次 connection reset，正式任务返回 degraded，保留首屏 18 条且 DOM=0；重复任务未增加事件。 |
| 正式 18420 账号 → 原模型 → 画像 | required（追加请求） | BLOCKED | 本人身份成功；作品 / 收藏 / 喜欢 HTTP 403，关注 7 条真实入库；原 full worker 接收 7 条信号，主 / 备用模型 HTTP 429，按既有 65 秒退避重试后失败；核心画像 SHA 未变。 |
| 来源注册 / discover / 推荐器改造 | N/A | N/A | 代码仅改变既有账号 harvester 与入口；不改注册表、discovery collector、生产者、画像事件映射或模型配置。 |
| 真实站点状态变更、历史数据清理、提交 / 合并 / 发布 | N/A | N/A | 用户已授权修复与真实测试；本轮未执行社交写动作、生产数据清理、Git 提交 / push / 合并、商店上传或发布。 |

## 安装包与手动复测

构建产物来自本修复 worktree。扩展 manifest 仍为 `0.3.233`，本地构建不等于商店版本已经更新；安装路径和脚本摘要才能区分本修复。正式打包脚本生成原名 ZIP 后，另复制同字节的 `douyin-account-fix` 文件名便于区分；Firefox 未签名包只用于临时加载，不等于商店 XPI。

| 目标 | 交付文件（相对 worktree 的 `extension/`） | SHA-256 |
| --- | --- | --- |
| Chrome / EGO | `openbiliclaw-extension-v0.3.233-douyin-account-fix.zip` | `785c411f6a0f0be91b01016eed36fa75ee3a469bcaa4d73b53d936a4a9a5af87` |
| Firefox | `openbiliclaw-extension-v0.3.233-douyin-account-fix-firefox.zip` | `e750bb8b4ea47bdd0a35fd347938e2aba24b6a3e5aefe4aaddf73bca459f7261` |

日志与完整 bundle 摘要留在本地 `.tmp/douyin-account-audit/`，不进入 Git。原浏览器商店扩展未更改；追加验收已在独立测试浏览器加载 worktree 扩展并验证脚本摘要，详见下节。

1. 将 Chromium ZIP 解压到新的固定目录，在 EGO / Chrome 扩展管理页加载该目录并确认扩展路径。重新加载现有扩展只会读取它当前注册的目录，不能自动部署本 worktree 的文件。
2. 使用同一浏览器登录抖音，分别执行作品、喜欢、收藏、关注初始化采集。核对本人查询业务状态为 0，数据归属与实际个人列表一致，记录数量及任务 `ok / empty / degraded` 状态。
3. 对真实空列表确认 `empty + 0`，周围推荐区域不应补入个人记录。检查收藏请求为 POST，下一页 URL 与 body 的 cursor 一致；喜欢 / 关注至少覆盖两页并确认游标推进。
4. 人工模拟后续页断网 / 失败时，检查最终 `degraded`、有效 API partial 与错误诊断；不得出现推荐卡片记录。超过 300 条时按任务上限解释结果，不宣称全量。
5. 若需清理旧错误数据，先另行圈定导入来源和具体记录；本修复不执行删除，不触及手动反馈。

## 追加真实端到端验收（2026-11-05）

用户明确要求真实端到端测试后，在独立 Chrome for Testing 和真实 FastAPI 服务上执行了以下两轮。完整脱敏汇总见 [live-evidence.json](assets/2026-11-05-douyin-account/live-evidence.json)。

### 实装、登录和隔离环境

- 本机原服务 `127.0.0.1:18420` 保持运行。测试 API 为 `127.0.0.1:52168`，Python 使用本 worktree 的源码，新建独立 config / data / SQLite；两者健康检查均 HTTP 200。测试实例保留原模型配置，但通过已有外置 worker 模式运行 API，不启动模型 worker，因此本次证明到持久事件回传，不包含模型画像生成或推荐。
- 独立浏览器加载本 worktree 的 `extension/`，扩展 ID `hkgfbeinkeidmnkhdgociclhaclfmbam`。在真实 service worker 内读取自身资源并计算 SHA-256，MAIN 为 `bb140c11e8756d5783c538393e49664df0b0172d8acf3581f0262226bfd62ae9`，content 为 `1cc4ba1337eb771ca6025c08c428a650c4e3612623366fc537d7f20447132b1e`，与修复构建一致。仅该测试扩展 endpoint 指向隔离 API。
- 初始预检直接打开个人页曾进入验证码中间页。续测沿用同一测试 profile；前轮进程结束后临时会话 Cookie 未保留，重新载入同一份本机既有凭据后页面正常进入本人账号，真实本人接口 HTTP 200 / `status_code=0`。本轮没有操作验证码、轮换账号或代理，也没有使用第三方解码服务。不能把这次恢复解释为对所有风控的解决。
- 任务通过真实 `DyTaskQueue.enqueue_with_id` 入队、`POST /api/sources/dy/kick` 通知，由实装扩展领取并执行；未伪造采集响应或回调，也未手工写入事件。正式 dispatcher 从首页进入本人页，每类任务上限 36 条。

### 四类正向采集

任务 `2e2d713a-bba6-479b-99b2-472ecacb774e` 在 07:41:12 UTC 完成。四个 scope 各通过真实 HTTP partial 回传，最终回调 `ok`，数据库任务状态 `completed`。

| Scope | 真实接口 | 返回 / 入库事件 | 页数 | 业务结果 |
| --- | --- | --- | --- | --- |
| 作品 | `aweme/post` GET | 23 / view 23 | 2（18 + 5） | HTTP 200、status 0；第二页游标等于首屏返回值，最终 has_more=0 |
| 收藏 | `aweme/listcollection` POST | 4 / favorite 4 | 1 | HTTP 200、status 0；URL 与 JSON body cursor 均为 0 |
| 喜欢 | `aweme/favorite` GET | 7 / like 7 | 1 | HTTP 200、status 0；has_more=0 |
| 关注 | `user/following/list` GET | 8 / follow 8 | 1 | HTTP 200、status 0；has_more=false |

逐 scope 比较真实接口返回 ID 集合、扩展回调 ID、最终任务结果和持久事件 scope / ID，全部一致。所有主动请求的 `sec_user_id` 与权威本人接口一致，作品作者也与本人一致。四类 `dom_items_harvested=0`，`api_items_harvested` 分别为 23 / 4 / 7 / 8，没有推荐卡片补入账号记录。

隔离库从空变为 `events=42 / seen_items=29 / profile_update_ledger=0`；42 条事件全部为 douyin 且均绑定本轮任务。`seen_items` 按内容去重，不等于跨 scope 的事件条数。模型 worker 未运行，不能据此宣称画像更新通过。

### 第二页故障与重复导入

任务 `c4fa1a08-41a4-41ca-93a1-2ad1a964f20e` 只请求作品，上限仍为 36。首屏读取真实源站 18 条，仅对带产品请求参数的第二页通过浏览器路由注入一次 `connectionreset`，没有伪造 JSON 或制造源站风控。

最终回调为 `degraded`，错误 `Failed to fetch`，有效 API 页数 1，保留的 18 条 ID 与真实首屏逐项一致，DOM 条目为 0。后端任务正常终态 `completed` 并保留 scope degraded；跨任务去重后事件仍为 42、seen_items 仍为 29、profile ledger 仍为 0。不能把这个注入故障称作源站自然故障。

### 生命周期、清理与验证限制

- 执行时观察到任务页 `muted=true / audible=false`；手动账号任务允许前台，观测为 `active=true`，不宣称全程后台执行或声学静音。两轮结束后任务页自动关闭，活动任务 session 状态清空，仅保留原预检页。
- 完成后关闭测试浏览器、停止隔离 API，删除两阶段临时 backend config / 数据库、浏览器 profile / Cookie 和原始请求回调日志，仅保留脱敏汇总。原 `18420` 服务最终健康检查 HTTP 200。没有修改原浏览器商店版、原配置或生产数据，也没有点赞、收藏、关注等账号写操作。
- 本账号的收藏、喜欢、关注各只有一页，未宣称三者多页真实验收；相应分页仍由确定性回归覆盖。本账号没有正常空 scope，正常空列表的防污染边界仍是回归证据。EGO、Firefox 实装、无人值守调度、模型画像生成和推荐链路均未在本轮运行。

## 追加正式环境与原模型验收（20:07 CST 起）

用户进一步明确选择本机 `http://127.0.0.1:18420` 正式服务。这一轮不再使用隔离数据库，原服务、原 full worker、原模型配置和正式画像保持原路径运行。结果为 **BLOCKED：上游 403 / 验证码与原模型 429**；上一阶段隔离账号链路的 PASS 不能替代这一轮。

### 环境、备份与执行

- 正式服务实际版本为 `0.3.230`，源码快照 `a0cae9322bd55f37ee957cdd365878cf1e433a28`，运行目录 `~/Library/Application Support/OpenBiliClaw-AppReview`。CLI 导入路径与配置 data_path 均核对为该部署，未用新 worktree 的 Python 代码迁移正式库。
- 在 `manual-backups/douyin-e2e-20261105-200709/` 留存 SQLite backup API 一致性备份、memory 文件快照和配置副本；目录权限为 0700，数据库和配置为 0600，不入 Git。数据库与文件快照分别完成，不宣称跨进程全局原子快照。
- 沿用 Chrome for Testing 加载修复扩展，实装两个抖音脚本 SHA 与前述构建一致；该浏览器 endpoint 为 `18420`。原商店扩展未替换，正式服务 / worker 未重启，模型、Key、并发、退避、时间戳、去重状态和消费游标均未手工改变。
- 实际执行部署版本 `python -m openbiliclaw.cli fetch-douyin --wait-seconds 240`，使用产品默认账号额度。CLI 入队、正式 API kick、实装 dispatcher 和真实回调正常工作。完整脱敏证据见 [production-evidence.json](assets/2026-11-05-douyin-account/production-evidence.json)。

### 正式任务与有效 partial

任务 `fed7f6e0-de22-4474-8921-d03e886e53e4` 于 12:09:05 UTC 创建、12:09:44 UTC 终态。本人 `profile/self` HTTP 200、业务 status 0；作品 GET、收藏 POST、喜欢 GET 均 HTTP 403，关注 GET HTTP 200 / status 0，返回当前账号 7 条关注。

最终回调为 `degraded`（数据库终态 `completed`），scope 分别为 `degraded / degraded / degraded / ok`。7 条关注在源响应、回调、任务结果及正式事件库中的 ID 集合完全一致，请求账号与权威本人一致；所有 scope 的 DOM 条目为 0。403 未被改写成“正常空”，也未由推荐卡补齐。

正式库新增 7 条绑定本任务的 follow 事件，另有浏览器正常采集产生的 1 条 snapshot；两者分开记录，不将 snapshot 冒充账号任务结果。没有删除已接受的真实信号，也没有制造输入以绕过去重。

本轮页面触发滑块验证码。普通拖动尝试后没有取得验证成功状态，未轮换账号 / 代理或伪造验证响应，停止进一步采集重试；该源站限制未解决。

### 原 full worker 与模型结果

- API 的 `event_lane_paused=true` 对应已有 full worker 委派模式，不据此判定 worker 停止。正式 worker 心跳持续正常，`profile_events` 消费游标从 109857 推进到 109865，累计信号数从 226 增至 233，确认接收了 7 条关注信号。
- worker 日志明确记录 `preference chunk 1/1 started: events=7`，随后原配置 `openai_compatible / deepseek-v4-flash` 和 `openai-compatible / glm-5.2` 路由出现 HTTP 429。该次处理共观察到 3 次真实 HTTP 429，执行既有两次 65 秒退避，131.4 秒后报告所有可选 provider 失败 / 冷却中。没有更换模型或缩短冷却来获得 PASS。
- 正式库没有新增 usage 成功记录或 profile_update_ledger 记录；HTTP 调用证据来自 worker 日志，不能把空 usage 表增量解释为“未调用模型”。preference、awareness、insight、soul、soul_profile.json / .md 的 SHA 均与测试前一致。因此只证明已进入原模型处理，**画像生成未通过**。
- 消费游标已推进而本次画像未更新；事件事实仍在正式库。本轮没有回拨游标、清空去重或再次注入相同信号，不宣称解除限流后该批信号必然自动重放。需要恢复模型可用性后另行核对受影响批次的重试 / 重建行为。

### 收尾与范围

任务已终态，扩展活动任务状态清空，任务页自动关闭。随后关闭本轮浏览器并删除其临时 profile / Cookie，正式服务与 worker 保持运行，备份和有效事件保留。配置 SHA 和核心画像 SHA 未变；没有向源站执行点赞、收藏、关注等写操作。该阶段没有故障注入，403 和 429 均为自然真实响应。

## 追加 Codex 订阅模型真实请求复测（20:37 CST）

用户提出改用已有 Codex 订阅模型后，使用本机已登录的 `gpt-6.1-sol` 和官方 Codex 端点完成真实请求。没有导入其它会话身份或更换账号。脱敏证据见 [codex-evidence.json](assets/2026-11-05-douyin-account/codex-evidence.json)。

| 路径 | 数据与结果 | 耗时 |
| --- | --- | --- |
| 基础 JSON 探测 | 真实返回有效 JSON；31 tokens | 3.11 秒 |
| 正式 0.3.230 代码的 LLMService → PreferenceAnalyzer | 只读正式任务中的 7 条 follow；因 Codex input 缺少 json 标记失败；没有 429 | 1.22 秒 |
| worktree 0.3.233 代码 | 同一批 7 条记录与已有偏好；结构化解析、偏好合并成功；2470 tokens | 9.14 秒 |
| 正式 0.3.230 分析代码，仅诊断进程加载修复后的 Codex transport | 同一批记录与已有偏好；成功；5403 tokens | 11.60 秒 |

第三组与第四组分别验证当前版本可用，以及旧版业务栈只替换传输模块就能通过。第四组保留旧版 prompt / LLMService / PreferenceAnalyzer，避免把新版其它 prompt 变化误归因于传输修复。两次成功结果均有 72 个合并后的兴趣，包含既有画像，**不是从 7 条关注新提取出 72 个兴趣**。

这些是读取正式数据的独立诊断进程，调用真实模型，但没有改正式配置、重启或切换正式 worker，也没有调用画像持久化入口。三次业务探测前后配置与核心画像文件 SHA 全部一致，最终正式 API runtime-status HTTP 200 且 worker_running=true。没有输出或保存账号原始数据、模型画像正文及凭据。

结论：Codex 当前可用，原模型 429 可通过选用该路模型避开；但正式 0.3.230 还缺 #334 的 JSON 请求兼容修复。本轮证明了兼容修复的真实业务效果，**不代表正式 worker 已成功写回画像**。抖音三类请求的 403 仍是独立阻塞。

## 正式部署与画像恢复（2026-11-06）

用户要求继续完成正式修复后，已部署到 `127.0.0.1:18420`。脱敏证据见 [deployment-evidence.json](assets/2026-11-05-douyin-account/deployment-evidence.json)。

- 基于原 `a0cae9322bd55f37ee957cdd365878cf1e433a28` 的 src 创建独立部署快照，只移植 #334 的 Codex JSON input marker，并补上本轮发现的抖音账号结果后端校验；没有把当前 main 的其它功能整体升级到正式环境。LaunchAgent 的 PYTHONPATH 指向新快照，配置/data root 不变。
- 服务停稳后使用 SQLite backup API、memory 文件和配置/LaunchAgent 副本备份到本机 `manual-backups/douyin-codex-20261106-002709/`，目录 0700、配置/数据库 0600。新增实例 `codex-profile`（OAuth、`gpt-6.1-sol`、low），只将 soul route 指向它；default chain、discovery、recommendation、evaluation 均保持原值。
- 修复扩展已在临时 Chrome for Testing 中验证加载 hash，但普通 Chrome 的旧扩展同时在线，抢先领取新 bootstrap `9a7873fa-2e10-4e96-b345-9b77e09a1912`。旧客户端在 identity_unavailable / API=0 时返回 43 个条目，去重后误入库 40 条。不能把这个 degraded 任务当作有效采集，也不能把临时浏览器实装 hash 归给该回调。
- 发现后立即停止服务；确认 generic cursor 仍为 109865、buffer 为空，错误记录尚未被消费。将该任务、40 条记录及相关 seen 状态备份到私有 quarantine，撤销本轮错误事件、13 个 seen 投影和本轮新增 dy bootstrap 去重键，将该任务标为 failed / quarantined。此前有效任务的 7 条 follow 保留；没有批量清理历史数据。后台认知循环独立产生的既有信号处理结果保留。
- 在 worktree 加入 API 回归，先复现错误回调被接受，再修复：账号任务明确回报非零 DOM，或携带 rows 却回报零 API 时，在 staging/入库前 failed，并返回 `rejected=untrusted_bootstrap_source`。有效 API partial、真实空、终态幂等与 frozen-final 恢复沿用原语义。这个校验拦截明确负面 provenance，不宣称能认证没有 provenance 字段的任意旧客户端。
- worker 停止期间，调用**正式部署代码**及**正式配置和数据目录**的 `SoulEngine.analyze_events()` → `build_initial_profile()`，精确重处理原有效任务 `fed7f6e0-de22-4474-8921-d03e886e53e4` 的 7 条 follow。该路径是显式人工恢复，不伪称原 worker 自动重放；不重置消费游标，不重复插入事件。
- 两次真实 Codex 调用成功：`soul.preference.chunk` 5358 tokens，`soul.profile_build` 330250 tokens（包含原画像上下文，并非仅 7 条记录的大小），总耗时 41.76 秒。`init_cognition_persist`、`init_preference_build`、`init_soul_build` 三条账本均 success；preference、soul、soul_profile JSON/Markdown 已写回。用户手动 profile_overrides 的 SHA 未变。
- 最终正式 API health / runtime-status / profile-summary 均 HTTP 200，worker_running=true。新 API 校验及 Codex 传输已由重启后的正式部署加载。临时浏览器与带 Cookie 的 profile 已删除。

验证：Codex 13 项通过，最终 API 24 项通过；实际部署快照的两组回归合计 37 项通过。Ruff 通过，MyPy 366 个源码文件通过。额外全量 pytest 在 1185 项通过、运行 308.45 秒后主动中断；该全量运行没有完成，不能标成全量 PASS，本分支仍未提交/合并/发布。

常用 Chrome 扩展更新仍需用户操作：浏览器工具的 URL 安全策略拒绝 `chrome://extensions/`，不通过其它方式绕过该限制。已请求用户停用旧 OpenBiliClaw 并从本 worktree 的 extension 目录加载修复版。临时浏览器仍遇到抖音验证码，一次普通滑块操作被网站拒绝，没有取得本轮四类真实采集通过的证据。

## 交付状态

当前 verdict：`incremental only`。采集代码与真实隔离账号链路此前已验证；**正式后端兼容修复、旧扩展负面来源拦截和正式画像持久化恢复现已完成**。完整正式采集链路仍等待常用 Chrome 加载修复版，并重新验证抖音验证码 / 403。不能把画像恢复成功描述成所有四类采集已经恢复。验收时尚未提交/合并；随后按用户要求进入 main 合入流程，见下节。未发布商店版本。

## 合入与清理记录（2026-11-06）

用户明确要求 commit / merge main / push / 清理。修复提交为 `8611199f`；在同一 worktree 合入 `origin/main` 的 `c9ebb052`，API 文档冲突保留双方新增章节，代码自动合并后重新验证。上文真实请求与部署验收属于各自记录的代码快照，不因 Git 合入改变已有边界。

- 合并后 Ruff 与 MyPy（366 源码文件）通过；扩展完整测试 1879/1879 通过，typecheck、Chrome / Firefox 构建与各 22 项资源清单通过，两个 ZIP 完整性检查通过。首轮扩展回归因 worktree 缺少 tsc 而失败，连接既有开发依赖后重跑全套成功。
- Python 全量合并回归命令为 `PYTHONPATH=.tmp/merge-test-deps:src python -m pytest -n 6 --dist loadfile -q --disable-warnings`，完成于 918.91 秒：12265 passed / 70 skipped / 5 failed。对 5 项失败用相同源码串行复跑，CLI 30 秒超时、聊天按钮 1 秒超时及 50 轮补货 2 秒超时三项通过（共 3 passed / 2 failed，95.50 秒）；没有修改断言或放宽超时。
- 剩余两项在未合入本修复的 main `c9ebb052` 上对照复跑，同样 2 failed（52.01 秒）：`tests/test_desktop_web_issue_98_e2e.py::test_recommendations_and_runtime_recover_without_leaving_init_gate` 期望 30、实际 27；`tests/test_desktop_web_platform_recommendations_e2e.py::test_enabled_zero_stock_platform_survives_config_snapshot_retry` 缺少配置快照重试后的平台 Tab。这两份测试及相关 web 源码相对 main 均无差异，记录为已有基线失败，不把本轮描述成全量通过。
- 清理前正式服务复核：health、runtime-status、profile-summary 均 HTTP 200，worker_running=true。并行全量测试期间的一次 runtime-status 20 秒超时保留为过程记录，随后独立复核 9.90 秒成功。
- 修复版运行目录固定保留在本机 `~/Library/Application Support/OpenBiliClaw-AppReview/manual-builds/douyin-account-fix-20261106/extension`；另存 Firefox 解压产物及两份 ZIP。它们包含最新 main 的手机扫码界面改动，区别于前面真实账号测试时的归档，不伪称商店已更新。
- 完成 Git 推送后清除本次 worktree 注册、临时测试依赖和本地修复分支；原 worktree 路径仅保留指向上述固定目录的兼容符号链接，避免已加载的 extension 路径失效。正式部署快照、配置和数据库备份保留；验收脚本与日志归档到本机原私有备份目录，不进入 Git。主工作区原有 outputs / promo / tmp 与其它 worktree 不在清理范围。

本次代码交付不包含扩展商店发布，也不把仍需处理的旧扩展加载、验证码 / 403 标为已通过。
