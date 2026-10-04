# TikTok 来源验收报告

本轮日期：2026-10-04，真实 HTTP/UI 续验于 2026-10-05 完成。结论为 **incremental only**：公开发现链路已实现并有真实模型证据；登录态搜索、已安装扩展 Cookie 同步及三端完整交互仍缺验收，不能把实现完成等同于所有 required gate 通过。

## 范围与 provenance

- Integration level：`discovery-only`；账号历史、画像初始化、增量行为采集、站内收藏均明确排除，见 [contract](platform-source-contract.tiktok.toml) 和 `tests/test_tiktok_contract.py`。
- 原分支：`feat/tiktok-source`，起点 `8c4ed1a5eeef75ad8bafa6656ff219937115e4d7`，PR #275。修复 worktree：`.worktrees/tiktok-source-readiness`，分支 `fix/tiktok-source-readiness`；先合入 `d6588ce6`，再同步 main `00a193f8`（v0.3.226），保留 Ollama 修复、测试隔离及发版文档。
- 所有 Python 命令使用 `PYTHONPATH="$PWD/src" ../../.venv/bin/python`，从修复 worktree 导入；不使用主工作区的 editable 源码。原 worktree 的未跟踪文件未修改。
- 真实链路读取主工作区实际配置加载器的模型/网络设置；数据目录和 SQLite 全部改到临时目录，画像为显式的 science 合成兴趣。实际配置 chat 默认链为 `deepseek-v4-flash` / `sensenova-6.8-flash-lite`（OpenAI-compatible provider），embedding 为本地 Ollama `nomic-embed-text`（768 维）；未以桩替换模型。未修改用户配置或生产数据库。
- Chrome/Firefox 最初构建版本 `0.3.225`；同步 main 后重建为 `0.3.226`，Chrome 装载根为 `extension/`（编译脚本在 `dist/`），Firefox 产物为 `extension/dist-firefox`；树摘要见 [builds.json](testing/assets/2026-10-04-tiktok/builds.json)。本轮未安装这两份构建，不声称有 installed-app 证据。

## Gate ledger

| Gate | Applicability | Status | Evidence / remaining risk |
| --- | --- | --- | --- |
| Scope/worktree / frozen contract | required | PASS | unit/static；注册 audit `required_missing=0`，37 PASS / 11 N/A / 12 MANUAL；MANUAL 不计为完整验收通过 |
| Historical precedent + repair review | required | PASS | 本轮先复现五项回归再修复；自主代码复核。独立 Claude review 因 CLI 未登录未执行 |
| Canonical identity / storage | required | PASS | unit/static + full-pipeline；`tiktok:<id>` 和 HTTPS 视频 URL，真实 API 3 条全部正确 |
| Transport / normalizer / error taxonomy | required | PASS | unit/static；429、业务错误、缺少列表、强制 web、熔断恢复、部分失败、预算/claim 一致性测试 |
| Auth / capability readiness | required | BLOCKED | unit/static 通过；passport heartbeat 只证明会话，不能证明 search。当前环境无登录 Cookie，真实 search 未验证 |
| Browser task / MV3 recovery | N/A | PASS | 后端发现，无内容采集任务；contract 排除测试。Cookie 同步不属于浏览器任务 |
| Bootstrap / post-init incremental | N/A | PASS | discovery-only 排除测试；不把匿名推荐流当账号画像 |
| Formal discover / keyword dual-track / admission | required | PASS | unit/static + full-pipeline；planner 与 inspiration 专项测试，guest 多词转 hashtag，失败词单独 mark_failed |
| Eval / recommendation | required | PASS | full-pipeline；真实配置模型评估 4 条，3 条入池，3 条生成推荐文案，API HTTP 200 返回 3 条 |
| Config / API / status convergence | required | PASS | unit/static + config-show；已更新凭据 verified 文案，明确搜索需另测 |
| Setup surface | N/A | PASS | guidedInit=false，contract 测试 |
| Desktop / mobile recommendation rendering | required | PASS | 2026-10-05 正常 Uvicorn 后端 + 独立 Chrome 普通 UI，真实封面/推荐/跨端本地保存；见续验 |
| Desktop / mobile full actions | required | PARTIAL | 桌面来源开关、mode/region/budget 保存及过滤、两端本地保存通过；手机无来源凭据管理入口，完整动作仍未齐备 |
| Extension popup / mobile credentials | required | BLOCKED | build/assets + unit/static 通过；已安装扩展登录态交互未执行 |
| Image delivery | required | PASS | live-transport；3 个真实 cover 通过应用 fetch_cover_bytes，均为 image/*，见 covers.json |
| Image proxy DNS / redirect boundary | required | PASS | TikTok 专用路径逐跳 DoH 解析、拒绝非 global/过渡地址并固定连接 IP；22 项安全测试 + 3/3 真实代理封面下载，见 covers-pinned.json。其他来源的旧下载路径不在此证明范围 |
| Mobile deep link | N/A | PASS | HTTPS browser fallback，contract 测试 |
| Native save | N/A | PASS | TikTok local-only，contract 测试；未调用站内 mutation |
| Focused backend verification | required | PASS | 244 个专项测试通过；后增 readiness 18 项通过，图片/CLI/API/声明/版本相关 118 项通过 |
| Full backend verification | required | PASS | 代码基线 `1c632c29` 云端全量 9970 passed / 111 skipped / 0 failed；其后资料缺 ID 补丁的相关 157 项通过。最新 head 的完整状态以 PR CI 为准，分阶段证据不冒充同一次执行 |
| Chrome + Firefox tests/build/assets | required | PASS | 两目标构建、类型检查、资源检查通过；扩展测试 1536/1536 |
| Safe real E2E | required | PASS | 匿名 tag → pending_eval → 真实 LLM → 推荐 API；限于此明确切片 |
| Authenticated / expired credential E2E | required | BLOCKED | 无效测试 Cookie 的真实 passport 拒绝且不落盘已通过；有效登录/自然过期场景仍无会话 |
| State-changing E2E | N/A | PASS | 当前请求为修复公开来源，未请求站内点赞/收藏/关注；上游 mutation 为 none |
| Documentation / delivery | required | PASS | 模块、架构、CLI、隐私、第三方声明已同步；修复提交 328bd5e1，后续主线同步及图片边界提交见 PR #275 |

## 传输与故障语义

Web 为主后端，只有 auto 可以回退 yt-dlp；yt-dlp 的列表能力仍受上游 extractor 限制，不能把它当可靠的全功能降级。强制 web 在连续失败后显式失败，60 秒后允许恢复探测。429 的 Retry-After（无有效数值时 300 秒）和请求间隔在 `tiktok_request_state.sqlite3` 共享，跨客户端/进程/重启保留。

HTTP 200、业务状态成功且存在显式列表时，`[]` 才是有效空结果。错误、缺少列表、gate、限流不能被记作成功空结果；部分查询成功的候选保留，失败查询单独记录。feed 一次拉取固定最多 12 条，与每日剩余拉取次数无关。每日预算约束实际 claim 数量，ledger 记录实际执行单位。

真实匿名 tag 使用 `science`，人工注入 real coordinator 的 pending keyword。关键词自动生成本身由专项测试覆盖，本次真实模型调用证明的是评估和推荐文案链路。发布时刻使用上游 epoch，点赞/收藏/评论/分享/播放数分别映射，danmaku 不可用；`favorite_count` 与 `collect_count` 均支持。

## 命令与证据

以下命令均在修复 worktree 执行；`python` 指前述主环境 Python 加 worktree PYTHONPATH。

| Command | Exit | Evidence |
| --- | ---: | --- |
| `python scripts/audit_platform_source.py --contract docs/platform-source-contract.tiktok.toml --check --json` | 0 | [contract-audit.json](testing/assets/2026-10-04-tiktok/contract-audit.json)，只证明 registration_check |
| `python -m ruff check src/ tests/ scripts/smoke_tiktok_pipeline.py` | 0 | 无 lint 错误 |
| `python -m mypy src/` | 0 | 313 source files |
| TikTok + planner + inspiration 专项 pytest | 0 | 244 passed；后增 readiness/contract/image-cache 边界用例 67 passed |
| `npm run build && npm run verify:assets` | 0 | Chrome build/assets |
| `npm run build:firefox && npm run verify:assets:firefox` | 0 | Firefox typecheck/build/assets |
| `npm test`（extension） | 0 | 1536 passed，0 failed |
| `python scripts/smoke_tiktok_pipeline.py --config <实际配置> --output <隔离输出>` | 0 | [pipeline.json](testing/assets/2026-10-04-tiktok/pipeline.json)：4 discovered / 4 evaluated / 3 cached / 1 rejected / 768 embedding dimensions / 3 served |
| `fetch_cover_bytes` 对上述 3 条推荐封面 | 0 | [covers.json](testing/assets/2026-10-04-tiktok/covers.json)：3/3 成功 |

完整 pipeline 脚本只在临时数据库内写候选、推荐和调度 ledger，不等于 projection-free transport smoke。独立 `discover-tiktok --mode feed|tag|user|search` CLI 不初始化应用数据库、不加载画像、不调用模型、不入候选池，只在临时目录写请求节流状态；contract 的九项 forbidden sinks 适用于这个 transport smoke。不得把 pipeline 中的正常临时投影写入伪称为九项零增量；transport smoke 的 feed/tag/user 实际各取回 2 条；应用数据库摘要前后不变，临时目录只有请求时间戳 SQLite，因此九项投影均为零，见 [transport-smoke.json](testing/assets/2026-10-04-tiktok/transport-smoke.json)。此证据不是逐表 hook 计数。

## UI 证据范围及剩余前提

桌面/手机使用当前源码和真实推荐 DTO，在 loopback 的专用 fixture 服务渲染；封面字节来自应用真实下载路径。fixture 未提供 WebSocket，因此有 runtime-stream 重连控制台信息，这不算真实后端故障，也不当作全应用 E2E 通过。扩展仅构建/测试，尚未安装验收。截图：[桌面](images/tiktok-2026-10-04/desktop.png)、[手机](images/tiktok-2026-10-04/mobile.png)。

要关闭剩余 required 项，需要可用的 TikTok 登录会话及装有本次构建的浏览器，完成 Cookie 同步、passport 探针、真实关键词搜索、过期 Cookie 状态与三端凭据/设置操作；图片 DNS 边界已补齐并实测，不再是本次待办。所有公开来源实现修复可独立交付，账号行为接入属于 contract 明确排除的另一个功能范围。

## 最终复核补充

完整扫描耗时 34 分钟：5 项失败分别为旧 hashtag 断言、测试运行期间主线版本更新引发的两项版本一致性断言、推荐服务地址测试隔离和生成式第三方声明不同步。已修正断言、合入 main 的隔离 fixture，并把 signer notice 写入生成器而非手改生成结果；这 7 个相关测试文件合并复跑 178 项通过。安全下载路径与图片 API/CLI/声明/版本的 118 项测试另外通过。随后 `1c632c29` 的 [完整 CI](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/37207105480) 全绿：后端 9970 passed / 111 skipped，独立引导初始化浏览器测试 39 passed，Windows 和 Firefox 检查通过。其后资料查找缺少 ID 的补丁单独通过 157 项回归；最新 CI 状态以 PR 当前 head 为准。

TikTok 封面经 `runtime/tiktok_images.py` 处理：Cloudflare DNS-over-HTTPS 仅接收公开 CDN hostname，服从现有 network 路由；固定 IP 用 curl CONNECT_TO，Host/SNI/证书验证保留原域名。每次跳转重新执行白名单、443 端口、解析地址校验并使用新会话；DNS 失败时关闭本次下载，不回退未经验证的本地解析。正文流有 10MB 上限，拒绝后主动取消下载。真实网络最终结果见 [covers-pinned.json](testing/assets/2026-10-04-tiktok/covers-pinned.json)。此路径增加对 Cloudflare DoH 可达性的依赖，不携带账号 Cookie。

实现依据：[libcurl CONNECT_TO](https://curl.se/libcurl/c/CURLOPT_CONNECT_TO.html)、[Cloudflare DoH JSON API](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。初次本地 DNS 地址固定测试超时，随后改为沿配置代理的 DoH 解析并完成 3/3 真图复验；不将中间失败结果写成成功。

## 登录环境就绪后的执行步骤

1. 使用隔离后端配置/数据目录和独立端口，将本次构建的扩展连接到该后端。Chrome 的装载根为修复 worktree 的 `extension/`，Firefox 为 `extension/dist-firefox/`；先核对 builds.json 的版本与树摘要。
2. 在这个装有扩展的浏览器登录 TikTok，确认统一凭据端点收到 Cookie 同步并通过 passport 心跳。分别记录未登录、已验证、过期/拒绝三种状态；不以“Cookie 存在”替代验证成功。
3. 在同一隔离配置下执行 `openbiliclaw discover-tiktok --mode search --query "science experiments" --limit 3`，记录实际错误分类或条目数。再让正式 producer 消费同一短语，核对 pending keyword 的 used/failed 状态和候选来源；搜索必须保留短语，不能转 hashtag。
4. 桌面、手机、已安装 popup 分别操作 TikTok 启用、mode、预算、地区、验证/凭据管理及推荐过滤；刷新后核对后端配置与状态一致。暂存/收藏只验证本地语义，不调用 TikTok 站内收藏接口。
5. 把结果、受测 commit、安装路径与真实 pipeline 终态补回本报告；只有相应 required 行逐一 PASS，才把总体 verdict 从 incremental only 改为 complete。

资料查找终态补充：HTTP/业务成功但作者资料缺少 secUid，或话题资料缺少 challenge ID，均记录 `invalid_response`；不能把这种无法确认的响应视为“用户/话题不存在”并消费关键词。对应用户与话题反例已加入 `tests/test_tiktok_web.py`。


## 真实 HTTP 后端与浏览器续验（2026-10-05）

本轮使用 `0a65d30c` 加发现第二阶段参数转发修复，在独立 worktree 启动正常 `create_app()` + Uvicorn（loopback 18477），未注入假 API 响应、假模型或 fake SoulEngine。主项目配置通过默认 loader 读取，保留 config.local 与环境覆盖；保存到权限 0700 的临时 runtime 根后，配置/数据/日志均隔离。画像为显式合成的 science 兴趣；关闭周期调度，仅手动运行一次真实 producer，评估最小批量调为 1、等待窗口为 0，使用真实 inline evaluator 与推荐引擎。这里证明有界人工触发链路，不冒充无人值守调度验收。

实际发现一个此前测试遗漏：默认两阶段发现的第二阶段未转发 `keywords` / `keyword_ids`。TikTok 已 claim `science`，却进入 LLM 标签生成。新回归先失败（请求 unclaimed 而非 scienceexperiments），补齐参数转发后通过。影响范围内的 engine/keyword yield/TikTok 测试共 199 passed；Ruff 全 src/tests、MyPy 313 文件通过。此前 head `0a65d30c` 的全量 CI 为 9973 passed / 111 skipped；本轮补丁的 199 项是独立执行，不冒充重新全量。

[脱敏原始结果](testing/assets/2026-10-05-tiktok/live-http-e2e.json)：

- 真实 TikTok 话题请求发现 4、入候选 4；真实评估 4、入池 3、低分拒绝 1；生成文案 3；真实 TCP HTTP `/api/recommendations` 返回 3 条 TikTok。关键词 `science` 终态 used。
- 评估遇到主服务限流并自动 fallback，成功调用模型 `sensenova-6.8-flash-lite`；文案成功调用 `deepseek-v4-flash`。未更换配置模型或伪造评估。
- 桌面设置修改 mode=auto、region=US、feed budget=4，真实保存、后端读取和热加载一致；来源 off/on 保存后分别读取 false/true。以上修改仅在临时配置内。无 Cookie 测试连接正确提示访客可发现、搜索需登录。
- 桌面 TikTok 过滤、真实封面/计数/文案展示通过；桌面收藏 1 条，手机稍后再看 1 条，真实 saved API 两条均为 TikTok、sync_status=unsupported。手机可看到桌面收藏的选中状态。没有 TikTok 站内 mutation。
- 向统一 credential API 提交专用无效测试值，真实 passport probe 返回 cookie_invalid，accepted=false、persisted=false、checked=live_probe；临时数据目录没有 Cookie 文件。这是无效凭据拒绝验收，不是有效登录后自然过期的替代证据。
- Playwright 独立 Chrome profile 仅用于普通桌面/移动网页验证：两次连接浏览器 inventory 均失败，主项目及原 TT worktree 均无可用 Cookie，故没有已安装扩展、有效登录搜索、Cookie 自动同步证据。手机版现有入口只有保存同步设置，没有平台来源/凭据管理入口；不能标为手机凭据流程通过。

截图：[真实后端桌面推荐](images/tiktok-2026-10-05/desktop.png)、[真实后端手机保存状态](images/tiktok-2026-10-05/mobile.png)。本轮替代此前渲染 fixture 的网页证据，但总体仍为 **incremental only**。有效登录搜索、安装版扩展同步和完整凭据跨端流程仍待完成。临时浏览器和 Uvicorn 在验收后关闭；生产服务、配置、数据及已安装扩展未改动。
