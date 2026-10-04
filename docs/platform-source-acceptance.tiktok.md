# TikTok 来源验收报告

本轮日期：2026-10-04。结论为 **incremental only**：公开发现链路已实现并有真实模型证据；登录态搜索、已安装扩展 Cookie 同步及三端完整交互仍缺验收，不能把实现完成等同于所有 required gate 通过。

## 范围与 provenance

- Integration level：`discovery-only`；账号历史、画像初始化、增量行为采集、站内收藏均明确排除，见 [contract](platform-source-contract.tiktok.toml) 和 `tests/test_tiktok_contract.py`。
- 原分支：`feat/tiktok-source`，起点 `8c4ed1a5eeef75ad8bafa6656ff219937115e4d7`，PR #275。修复 worktree：`.worktrees/tiktok-source-readiness`，分支 `fix/tiktok-source-readiness`；合入 main 基线 `d6588ce6`，保留 Ollama 修复和文档。
- 所有 Python 命令使用 `PYTHONPATH="$PWD/src" ../../.venv/bin/python`，从修复 worktree 导入；不使用主工作区的 editable 源码。原 worktree 的未跟踪文件未修改。
- 真实链路读取主工作区实际配置加载器的模型/网络设置；数据目录和 SQLite 全部改到临时目录，画像为显式的 science 合成兴趣。未修改用户配置或生产数据库。
- Chrome/Firefox 构建版本 `0.3.225`，Chrome 装载根为 `extension/`（编译脚本在 `dist/`），Firefox 产物为 `extension/dist-firefox`；树摘要见 [builds.json](testing/assets/2026-10-04-tiktok/builds.json)。本轮未安装这两份构建，不声称有 installed-app 证据。

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
| Desktop / mobile recommendation rendering | required | PASS | 真实推荐 DTO 的隔离 HTTP 渲染 fixture，TikTok 身份、计数、推荐文字可见；不是 installed-app 全流程 |
| Desktop / mobile full actions | required | NOT_RUN | 渲染验证不覆盖所有设置、保存及凭据动作 |
| Extension popup / mobile credentials | required | BLOCKED | build/assets + unit/static 通过；已安装扩展登录态交互未执行 |
| Image delivery | required | PASS | live-transport；3 个真实 cover 通过应用 fetch_cover_bytes，均为 image/*，见 covers.json |
| Image proxy DNS / redirect boundary | required | NOT_RUN | 共享边界由已有自动测试覆盖；本轮未做真实恶意重定向/DNS 环境演练 |
| Mobile deep link | N/A | PASS | HTTPS browser fallback，contract 测试 |
| Native save | N/A | PASS | TikTok local-only，contract 测试；未调用站内 mutation |
| Focused backend verification | required | PASS | 244 个专项测试通过；后增边界用例单独 14/14 通过 |
| Full backend verification | required | NOT_RUN | 全量测试运行中，交付前更新结果 |
| Chrome + Firefox tests/build/assets | required | PASS | 两目标构建、类型检查、资源检查通过；扩展测试 1536/1536 |
| Safe real E2E | required | PASS | 匿名 tag → pending_eval → 真实 LLM → 推荐 API；限于此明确切片 |
| Authenticated / expired credential E2E | required | BLOCKED | 需要用户实际登录环境，不捏造 Cookie 或账号身份 |
| State-changing E2E | N/A | PASS | 当前请求为修复公开来源，未请求站内点赞/收藏/关注；上游 mutation 为 none |
| Documentation / delivery | required | NOT_RUN | 模块、架构、CLI、隐私、第三方声明已同步；提交/推送记录待最终补齐 |

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
| `python -m mypy src/` | 0 | 312 source files |
| TikTok + planner + inspiration 专项 pytest | 0 | 244 passed；后增 readiness/contract/image-cache 边界用例 67 passed |
| `npm run build && npm run verify:assets` | 0 | Chrome build/assets |
| `npm run build:firefox && npm run verify:assets:firefox` | 0 | Firefox typecheck/build/assets |
| `npm test`（extension） | 0 | 1536 passed，0 failed |
| `python scripts/smoke_tiktok_pipeline.py --config <实际配置> --output <隔离输出>` | 0 | [pipeline.json](testing/assets/2026-10-04-tiktok/pipeline.json)：4 discovered / 4 evaluated / 3 cached / 1 rejected / 768 embedding dimensions / 3 served |
| `fetch_cover_bytes` 对上述 3 条推荐封面 | 0 | [covers.json](testing/assets/2026-10-04-tiktok/covers.json)：3/3 成功 |

完整 pipeline 脚本只在临时数据库内写候选、推荐和调度 ledger，不等于 projection-free transport smoke。独立 `discover-tiktok --mode feed|tag|user|search` CLI 不初始化应用数据库、不加载画像、不调用模型、不入候选池，只在临时目录写请求节流状态；contract 的九项 forbidden sinks 适用于这个 transport smoke。不得把 pipeline 中的正常临时投影写入伪称为九项零增量；transport smoke 的 feed/tag/user 实际各取回 2 条；应用数据库摘要前后不变，临时目录只有请求时间戳 SQLite，因此九项投影均为零，见 [transport-smoke.json](testing/assets/2026-10-04-tiktok/transport-smoke.json)。此证据不是逐表 hook 计数。

## UI 证据范围及剩余前提

桌面/手机使用当前源码和真实推荐 DTO，在 loopback 的专用 fixture 服务渲染；封面字节来自应用真实下载路径。fixture 未提供 WebSocket，因此有 runtime-stream 重连控制台信息，这不算真实后端故障，也不当作全应用 E2E 通过。扩展仅构建/测试，尚未安装验收。截图：[桌面](testing/assets/2026-10-04-tiktok/desktop.png)、[手机](testing/assets/2026-10-04-tiktok/mobile.png)。

要关闭剩余 required 项，需要可用的 TikTok 登录会话及装有本次构建的浏览器，完成 Cookie 同步、passport 探针、真实关键词搜索、过期 Cookie 状态与三端凭据/设置操作；共享图片 DNS 安全边界仍需独立验收。所有公开来源实现修复可独立交付，账号行为接入属于 contract 明确排除的另一个功能范围。
