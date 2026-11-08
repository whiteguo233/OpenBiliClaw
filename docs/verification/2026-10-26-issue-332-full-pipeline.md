# #332 正式发现入口与原模型完整链路复测

范围为 `capability-increment`：使用实际安装的开发扩展、真实抖音请求、正式 `DouyinDiscoveryProducer.produce_if_due`、原配置的模型 / embedding、共享候选准入及实际桌面页面，重新验收搜索与热榜。此前[来源修复报告](2026-10-25-douyin-discovery-resilience.md)的取数证据没有代替本轮评分或出卡。

本次指定的 Chrome 正向链路通过：**真实取回 / 新入队 / 评估各 42 条，准入 6 条，原路由生成 6 条文案并写入推荐历史，实际 HTTP / DOM 同时显示热榜 2 张、搜索 2 张**。另 2 条准入内容继续被原画像不喜欢规则过滤。整个来源仍为 `incremental only`，没有把单浏览器有界复测写成所有浏览器、无人值守调度或账号操作认证。

## 环境、来源身份与执行边界

- 独立 worktree、分支 `fix/issue-332-shared-presence`；本轮起点 `4ac8b45af2f3c75dba38e61565ffea33e6839a8b`，此前扩展源码提交 `b7d7fe77ea040005fae9c136384befcc1f93ec04`。阶段 A 使用该源码；阶段 B 加入下述持久轮换修复。精确源文件及安装脚本 SHA-256 见[脱敏证据](assets/2026-10-26-issue-332/full-pipeline-evidence.json)。本轮没有修改扩展 TypeScript 或借 reload 更新其它 checkout。
- 临时 API `127.0.0.1:58972` 与诊断 worker 是两个真实进程，共享全新隔离 SQLite / memory root，Python 导入明确指向 worktree。原 `18420` 服务持续运行。模型与 discovery 配置经项目配置加载器复制并比对一致；保留原路由、fallback、embedding、1200 秒模型 timeout、0.60 门槛、3 分钟成功间隔和来源预算。
- 使用同一个 Chrome for Testing profile、同一授权登录态及开发扩展 `oegfehdndnlolhgmbbiibkilonegcndi`。真实 `/api/extension/reload` 返回 HTTP 200 / delivered，旧 worker 关闭，新 worker 读取自身脚本摘要及 `58972` endpoint 与磁盘一致。原浏览器、商店版和账号 / Cookie / IP 没有轮换。
- 82 项真实画像兴趣保留。搜索关键词由原模型按原画像生成，不手填、不提交私有关键词。原统一关键词 planner 配置为关闭，因此本轮验证实际启用的 legacy keyword generation；不冒充 coordinator claim 路径已真实执行。
- 有界 worker 调用正式 producer：每次选择 `hot` 或 `search`，关键词数量上限 1，任务视频上限分别 12 / 6。自动循环、其它来源及自动更新停用，防止无关流量；不是完整无人值守调度认证。所有轮次保留真实间隔，没有回写任务时间或提前解除冷却。
- 每轮开始 worker 本地 presence 为 0，`is_present(0)` 从 API 进程共享记录读取到在线扩展；实际 claim / callback 都由已安装扩展完成。随后调用实际 `candidate_pipeline.drain_pending`、`drain_pending_expression_copy` 和正常 `serve`，没有伪造 callback、手工入库推荐或修改评分。
- 阶段 A 的 18 条评估全部完成后才重启 API / worker 加载修复，保留 SQLite、embedding cache 和 2 条搜索准入内容。阶段 B 评估期间没有替换 RuntimeContext。下文只读过滤诊断另开 context 时也没有在途评估。
- 本轮没有执行点赞、收藏、关注、账号 bootstrap 或发布。网页自身可能有 telemetry / 历史等请求；“未执行社交操作”不等于所有站点请求均无内部副作用。

## 新发现的正式入口问题及修复

正式 producer 的 factory 每轮重建 `DouyinPluginSearchClient`。原 6 小时热词选择记录只在客户端对象的内存中，导致普通客户端测试通过、正式入口却持续重复前两词。新增 factory 级回归先复现两轮 `[[1,2],[1,2]]`，修复后成为 `[[1,2],[3,4]]`。

现在从既有 `dy_tasks` 的 hot payload 读取近 6 小时的选择，包括 pending 预留，再与当前对象内存记录合并。新实例及重启后仍优先未选过的真实榜单词，候选不足才按原规则补回旧词。不新增 schema / 配置，不与失败退避混为一项策略，不复用旧 Cookie 客户端。

阶段 B 真实任务读取了阶段 A 留下的选择历史，三轮热榜词 ID 依次为 `2713121 / 2713035`、`2712925 / 2712071`、`2712876 / 2712819`，两两不重叠。全过程没有手工指定热词。

## 实际任务、评估和过滤

时间均为 UTC（本地日期 2026-10-26）。持续时间为 worker 单调计时，搜索 producer 耗时包含真实关键词生成；不与取整的 SQLite 时间戳混算。

| 任务 | 创建—完成 | 正式 producer 耗时 | 真实返回 / 新入队 | 原模型评估 / 准入 / 低分拒绝 |
|---|---|---|---|---|
| A 热榜 `eba44e13-2303-4363-805d-1f14d0f25f72` | 18:10:05—18:10:30 | 25.933 秒 | 12 / 12 | 12 / 0 / 12 |
| A 搜索 `a544f099-388e-4dd8-91af-afab23e4433c` | 18:15:58—18:16:06 | 13.042 秒 | 6 / 6 | 6 / 2 / 4 |
| B 热榜 `eab7e77e-fb69-4cac-ae8d-659ca99fdfba` | 18:25:32—18:25:54 | 22.036 秒 | 12 / 12 | 12 / 2 / 10 |
| B 热榜 `e5459a72-368a-4de1-8ad8-6d0feb514104` | 18:34:17—18:34:38 | 21.468 秒 | 12 / 12 | 12 / 2 / 10 |

四个任务都通过源站原生搜索响应采集，`api_pages_fetched=0`，没有为填满 cap 主动重放搜索 API。三轮热榜分别对应电竞 / 旅行（6 + 6）、水利工程 / 政治新闻（7 + 5）、乒乓球 / iPhone 测试（7 + 5）；callback 保留真实热词归属。第三轮也自然经过 factory 重建后的持久轮换。

A 热榜全部低分是原模型正常决策，不能为获得正向结果修改门槛。A 搜索两条得分 0.62 / 0.82。第二次热榜两条得分 0.62 / 0.60，文案生成并正常写入推荐历史，但实际推荐 HTTP 只返回搜索两条。只读逐层诊断确认：数据库 4 条 → 当前画像不喜欢规则 2 条 → 时效过滤 2 条 → IP cap 2 条。保留这次真实负向结果，不删除画像中的不喜欢项来放行。再进行一次有界的正常轮换，第三次热榜两条 iPhone 内容得分 0.62 / 0.68，最终通过输出规则并真实出卡。

最终 42 个 canonical 候选 ID 均唯一，状态为 `cached=6 / rejected_low_score=36`，没有遗留 pending / evaluating。逐项验证 `douyin:<id>` candidate key、`dy:<id>` 采集兼容 ID、canonical 视频 URL、非空标题 / 作者 / 封面及统一 0.60 阈值。策略名 `dy-plugin-hot-related` 沿用兼容标识，实际 `hot_collection_method=hot_word_search`，不是用旧 related 请求冒充热词搜索。

## 原模型、推荐接口与实际页面

关键词生成及评估使用原 `openai_compatible / deepseek-v4-flash`；两批文案分别使用原路由选择的 `sensenova-6.8-flash-lite`、`deepseek-v4-flash`，没有为测试更换路由。四批评估耗时 245.063 / 118.713 / 100.426 / 74.158 秒，均保留原 timeout。真实 embedding 使用原 `ollama / nomic-embed-text / 768 dimensions`，隔离 cache 从空增长至 133 个持久向量。

| 实际 usage 记录 | 调用数 | 结果 |
|---|---|---|
| `discovery.douyin.keyword_gen` | 1 | 成功生成真实画像关键词 |
| `discovery.evaluate_batch` | 4 | 42 条正常评分，准入 6 条 |
| `recommendation.write_expression` | 2 | 两批分别完成 4 / 2 条文案 |

首批文案及 `serve` 耗时 45.523 秒，初次 `serve` 返回搜索 2 条；正常推荐 GET 的薄首屏 top-up 随后将另 2 条热榜写入历史，输出边界保留画像过滤。末批文案及 `serve` 耗时 10.312 秒，再写入 2 条热榜。最终推荐历史 6 条，其中 4 条满足当前输出规则。

最终真实浏览器重新加载 `http://127.0.0.1:58972/web/`，捕获页面自己收到的 `GET /api/recommendations` HTTP 200，与真实 `#videoGrid .video-card` 和数据库逐项核对：ID、标题、文案、canonical URL、推荐历史 ID、发布时间全部一致。实际渲染 4 张卡，四张封面均加载成功（440 × 330），页面没有 `video` 元素，未捕获 page error。独立 HTTP 客户端也取得同样 4 条；没有向 DOM 填写测试卡。

| 实际可见卡片 ID | 来源 | 原评分 |
|---|---|---|
| `7700543493390732570` | 热榜 | 0.62 |
| `7700573696394800330` | 热榜 | 0.68 |
| `7588112859939228971` | 搜索 | 0.62 |
| `7631561939390505827` | 搜索 | 0.82 |

实际截图保存在本机 `outputs/issue332-20261026-full-pipeline.png`，含个性化文案不提交仓库；证据 JSON 只保留图片摘要、尺寸及逐项一致性结果。原始响应、模型输入 / 输出、私有关键词和登录态也不提交。

## 静音、普通数据与清理

四个源任务的 97 次标签页事件中，77 个源站状态全部 `muted=true / active=false`，没有 `audible=true` 记录；4 个自建任务页均在终态关闭。前台先保持扩展管理页，后切到实际桌面核验。该结论是 Chrome API 状态观察，不是扬声器声学测量。

浏览器记录 7 条原生 general-search stream，全部 HTTP 200；所有 `/aweme/` 响应合计 177 条，含页面自身请求，不能当作 177 次搜索。四个任务全部 completed，没有通过验证码绕过、账户轮换或主动制造风控来验证。一次成功运行不构成源站安全频率保证，失败退避边界仍见前报告。

最终普通 sinks 均保持 `events=0 / seen_items=0 / profile_update_ledger=0 / saved_memberships=0`，只产生本轮候选、评分、文案和推荐。原配置、原画像三文件、隔离画像三文件及原商店版 manifest / service worker 均按测试前 SHA 比对一致。

所有源任务和评估结束后停止自有 worker、关闭本轮 Chrome 会话，再停止临时 API，删除含凭据、画像、浏览器 profile、SQLite、原始响应及日志的私有临时目录。原 `18420` 服务的最终健康检查为 HTTP 200；隔离服务已经停止。源码分支、脱敏验收记录和本地截图保留，未合入 main、推送或发布。

## 验证与完成边界

- 新 factory 回归先红后绿；`tests/test_dy_tasks.py`、`test_douyin_plugin_search.py`、`test_douyin_producer.py`、`test_douyin_direct_strategy.py`、`test_douyin_discovery_service.py`、`test_api_dy_ingest.py` 合计 **113 passed**，120 条既有 warnings，14.28 秒。
- `ruff format` 四个修改的 Python / 测试文件无变化，`ruff check src/ tests/` 通过；`mypy src/` **363 files passed**；`git diff --check` 通过。
- 本轮没有 TypeScript 改动，实际安装并 reload 的三份脚本与此前已验证构建相同；没有把前阶段完整扩展测试的两项 main 既有失败改写为本轮通过。

| 本次门禁 | 适用性 | 执行 | 证据 |
|---|---|---|---|
| checkout / 导入 / 已安装脚本身份 | required | PASS | 明确 PYTHONPATH、真实 reload、自身脚本 SHA 与最终文件一致 |
| 正式 factory 轮换与 API / worker 共享 presence | required | PASS | factory 回归先红后绿；四轮 worker 本地 presence 0、共享在线 true；阶段 B 跨重启继续轮换 |
| 热榜 / 搜索原生取数与 normalizer | required | PASS | 4 个 completed 任务、42 个唯一 canonical 候选、主动 fallback 页数 0 |
| 原模型 / embedding 及原门槛准入 | required | PASS | 42 条真实评分、6 条准入、133 个持久向量、路由及配置比对 |
| 文案 → 正常 serve → 实际 HTTP / DOM | required | PASS | 6 条文案 / 历史，保留画像过滤，实际热榜 2 + 搜索 2 逐项一致 |
| 后台静音 / 任务页关闭 / 普通 sink 隔离 | required | PASS | 77 个源站状态、4 个页关闭，四类普通 sink 全为 0 |
| 范围内回归 / 原配置保护 / 私有数据清理 | required | PASS | 113 项 Python、Ruff、MyPy、hash 比对及临时进程 / 目录清理 |

本轮未实测 Edge、统一 planner 开启后的 claim 路径、完整无人值守循环、新风控挑战，不将这些写成 PASS。账号初始化、native save 与社交写入属于本次发现修复的范围外能力（`N/A`，四类普通 sink 为 0，没有发出相应产品操作），不能从本报告推导这些能力通过。CLI、依赖和安装器协议未变，无对应接口文档迁移。
