# 动态 Web Discover 来源验收

续建进度见[2026-10-22 网站与 RSS 完成验收](testing/2026-10-22-dynamic-sources-completion.md)；最新真实浏览器聊天、Luna 评分及 7 张来源名推荐卡结果见[同日哔哩轻小说验收](testing/2026-10-22-bilinovel-chat-e2e.md)。下文为历史验收，不能代替续建后的结果。

日期：2026-10-02 至 2026-10-03。范围：`capability-increment`，发现能力与登录接管；不包括账号历史导入、互动事件或站内发布/收藏。契约：[dynamic-web](platform-source-contract.dynamic-web.toml)。兼容的公开 HTTP recipe 仍遵守 [web](platform-source-contract.web.toml)。

以下主体记录历史 HTTP / Playwright 验收：真实网站自然语言建源、持久化后无模型重复执行，以及完整服务的聊天审批、定时发现、真实模型评估和重启保留。此前独立 Feed 推荐与反馈证据仍单列，真实账号及目标安装环境不能以 fixture 代替。完整服务的操作与隔离说明见[同日复验](testing/2026-10-03-dynamic-source-live-e2e.md)。

2026-10-22 根据用户要求，新普通网页默认切换至真实 Ego Lite 原生执行器，禁止用
Playwright 验收替代。当前桥接正在验证，官方宿主首次设置仍待用户完成；历史成功
不提升为 Ego 成功。新增执行器的证据与未完成项单列于
[Ego Lite 验收记录](testing/2026-10-22-ego-lite-discovery-e2e.md)。

## 工作区与数据边界

- 分支：`feat/dynamic-discovery-sources`。
- worktree：`.worktrees/dynamic-discovery-sources`；从 `58da9380` 接手公开 Web 对话订阅，再以 `fe5ec41e` 整合 main `5daad72c` 的聊天生命周期。
- Python：主工作区 `.venv/bin/python`，所有命令设置 `PYTHONPATH="$PWD/src"`；真实运行已打印并确认导入位于当前 worktree。
- 未写入用户生产数据库、记忆或配置；没有合入 main、部署、发布或推送。
- 浏览器 fixture、API 与真实 Feed 验收使用独立数据目录；浏览器执行器另使用独立 profile。真实模型验收从当前配置读取 provider/model/route 和既有评分门槛，在构造服务前把 `data_dir` 重定向到临时目录，仅使用合成 Django/Python 兴趣画像，不导入真实用户画像。
- 已获准的正式生命周期与推荐 E2E 可以在隔离目录写草稿、来源、流程版本、运行/调度/seen 状态、候选、内容缓存、推荐文案、embedding 缓存、LLM usage、合成记忆初始化和合成画像，以及测试推荐的本地反馈事件。这些写入是验证正式路径的一部分；没有向网站发布、关注、收藏或保存。
- 契约 `smoke.sinks` 的零投影约束描述 `smoke_only` 预览，不适用于上述明确获准的隔离正式 E2E。创建持久草稿本身也不是零写入预览。合成画像和本地推荐反馈不构成网站账号历史导入或网站互动事件采集能力。
- 推荐 E2E 的持久证据根为 `/tmp/dynamic-source-recommendation-e2e-xv6pouwn/`：`data/smoke.db`、`data/embedding_cache.db` 及脱敏日志；`MemoryManager.initialize()` 仅指向该根下的 `data/memory`。证据确认 `production_data_written=false`、`config_write_attempts=0`。API 使用真实 ASGI 路由及隔离依赖注入，不启动服务 lifespan，也不代理到现有生产 API。

## 已实现的产品路径

`聊一聊 → prepare_discovery_source → 持久草稿与试跑 → 登录接管（需要时）→ activate_discovery_source 审批卡 → 来源实例与流程版本 → 既有 quota/关键词/候选评估 → 推荐池`。

`custom_<uuid>` 是独立配比与来源归属；`source_platform=web` 和 canonical URL 内容 ID 保持稳定。同站默认追加流程，可以拆成多个来源；重复内容只有一个库存归属，同时保存多来源证据。浏览器只持久化不含代码、凭证的受限流程，账号会话由本机或服务器上的私有 profile 保存。

试跑不占库存份额；审批前不启用调度。启用事务覆盖来源创建、版本发布及草稿幂等标记。周期任务固定版本、参数与会话引用；停用/归档/版本变化阻止旧任务继续提交。入队异常或容量不足不能把未接受的条目标记为 seen。登录失效暂停对应流程；修复在原目标与范围内有界试跑，成功后才发布新版本。

## 验证记录

| 项目 | 适用性 | 执行 | 证据与边界 |
| --- | --- | --- | --- |
| 合并后的旧聊天与公开 Web 回归 | required | PASS | 111 tests，覆盖流式生命周期、审批、skills、来源 API |
| 动态 registry / 独立配比 / 双轨关键词 / OpenClaw | required | PASS | 最新广泛组合 643 tests / 55.70s，覆盖 quota、候选 admission、关键词、inspiration、OpenClaw、Agent 审批、身份与 Web 状态；命令清单 `/tmp/dynamic-source-backend-final-broad.sh`，日志同名 `.log` |
| 来源发布原子性、重复确认、停用 fence、入队重试 | required | PASS | 生命周期初组 11 tests；最后补充 repair 试跑期间停用不能 publish、100 次之后关键词仍轮换，组合 35 passed / 7.02s，`/tmp/dynamic-source-last-lifecycle.log`；队列满后重试可接纳 |
| stale / superseded 运行历史结算 | required | PASS | 新增 6 个边界 case，相关组合 61 passed / 11.17s，`/tmp/custom-workflow-run-history-green.log`，过期或被替换的运行保留准确终态 |
| 真实 SSE 审批卡 | required | PASS | 从真实草稿生成可读摘要；未批准不启用、拒绝不建源、重复批准不重复创建 |
| 全局静态检查 | required | PASS | 本轮 Ruff check 通过、MyPy 318 source files 无问题、format check 768 files 已格式化；最后登录优先级修复后全局再次通过。此前首次 format check 指出的 `api/app.py` 已格式化，原日志保留 |
| 全量 Python 回归 | required | PASS（含独立重跑） | 本轮单次 10284 passed / 68 skipped / 8 failed，2694.04s，`/tmp/dynamic-source-pytest-full-postfix.log`。6 项配置路径冲突在临时 cwd 独立重跑通过；2 项运行中源码行号变化导致的 inspect 错位，在新进程重跑两完整文件 21 passed。原收集 10360 项最终 10292 passed / 68 skipped，无未解决失败，但不称原单次全绿。首次 34 项失败的历史与修复保留下表 |
| 自定义来源 / 安装器 / 浏览器组合回归 | required | PASS | 141 passed / 57.42s，`/tmp/dynamic-source-acceptance-final-tests.log`，覆盖 custom、installer、compiler、executor、install 与 proc-no-window；后续生命周期组合及真实结构观察建源另列，不与独立 audit/contract 64 项混算 |
| 最终改动后的发布前回归 | required | PASS | 122 passed / 57.66s，`/tmp/dynamic-source-release-check-tests.log`；覆盖自定义生命周期/历史/日期/审批/契约、compiler/executor/browser install、Windows no-window、packaging extra/installer。与此前 141 等组合有重叠，不累计为独立覆盖数，也不等同全量重跑 |
| 本轮最终冻结代码回归 | required | PASS | 生命周期、真实 Chromium executor、惊喜来源身份三个完整文件 48 passed / 13 warnings / 63.93s，`/tmp/dynamic-source-final-frozen-targeted.log`；包含资源失败与登录墙/待授权跳转共存时优先接管、纯空白 418 失败、总预算后 profile 复用，以及旧/动态/停用后缓存的所有惊喜出口归属。guided-init 独立 39 passed / 95.90s，`/tmp/dynamic-source-guided-init-ci.log` |
| 桌面 / 移动 / popup 共享组件 | required | PASS | 本轮 Web JS 142（含接管页 5），`/tmp/dynamic-source-live-ui-tests.log`，计数修复后共享组件 12 项通过；扩展最终完整串行 1539 passed / 0 failed，72.96s，`/tmp/dynamic-source-live-extension-final.log`；typecheck/build/preflight 19 项。首轮串行 1538 passed / 1 failed 为 native-save 1000ms 时序用例，整个文件独立重跑 33 passed，再次全套通过；未改断言或超时阈值。更早并发轮次的 150ms 失败另保留原记录 |
| 渲染及操作 | required | PASS | 隔离 fixture API 的桌面与 360px 移动比例、间隔、启停、标签和接管跳转；`output/playwright/dynamic-sources/`。这不是安装态扩展或真实网站流程证据 |
| 登录接管页面 | required | PASS（隔离 UI） | 真实 Chrome 操作真实 `source-login.html`，后端为合成 fixture：1280/360px、两个授权按钮、单个 `login_origin` POST、两次填入后本页值为空、一次 resume 为 ready；详见下文 |
| Django 官方真实 Feed 建源与试跑 | required | PASS | 系统 DNS/TLS 读取 `https://www.djangoproject.com/rss/weblog/`，ready 后启用独立来源，份额 2 |
| 真实聊天界面 → 审批 → 完整服务定时发现 | required | PASS | 真 `create_app` / runtime / Agent / SSE / 网站与模型；桌面批准后建源，份额 2、5 分钟，连续四次自然 due（含一次重启后）；第一次 10 条入队，之后 3 次 not_modified，seen 与候选保持 10。持久根 `/tmp/dynamic-source-live-product-qsdnjsn7`，未使用 force 或改时钟 |
| 同一完整服务的真实评估与重启 | required | PASS | 10 evaluated → 2 cached / 8 rejected，原门槛；实际评估回退模型 sensenova-6.8-flash-lite，673.75 秒，保留限流/JSON/输出长度重试。重启前后来源、流程、cursor SHA、seen、两条缓存均一致；`restart-verification.json`、`real-evaluation-result.json` |
| 真实界面停用后停止发现 | required | PASS | 06:30:43Z 移动页停用，完整服务继续至 06:34:50Z，超过原 next-due 90.24 秒且跨过一个 60 秒 tick；runs 仍 4 / seen 10 / cache 2。`disabled-one-tick-after-due.json`，没有用停进程代替停用验收 |
| 真实 Feed → LLM / embedding → 入池（早期独立轮次） | required | PASS | 10 fetched / 10 retained / 10 evaluated / 9 admitted，DeepSeek V4 Flash + Ollama nomic-embed-text；146.62s，门槛未修改。`/tmp/dynamic-source-live-eval-10-final.log`、`/tmp/dynamic-source-live-eval-10.json`；与下一行不是同一运行 |
| 真实 Feed → 评估 → 文案 → 推荐 API / 反馈（独立完整后段） | required | PASS | 10 fetched / retained / evaluated → 7 admitted → 5 durable copy → API 首次 5 张卡；规范身份和本地 feedback 幂等已验。`/tmp/dynamic-source-recommendation-e2e-xv6pouwn/evidence.json`；详见下文。此行的推荐后段与下方浏览器建源是独立运行证据 |
| 首次小批次真实评估 | required | PASS | 10 fetched / 3 retained / 3 evaluated / 0 admitted，原因是低相关分数；不将抓取成功写成入池成功 |
| 真 Chromium 执行器 / fixture 登录持久化 / SSO / 越界 | required | PASS（受控网站） | 浏览器组合 114 tests；实际 executor 访问本地 fixture，覆盖登录后重开复用、清 Cookie 后 `login_required`、显式人工 SSO、资源/内容权限区分与越界阻断。不是接管页的 mocked API，也不等同真实外站账号；详见下文 |
| Hugging Face Papers 动态检索 | required | BLOCKED | 真实 Chromium 访问 `https://huggingface.co/papers`，11.31s 后 `Website did not load before the browser timeout`，模型调用 0、样例 0、未启用、未登录；`/tmp/dynamic-browser-hf-smoke.json`。不能记为未尝试或抓取成功 |
| Django 真实浏览器自然语言建源与持久复用 | required | PASS | `/tmp/dynamic-browser-django-action-schema-smoke.json`：真实 Chromium / DNS / TLS，DeepSeek V4 Flash 单次调用 → 严格编译 → trial ready 5 条 → 隔离启用（2 份 / 30 分钟 / version 1）→ fetch 5 条；DB 关闭重开且无 LLM 服务仍 fetch 5 条、计划 SHA 相同，`persisted_repeat_passed=true`；42.94s，无真实登录或生产写入。历史失败轮次保留，详见下文 |
| 真实浏览器关键词变化与持久流程复用 | required | PASS（单页前 5 条） | `/tmp/dynamic-browser-django-query-top-five-smoke.json`：自然语言明确每轮第一页前 5 条，DeepSeek 单次生成严格流程，database / authentication 各取不同的 5 条文档；重开 DB、llm_service=None 后同计划 SHA 再取原 5 条，74.11 秒。是参数化 GET 搜索，不代表 HF 或复杂 JS 页面通过；先前翻页遇到未授权 www.djangoproject.com 链接时按正确 origin 边界 degraded，没有放宽范围 |
| Python wheel 构建与资源完整性 | required | PASS | `uv build --offline --wheel` 成功；本轮产物 `/tmp/openbiliclaw-dynamic-source-final-dist/openbiliclaw-0.3.225-py3-none-any.whl` 含新服务、浏览器 helper、接管 HTML 与共享来源 JS，metadata 默认依赖 `playwright>=1.48`；最后登录优先级修复后再次重建。这不是桌面安装或容器运行验收 |
| 豆瓣小组真实登录 | required | BLOCKED（已尝试） | 小组入口转公开精选；实际登录入口核心 JS 对 Chromium 返回 HTTP 418，继而 ERR_BLOCKED_BY_ORB，无 form/input/iframe。`/tmp/dynamic-browser-douban-diagnostic-final.json` 验证产品直接报告资源失败；未输入凭据、导入 Cookie 或绕过限制，尚未到可交接登录的状态 |
| Docker 运行态 | required | PASS（Linux/amd64） | 使用已有 Docker Desktop 官方 CLI，干净 0c983b19 上下文覆盖本轮最终 5 个 Python 与 2 个前端文件后正常构建，manifest SHA 与容器安装文件一致。Chromium 1243 连续 2 次各 2 页 / 2 条；5 秒总预算在 5.06 秒取消且同 profile 可重跑；418 诊断无 query/Cookie，最终登录优先级三分支通过。默认入口、health=healthy、browser available/installed=true、登录页与两份 JS 均 200，实际静态资源含修后文案。`network=none`、无宿主端口/真实凭据；`/tmp/dynamic-source-docker-acceptance-0c983b19/overlay-manifest.json` 与 `/tmp/dynamic-source-docker-*.log`。不代表外站账号或 arm64 验证 |
| macOS / Windows 桌面安装态 | required | NOT_RUN | 本机 macOS 源码及真实 Chromium 验证不证明打包安装态；本轮没有完成两系统桌面安装验收 |
| 安装态浏览器扩展 | required | PASS（本机 unpacked） | 当前分支构建、独立 Chromium profile 安装，真实连接 18485 完整服务，正确显示网站、2 份、5 分钟及 3 条运行历史；`output/playwright/dynamic-sources-live/extension-source-history.png`。没有发布到扩展商店，不代表其他浏览器安装态 |
| Ego Lite 原生执行器（2026-10-22 更新） | required | BLOCKED（首次设置待用户完成） | 原生桥接已进入实现与协议验证；尚未完成真实宿主网站试跑及聊天入池，详见单列 Ego 记录。安装检测及历史 Playwright 记录不计入成功 |
| iframe / 弹窗依赖的登录 | 当前未支持 | NOT_RUN（成功路径） | 第一版只支持顶层页面，嵌入页/额外页面被阻止；fixture 的拒绝行为验证不能推导为嵌入式验证码、弹窗 SSO 可用 |
| 账号画像导入、增量兴趣事件、站内发布/收藏 | N/A | PASS（排除验证） | `tests/test_custom_source_contract.py` 执行生产路径，验证无账号 bootstrap/信号导入、无扩展任务/Cookie 同步、原生保存不支持及无虚构指标；17 个契约排除项均有节点依据 |
| 来源注册审计 | required | PASS（注册） | 31 PASS / 17 N/A / 11 MANUAL / 0 MISSING；`required_missing=0`、`registration_check_passed=true`、`fully_verified=false`，本轮 `/tmp/dynamic-source-live-audit.json` |

一次 10 条模型 smoke 在 240 秒外层验收预算内超时，保留原失败记录；把验收进程预算调整为 600 秒后重跑通过，未改变产品的聚合等待、模型路由或入池门槛。

最终 Docker 镜像 digest 为
`sha256:47a59d7cc2e369f23dadca46e2a087a6e3a08c75d46b8d0b9b84eb0e37e9e902`。
首次最终构建遇到 PyPI 下载 hatchling 的 SSL EOF，保留
`/tmp/dynamic-source-docker-build-final-v2-network-failure.log`；原命令重试成功，未改
Dockerfile、证书或依赖校验。先前五文件镜像的 manifest 单独保留为 `overlay-manifest-v1.json`，
不把旧镜像的通过结果充当最终七文件镜像验证。最终 wheel 与冻结源码的逐文件核对结果为
`/tmp/dynamic-source-final-frozen-verification.json`。

## 首次全量失败与修后分组回归

以下数字是各组完整定向回归的测试数，不能相加后宣称等价于重跑整套 Python。首次全量的 34 项失败分布为 DTO 5、来源工具 1、inspiration 20、CLI 1、无窗口子进程检查 1、浏览器执行器 6；原始 FAIL 日志保留。

本轮完整重跑及安装验证汇总为 `/tmp/dynamic-source-regression-installation-result.json`。
6 项路径冲突来自隔离环境变量与既有 cwd 测试假设，未改产品或断言；重跑日志为
`/tmp/dynamic-source-pytest-path-isolation-rerun.log`。另 2 项检查函数源码的测试受同轮
`refresh.py` 新增两行影响，旧 code object 行号对应了新磁盘文件中的相邻函数；冻结文件后
新进程重跑 Web / Weibo wiring 两个完整文件通过，日志为
`/tmp/dynamic-source-pytest-wiring-rerun.log`。原始全量仍记录 exit code 1，
没有把重跑结果改写成原始进程全绿；本轮新增修复另做冻结代码定向回归。

68 项 skip 包括：40 项既有 source-auth verdict 迁移边界、14 项 XHS 实网显式 opt-in、
7 项 Windows／冻结包、2 项 Bilibili 实网凭据、3 项真实 provider refill opt-in，
以及 2 项测试进程 PATH 缺少 Docker Compose 的 HTTPS gateway 测试。独立 Docker
匿名浏览器验收不能替代最后两项网关测试；明细为 `/tmp/dynamic-source-pytest-skips.json`。

| 首次失败组 | 修后结果 | 复核入口 |
| --- | --- | --- |
| API / favorite / watch-later 精确 DTO 断言 5 项 | PASS，627 tests | `tests/test_api_app.py`、`tests/test_favorites_api.py`、`tests/test_watch_later_api.py`；补齐兼容默认 `source_id/source_name=""`，不改变既有内容身份 |
| inspiration 20 项 + 工具来源识别 1 项 | PASS，56 tests | `/tmp/dynamic-source-backend-regression.log`；`tests/test_inspiration_pipeline.py`、`tests/test_agent_tool_action_contracts.py` |
| CLI 1 项 | PASS，260 tests | `/tmp/dynamic-source-cli-final.log`；`tests/test_cli.py` |
| 浏览器执行器 6 项及相关流程组合 | PASS，114 tests | `/tmp/dynamic-browser-tests-final.log`；修复实际浏览器启动/网络执行兼容后复测，未跳过执行器边界 |
| Windows 无窗口子进程静态检查 1 项 | PASS（定向复测） | `tests/test_proc_no_window.py::test_every_backend_spawn_site_hides_the_console_window`，亦包含于 `/tmp/dynamic-source-acceptance-final-tests.log` 的 141 项组合；这是 spawn 约束检查，不是 Windows 安装态验收 |
| 动态注册 audit + capability 排除 + skill 约束 | PASS，64 tests | `/tmp/dynamic-audit-final-tests.log`；`tests/test_audit_platform_source.py`、`tests/test_custom_source_contract.py`、`tests/test_add_platform_source_skill.py` |

## 真实推荐后段的独立证据

`/tmp/dynamic-source-recommendation-e2e-xv6pouwn/evidence.json` 记录实际系统 DNS/TLS、Django 官方 RSS、配置中的 `openai_compatible/deepseek-v4-flash` 与 `ollama/nomic-embed-text`。`discovery.evaluate_batch` 和 `recommendation.write_expression` 均记录成功的真实调用。10 条候选中 7 条入池、3 条因原始 0.60 门槛被拒绝；explore 0.58 与其余门槛也没有改动。

5 条文案持久化后，初次按既有多样性规则选出 3 条，真实 `GET /api/recommendations` 按正常薄页补齐返回 5 张卡。5 个 DTO 均验证 `source_platform=web`、同一独立 `source_id`、`source_name=“Django 官方博客”`，canonical `item_key` 各自对应唯一 content-cache 行。随后实际 `POST /api/feedback` 使用过期 recommendation ID 加 canonical item key，能定位正确推荐；同一请求重放两次均 200，重放 `duplicate=true`，数据库仅一条对应事件。忽略一张卡后最终 GET 为 4 张，不能把这个 4 与初始 5 混淆为丢卡。

该轮评估耗时 306.74s，文案与首个 API 验证另 24.74s，后续 API 复核 0.46s。验收脚本曾取错 `reply_style` 配置位置、查询反馈事件时遗漏 `feedback:` 命名空间，修正脚本后续验，不为此修改产品代码。保留 `initial-attempt.json`、`api-projection-attempt.json` 及最终证据。这组 ASGI 验证不包含可视化浏览器卡片，也没有跑完整后台服务生命周期。

## 真实浏览器建源与无模型重复执行

最终 `/tmp/dynamic-browser-django-action-schema-smoke.json` 使用真实 Chromium 观察 `https://www.djangoproject.com/weblog/`，再由实际 `openai_compatible/deepseek-v4-flash` 单次调用生成声明式计划，经严格 compiler 与真实试跑获得 5 条样例。最终计划以 `ul.list-news > li` 为条目、`h2 > a` 为链接，内容 origin 仍只有 Django 原站；资源 origin 单独声明。样例只包含已获得的标题和 URL，作者、发布时间为空，没有用猜测值补全。

随后在隔离临时数据库启用 1 个来源，`target_share=2`、`interval_minutes=30`、workflow version 1，首次执行得到 5 条。关闭数据库再重开，以 `llm_service=None` 构造服务，读取已保存的计划再次得到同样 5 条 URL；两次 plan SHA-256 均为 `555e47cc5d9a946ea2e2ad8aa4ed26e6c10d8de891b252c3e7434d5cb78f0063`，`persisted_repeat_passed=true`。全轮 42.94s、模型调用 1 次、`source_activated=true`、`production_data_written=false`、`login_attempted=false`。该重复执行验证持久计划不依赖再次让模型生成；并未等待完整 30 分钟调度周期，也不替代真实账号登录证明。

先前失败保留：`/tmp/dynamic-browser-django-follow-fixed-smoke.json` 为模型 JSON 解析失败；`/tmp/dynamic-browser-django-final-smoke.json` 在格式适配后为 `Collection selectors need verification`（1 次调用、44.18s）。补充 DOM 父节点结构观察与 action schema 后，同一真实网站的最终结果为上面的 PASS；不删除这些暴露产品问题的轮次。Hugging Face 仍为实际加载超时的 BLOCKED。

同日参数化搜索使用 `https://docs.djangoproject.com/en/5.2/search/?q=database`，用户意图明确
每轮只取第一页前 5 条。模型原样保留 entry URL，把变化的关键词放入 `goto ?q={query}`，
按观察到的 `#docs-content dl.search-links dt` 采集，预算为 5 条 / 1 页 / 45 秒。
实际切换 authentication 得到不同的认证文档；发布后关闭数据库，再以 `llm_service=None`
重开仍能执行，plan SHA 均为 `7fb7025a4fb902ee0fd2905e974d1a5bdfc9264d195543dc1f1808b462badca5`。
该结论指无需重新让模型生成流程，不取消既有兴趣关键词规划需要的模型调用。

此前 Python Sphinx 搜索因脚本加载超时未通过；Django 多页搜索遇到未授权的另一个
origin 链接而 degraded，实际边界被保留。HF 的网络对比证据
`/tmp/dynamic-browser-network-spike.json` 显示直接连接超时而现有 HTTP 代理可响应；
浏览器尚未实现保持已校验 IP 与实际连接一致的代理适配，因此未把 HTTP 成功算作浏览器成功。

## 登录证据分层

接管页面使用真实 Chrome 与真实工作树 HTML，API 在 `127.0.0.1:18482` 提供固定合成响应，没有运行网站执行器或连接外站账号。桌面 1280px、手机 360px 均可读；修复前长字段使 `scrollWidth=384`，修复后为 345，未出现横向溢出。两个观测登录域显示为按钮，点击只向 `/takeover` 提交一次选定的 `https://login.synthetic.example`；两个合成字段各填一次，提交后本页输入均为空；一次 `/resume` 后显示成功并提醒新来源回聊天确认。

页面还修复了 `/resume` 失败后 `closed` 状态使画面永久停止刷新的问题。5 项脚本回归覆盖显式域授权、填入前清空且失败不回填、失败重新打开接管、成功后停止轮询，以及缺草稿 ID 的初始错误；最新全部 Web JS 为 142 PASS。浏览器和 fixture 服务已关闭。证据在 `output/playwright/dynamic-sources/`：

- `login-ui-evidence.json`：页面 SHA-256、视口尺寸、请求计数与 UI-only 范围。
- `login-fixture-requests.jsonl`：填入值已脱敏，只留长度与 selector。
- `login-mobile-360.png`、`login-mobile-ready.png`、`login-desktop-ready.png`：已实际查看的授权/完成状态。

另一组 `tests/test_workflow_executors.py` 是实际 Playwright Chromium 执行器访问受控 HTTP 网站，具有不同证据含义：`test_real_browser_login_handoff_persists_session_and_detects_expiry` 执行人工表单、确认私有内容、关闭重开复用 Cookie，并在清 Cookie 后得到 `login_required`；`test_real_browser_requires_explicit_manual_sso_origin` 证明后台发现不自动走登录域、明确配置后的人工接管可完成 fixture SSO 并恢复只读采集。CDN 资源放行仍不允许作为内容导航，未授权登录域只被观测、不被跟随，私网/跨端口重定向和后台 POST 被阻止。私网 fixture 能运行来自仅测试依赖注入，不能从计划、配置或 API 开启。

这些测试尚不能证明豆瓣等真实账号、嵌入式验证码、弹窗 SSO 或任意网站登录成功。Django 的真实匿名浏览器建源已单独通过，Hugging Face 的真实加载超时仍保留在上表。

## 注册审计与剩余门槛

注册审计已适配实例 registry 的实际入口，既有固定 `web` 文件不能冒充动态来源接线。17 个 N/A 指向执行生产路径的动态契约测试，而非旧 HTTP-only 边界测试；64 项 audit/contract/skill 回归通过。Discover 在来源家族层面为 optional-credential，因为匿名流程可用；每个 `auth.required=true` 流程的登录是硬前置，过期暂停，不能用家族级可选登录绕过。

11 项 MANUAL 仍为：`identity.semantics`、`auth.identity-evidence`、`upstream.response-contract`、`engagement.branch-coverage`、`discover.operational-contract`、`e2e.action-boundary`、`transport.fallback-ownership`、`events.mapping-and-scope`、`task.runtime-bounds`、`e2e.smoke-sinks`、`manual.semantic-e2e`。本记录提供逐层证据，但不把静态审计的 `registration_check_passed` 改写为全范围完成；真实账号、尚未运行的目标环境、Hugging Face 加载与 Ego bridge 仍保留各自门槛。

## 命令与复核入口

```bash
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.dynamic-web.toml --check --json
```

注册审计只证明接线，不能关闭真实登录、浏览器或跨系统运行的验收门槛。所列 `/tmp` 日志是本次本机证据；可复现的行为测试在仓库中。
