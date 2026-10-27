# 抖音多热词流式发现与失败退避验收

范围为 `capability-increment`：继续修复 #332 第五阶段留下的多词热榜超时，并研究如何减少多余请求和失败后的连续尝试。基线 `7d613d9a`，生产源码提交 `b7d7fe77ea040005fae9c136384befcc1f93ec04`，分支 `fix/issue-332-shared-presence`，实现与验证均在独立 worktree。复用既有抖音来源、任务协议、normalizer 和准入契约，没有新增来源或更换模型。第五阶段已验证的正向卡片证据仍见[原报告](2026-10-25-issue-332-shared-presence.md#第五阶段人工验证后复测原生热词与正向推荐)。

本报告当前结论为 `incremental only`：本机 Chrome 的来源修复已有真实证据，完整扩展测试仍有 main 上既存的两项失败。其它浏览器、完整无人值守调度及本轮重新评估 / 出卡没有借用来源取数结果计为通过。

后续[2026-10-26 正式入口完整链路复测](2026-10-26-issue-332-full-pipeline.md)已另用新隔离数据执行原模型评分、文案、正常 serve、实际 HTTP / DOM，最终同屏显示热榜 2 张 + 搜索 2 张，并修复 factory 每轮重建客户端导致的热词轮换丢失。该新证据不改写本报告当时仅取数的范围或既存测试失败。

## 根因与变更

旧 fetch tap 对原生 `/aweme/v1/web/general/search/stream/` 调用 `clone().json()`，失败后再尝试 `clone().text()`，等待整个 body 后才把原始 Response 交给网页。实际响应包含多段 JSON，旧解析器不能正确取出视频；等 EOF 还延迟网页自身消费响应。修复后立即交还原始 Response，只在独立副本中按完整 JSON 帧采集，正确处理跨 chunk 的 UTF-8、嵌套括号和字符串转义，观察最多 4 MiB / 60 秒，取消观察副本不取消原请求。

同一份真实响应的离线回放将网络变化排除在解析器对比之外：

| 真实响应大小 | 旧解析器（`7d613d9a`） | 新解析器 |
|---|---|---|
| 2,588,144 bytes | 0 帧、0 条 | 2 帧、8 条唯一视频 |
| 3,221,120 bytes | 0 帧、0 条 | 2 帧、7 条唯一视频 |

这是**真实响应的离线回放**，不冒充额外真实请求。表中帧数指 fetch tap 实际发出的有效 discovery 帧，原始流还包含元数据帧。本轮捕获的响应都正常结束，不能据此宣称源站这几次流永不结束；未结束流的及时交还与增量取帧另外由可控 stream 回归证明。仅提交响应摘要和计数，原始 body 不入库。

热榜任务同时修复首次 ready 和词间重复首页导航，复用现有页面搜索，跨文档时只恢复 collector，不再次提交搜索；同文档 execution key 防止 SPA 重复执行。下一词只请求总额剩余量。结果到达立即使导航 generation 失效，即使 partial 仍在等待 ACK，较晚完成的注入回调也不能重新提交失败词；该竞态先复现 `2 !== 1`，修复后通过。

有效的原生结果即使少于 cap，也不再重放主动搜索 API；UI / 导航未成功不补 API。兜底只保留一个主端点，真实空、非零业务状态及 `hit_shark` 不换端点试探。主动 API 后页失败仍返回已有 items；明确限流不被正向条数掩盖。识别到已观察过的 `验证码中间页` 标题会直接失败；这不是通用验证码识别器。

发现失败时，新增 partial 必须先收到后端 2xx ACK，再提交 failed，之后不进入下一词。后端从已领取 search / hot / feed 终态推导共享冷却：普通失败 15 分钟、明确限流 / 验证信号 30 分钟，连续失败指数增加、最多 6 小时。正式 producer 在关键词 claim 前检查，共用 `DouyinPluginSearchClient` / `DyTaskQueue` 的路径和 direct fallback 都遵守，重启不清除；已有 lease / staged 修复与显式 bootstrap 继续按原协议处理。未领取的离线 pending 失败不冒充源站失败。无新增表或配置字段。

## 实际环境与边界

- 独立 Chrome for Testing、同一个测试 profile、同一已授权登录态，全程不轮换账户、Cookie 或 IP。使用用户已允许的替代浏览器和开发版扩展，未改动原商店版。测试扩展 ID `oegfehdndnlolhgmbbiibkilonegcndi`，版本仍为测试用途 `0.3.231`。
- 临时 API 为 `127.0.0.1:56625`，API 与诊断 worker 为不同真实进程，共享隔离 data root；原 `18420` 服务持续运行。Python 导入明确指向 worktree `src/openbiliclaw`，不是 editable install 指向的主工作区。
- 本轮复制原来源凭据、画像和配置。模型路由、fallback、embedding、timeout、来源日预算及 0.60 评分门槛没有为测试修改；临时环境只启用待测来源并停用自动循环。有效画像有 82 项兴趣，普通搜索取自实际画像，不提交该私有关键词。
- 实际路径为 `DouyinDiscoveryService → DouyinPluginSearchClient → dy_tasks → 已安装扩展 → 网页原生请求 → 真实 callback → normalizer → candidate pipeline enqueue`。使用有界诊断 worker，未把它写成完整 producer 调度循环；producer 的关键词领取前暂停单独由回归证明。
- 本轮 `cache=False / evaluate=False`，不调用模型、不写推荐或人为准入；只验证来源修复。前阶段热榜 6 + 搜索 3 的真实准入及 9 条文案 / 推荐是独立证据，不与本轮候选混算。无 evaluating 租约期间才重启测试 API / worker；后续仅扩展热重载。
- 未执行点赞、收藏、关注、账号 bootstrap 或社交发布。网页会产生自身正常 telemetry / 历史请求，不把所有浏览器流量说成 GET 或无站点内部副作用。

构建由 worktree `npm run build` 生成，完整 dist 复制到 `outputs/issue332-extension-review/`，通过真实 `/api/extension/reload` 加载。最终 reload 返回 HTTP 200 / delivered，观察到旧 worker 关闭，新 worker 自身读取的脚本摘要与磁盘一致：

| 文件 | 最终 SHA-256 |
|---|---|
| `dist/background/service-worker.js` | `a32bb0d0f93b35c752cde0f84e7cf9d8dc30f2be21cbdf76d0f5c39f63e0531d` |
| `dist/content/douyin.js` | `b624ec9548f5272642442103a0583e13672259e92b00e6e00238814c0ee80809` |
| `dist/main/dy-fetch-tap.js` | `e3ebcb9e48f795983023e8852bf5c6ce86a78d29c1e1b7dc49b928a6df753e4c` |

早期失败和后续成功各自的源文件 / 构建摘要分别保留在[脱敏证据](assets/2026-10-25-issue-332/discovery-resilience-evidence.json)，不把重新加载当作已部署其它 checkout。

## 真实任务与失败保留

时间均为 UTC，持续时间是客户端计时，不能与取整的 SQLite 时间戳相减混用。

| 阶段 / task ID | 执行时间 | 结果与限制 |
|---|---|---|
| A：`111162cd-1fb6-4ed9-be9a-df00aaf19547` | 14:20:42—14:21:51，70.021 秒 | 导航 / 退避已修、stream 尚未修；两词目标 12，首词保留 10、次词 0，最终 `failed / api_timeout`，失败保持 |
| 冷却探针 | 14:21:51 后，0.019 秒 | 独立 worker 读取 API 写下的真实失败，search / hot / feed 均返回 0，新任务增量 0；截止 14:36:51，不修改时间戳或提前解除 |
| B：`9aa35f92-8c83-4f7f-83f0-c19227619fcd` | 14:41:55—14:42:16，22.519 秒 | 自然冷却过期后，完全相同的两条榜单热词返回 8 + 4 = 12 条并 completed；各词内容与主题匹配，API fallback 页数 0 |
| 普通搜索：`cb5951f9-d603-4cde-bbc7-20405cf80bf9` | 14:47:17—14:47:22，5.554 秒 | 真实画像兴趣词返回 6 条、入队 6 条、completed；API fallback 页数 0，与 B 轮之间保留超过 3 分钟间隔 |
| 最终 guard 构建：`20019081-5f9d-494c-ab87-73293b4e39b4` | 15:02:23—15:02:45，22.942 秒 | 最终脚本再次完成双词热榜，7 + 5 = 12 条、全部新入队、completed；第二词只请求剩余 5 条，API fallback 页数 0 |

B 轮首词为体育热点、次词为新闻热点，真实 callback 保留各自 `sentence_id`。12 条为唯一视频；其中 6 条相对 A 新增，去重后当时 hot 候选共有 16 条。策略名 `dy-plugin-hot-related` 为既有兼容标识；实际 collector 由 `hot_collection_method=hot_word_search`、原生 stream 和按词 callback 证明。没有用兴趣搜索或无关 related 游戏视频冒充热榜。

最终轮按真实榜单轮换到另外两词，返回水利工程和相关人物的政治新闻视频，逐项保留对应词的 `sentence_id / hot_word`。部分新闻为较早的视频，只证明与关键词主题相关，不证明符合当前事件的时效要求；后续仍需原 evaluator 的时效与相关性准入，不凭来源 HTTP 成功直接出卡。

五条已捕获 native stream 均为 HTTP 200，响应中未见 `hit_shark`；此次未复现 429 / 验证页，不把 A 的 timeout 武断归因于风控。A 的 15 分钟暂停是一般失败策略，30 分钟明确拦截退避由回归覆盖，不通过主动制造封禁做压力测试。

最终 4 个真实任务均为终态（completed 3、failed 1）；保留失败部分并去重后，共 34 条 canonical 候选（热榜 28、搜索 6），均为 `pending_eval`，阈值全为 0.60。逐项检查 `douyin:<id>`、`dy:<id>`、canonical 视频 URL、标题、作者、封面均正确且非空。events、seen、profile ledger、saved memberships、content cache、recommendations、LLM usage 均为 0，三个画像 JSON 与原数据一致。

B 热榜记录 24 次任务页事件，19 次源站状态均静音且 inactive；最终热榜记录 27 次事件，22 次源站状态均静音且 inactive，两个 task tab 均在终态关闭。普通搜索先前汇总时，连同 B 热榜为 46 次事件 / 36 次源站状态，均静音且 inactive，没有 audible 记录。前台保持扩展管理页。这是 Chrome API 状态观察，不是扬声器声学测量。

## 适用门与验证

| Gate | 适用性 | 执行 | 证据 / 限制 |
|---|---|---|---|
| Worktree、导入与已安装构建身份 | required | PASS | 独立分支、明确 PYTHONPATH、worker ready、真实 reload 与脚本摘要 |
| 原生多 JSON stream 解析与不阻塞网页 | required | PASS | 两份真实响应差分回放；可控未结束流 / 转义 / UTF-8 回归 |
| 多词热榜与剩余额度 | required | PASS | B 轮同两词 8 + 4；最终 guard 构建另外两词 7 + 5，两轮均 completed，无主动 API 兜底 |
| 普通兴趣搜索 | required | PASS | 5.554 秒、6 条、completed，无主动 API 兜底 |
| 真实跨进程失败冷却 | required | PASS | A 真实失败后，三个入口 0 新任务；自然到期恢复 |
| 429 / hit_shark / 验证、有效 partial 后失败 | required | PASS（回归） | 保留有效 partial、等待 ACK、停下一词、单端点及已知验证页覆盖；本轮无主动封禁试验 |
| MV3 导航恢复与晚回调失效 | required | PASS（回归） | resume-only、SPA 去重、旧注入不能在失败 ACK 期间重发 |
| 普通行为隔离 / 候选身份 / 静音 | required | PASS | 普通行为各 sink 为 0；34 个 canonical 候选；实际 Chrome 任务页均静音 / inactive 并关闭 |
| 后端非 integration 全量 / Ruff / MyPy / config-show | required | PASS（分段＋入口复核） | 12033 项选择中 11987 项最终通过、46 skipped；分段脚本造成的 2 次 spawn 失败及标准入口复核分别保留。Ruff / MyPy / config-show 通过 |
| 可选桌面浏览器 integration 基线 | N/A（本修复未改桌面） | FAIL | 完整 pytest 遇到库存 expected 30 / actual 27；main 单独同样失败，25 秒左右，与本轮来源代码无依赖 |
| 扩展回归与构建 | required | FAIL（既存两项） | 最终相关 131 项通过；全套 1857 项中 1855 通过、2 项失败；Chrome build / TypeScript / 22 项资源检查通过 |
| 本轮模型评估、推荐文案与桌面卡片重测 | required（完整发现链路） | NOT_RUN | 当前来源修复不重复模型请求；既有第五阶段真实 9 条正向链路单独保留 |
| 其它浏览器、打包客户端与完整无人值守调度 | required（全来源验收） | NOT_RUN | 未拿本机 Chrome 有界入口扩展为全端证明 |
| 上游状态写入、merge / 发布 / 长期生产部署 | N/A（本轮范围） | — | 当前请求为修复与研究；没有这些动作的请求，不宣称已完成 |

命令在 worktree 执行，Python 使用共享虚拟环境并显式 `PYTHONPATH="$PWD/src"`：

```bash
python -m pytest -q tests/test_dy_tasks.py tests/test_douyin_plugin_search.py \
  tests/test_douyin_producer.py tests/test_douyin_direct_strategy.py \
  tests/test_douyin_discovery_service.py tests/test_api_dy_ingest.py
python -m pytest -q
python -m pytest --collect-only -q -m 'not integration'
python -m pytest -q tests/test_shared_presence.py
ruff check src/ tests/
mypy src/
openbiliclaw config-show
cd extension
node --test --experimental-strip-types tests/dy-hot-navigation.test.ts \
  tests/dy-content-script.test.ts tests/dy-fetch-tap.test.ts tests/dy-task-dispatcher.test.ts
npm test
npm run build
```

`openbiliclaw config-show` 实际通过 worktree 导入的 Typer `app()` 执行，因为共享虚拟环境的 console entry 默认指向原工作区；输出只在私有临时目录验证，退出码 0。相关 Python 111 项、Ruff、MyPy 363 文件退出码均为 0。最终扩展相关 131 项退出码 0；全套退出码 1，仍为 main 已复现的 `all visible clients hydrate and reconnect the pending-confirmation badge` 和 `settings tabs use stable compact panels`，未修改对应产品代码来迁就旧断言。Chrome 构建退出码 0。

包含可选浏览器测试的 `pytest -q` 在 1273.53 秒后确认旧库存断言失败，随后停止可选轮次，记录为 1 failed、3464 passed、2 skipped、退出码 2，不能写成整仓通过。失败用例在 main 上独立运行同样得到 expected 30 / actual 27（24.70 秒、退出码 1），与本轮无关的静态页面 / 用例相对 main 均无 diff。后端非 integration 选择共 12033 项；对照相同 collection 的完整 nodeid 清单，已通过的前缀为 3420 项，其余 8613 项在新进程继续，避免重复执行前缀。两段结果与选择摘要分别保留，跳过 / deselect 不作为真实账号或平台功能通过的证据。

后半段首次结果为 8565 passed、2 failed、46 skipped、1198.01 秒、退出码 1。两条失败均为 `test_api_websocket_unblocks_spawned_worker_and_task_claim[False/True]` 在 30 秒内未收到子进程响应；临时分段脚本初版缺少 `if __name__ == "__main__"` 保护，spawn 导入入口时会重复进入 pytest。修正该测试入口后，采用标准 `python -m pytest -q tests/test_shared_presence.py` 串行复核全文件，18 passed、9.75 秒、退出码 0。没有修改产品代码、测试用例或 30 秒阈值。初次失败和退出码仍保留，不能把原后半段运行写成退出码 0。

按精确 nodeid 去重后的最终后端结果为：前缀 3420 项加后半段首次通过 8565 项，再加标准入口复核通过的 2 项，共 11987 项通过；另有 46 项跳过，合计 12033。其它 16 项 shared-presence 复核不重复加到这个总数。没有声称一次不间断的完整 `pytest` 全绿，原可选桌面及扩展两项旧断言仍为失败。

## 官方资料与可采用策略

1. 官方 OpenAPI 调用约定要求限流后不要立即重试、采用有界指数退避；各 API / 应用的实际配额应在对应控制台查看。[OpenAPI 接口调用约定](https://developer.open-douyin.com/docs/resource/zh-CN/local-life/develop/preparation/openapiinterfacecallconvention)、[官方 FAQ](https://open.douyin.com/platform/resource/docs/common-question/faq)。该文档属于生活服务 OpenAPI，**不能把其中的频率当作抖音消费网页的安全阈值**。本轮 15 / 30 分钟及 6 小时是工程选择，不是官方保证。
2. 对当前浏览器集成，减少重复首页导航、优先使用网页已有响应、限制每轮条数、避免为填 cap 追加请求、失败后串行暂停，是可以直接落实的减量措施。识别到验证码 / 登录拒绝时停止自动发现并通过正常流程恢复，不用重启或其它发现 transport 继续尝试。保留已有成功间隔和日预算；为测试不会提高预算。
3. 长期接入先评估开放平台是否提供所需授权能力及配额，不能假设它提供任意网页搜索。现行[抖音用户服务协议](https://www.douyin.com/agreements/?id=6773906068725565448)页面标注 2026-02-13 更新、2026-02-20 生效，相关条款限制未经许可的自动获取、抓取及垂直搜索等使用。降低频率不等于取得授权；没有查到可靠的网页“免风控频率”。

本轮未实现指纹伪装、账号 / IP / Cookie 轮换或验证码绕过。真实成功证明此环境下本次任务可完成，不代表以后不会出现限制。

## 清理与交付

最终任务全部终态后，临时 API、诊断 worker 与独立浏览器均已退出；私有临时目录中的配置、Cookie、登录态 profile、画像副本、SQLite、原始响应与浏览器日志已删除。原 `18420` 健康检查仍为 200，原配置摘要、三个画像文件及原商店包 manifest / service worker 摘要不变。主工作区原有 `outputs/`、`promo/` 未跟踪目录未修改。

只保留脱敏报告 / JSON、公共扩展构建和此前本地卡片截图；不提交配置、凭据、原始日志或个性化搜索词。最终测试目录为 `outputs/issue332-extension-review/`，新包为 `outputs/issue332-extension-resilience-review.zip`，最终源码提交由包内 `BUILD_INFO.json` 标识，文件 SHA-256 与真实运行摘要一致。旧 hot-word / muted ZIP 保留历史来源。修复没有合回 main、替换商店包、长期部署或发布；无版本 / 依赖 / 安装流程变化。

新包 1,693,690 bytes、190 文件，SHA-256 为 `0f5605d225a9c330191c1b04c034ef4ccba16759b6cb06d365129eb90aed5559`。ZIP CRC、包内脚本摘要、BUILD_INFO 提交及 6 个生产文件的 committed blob 摘要均已核对。测试 endpoint 已退役，包内不含连接凭据或登录态。
