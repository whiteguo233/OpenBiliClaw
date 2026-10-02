# Web 平台来源 Phase A + Phase B URL Probe 历史验收报告

> 历史证据：本报告只记录旧 `web-discovery-mvp` worktree 于 `2026-08-13` 的验证快照；其中真公网请求执行于 `2026-08-12`。这不是完成声明。适用性只写
> `required / N/A`，执行状态只写 `PASS / FAIL / NOT_RUN / BLOCKED`。
> 它不证明当前 `chat-web-source` 分支、最新 main 合并结果或对话新增来源功能已通过；本轮验收另行记录。
> 当时静态 audit 已做到 required MISSING=0，但其 PASS 仍只代表注册点存在；
> 语义与真实 E2E 只能由下文的已执行证据关闭。

## 范围与 provenance

- Integration level: Phase A `discovery-only`；Phase B URL authoring probe
  `capability-increment`
- Contract: `docs/platform-source-contract.web.toml` (`schema_version = 2`,
  `host_policy = "recipe-allowlist"`)
- Worktree / commit:
  `/Users/white/workspace/OpenBiliClaw/.worktrees/web-discovery-mvp` /
  `86843d4313a37f2d688e81cb380a226e28a8e981`
- Python import / CLI:
  `/Users/white/workspace/OpenBiliClaw/.worktrees/web-discovery-mvp/src/openbiliclaw/__init__.py` /
  `/Users/white/workspace/OpenBiliClaw/.worktrees/web-discovery-mvp/.venv/bin/python`
- Backend bind / data / config root: fixture 与真实公网纵向 E2E 均使用
  `TemporaryDirectory` 数据库；
  focused API 测试也只使用 pytest 临时数据库。两者均未打开用户生产数据目录，
  也未绑定生产端口。
- Browser / extension ID / version: Chrome for Testing `152` 通过 Playwright 从下述
  Chrome ZIP 解包并以隔离临时 profile 安装加载；扩展 ID
  `pcijilgekbngaimchfpkkdlbgjndobfk`。MV3 service worker 与 popup 均由真实
  `chrome-extension://` origin 加载；临时 profile 与解包目录在检查后删除。
  该安装态 package/smoke 属于 Phase A 文件集，早于 Phase B Popup probe，不能当作新 UI 的
  最终 built-artifact 证据。
- Real upstream: Phase A formal discovery 使用 `https://www.djangoproject.com/rss/weblog/`，origin
  `https://www.djangoproject.com` / port 443；执行于 `2026-08-12 18:27–18:32 CST`，
  最后复核 `2026-08-12T10:33:01Z`。Cloudflare DNS-over-HTTPS 返回
  `151.101.1.55 / 151.101.65.55 / 151.101.129.55 / 151.101.193.55`，E2E resolver
  固定第一条；resolver 只替换 DNS 来源，仍执行公共 IP 校验与 socket pin。
- Phase B real upstream probe: `https://www.djangoproject.com/weblog/`；同样固定公开 A
  `151.101.1.55`，保留 global-IP 校验、socket pin、TLS SNI、Host、robots 与
  `trust_env=false`。20 秒内返回 `ready` / `page_title="News & Events"`，识别出
  `html_alternate` Feed `https://www.djangoproject.com/rss/weblog/` 与 `well_known`
  Sitemap `https://www.djangoproject.com/sitemap.xml` 两个 high-confidence candidate，各有
  3 条样例。该次执行不使用默认 system resolver；本轮 DoH curl 自身 10 秒超时，因此不把
  显式 resolver 的成功写成默认环境路径成功。
- Installed build/package path + SHA-256: Chrome
  `/Users/white/workspace/OpenBiliClaw/.worktrees/web-discovery-mvp/extension/openbiliclaw-extension-v0.3.204.zip`，
  170 files，SHA-256
  `f9d46674f128a2fc2412843068a6d808c1b3922c9d37b0fd468102b6b73b2709`；Firefox
  `/Users/white/workspace/OpenBiliClaw/.worktrees/web-discovery-mvp/extension/openbiliclaw-extension-v0.3.204-firefox.zip`，
  66 files，SHA-256
  `f06a8ecdeac297329f05396d68f07f02610a206215e058325b0169d8780e5414`。Chrome
  包已完成上述安装态 smoke；Firefox 包完成 build / asset / package 校验，未冒充安装态 E2E。
- Existing user changes preserved: yes; 本报告只记录共享 worktree 的已执行证据，
  没有打开或改写用户生产配置、凭据或数据。

## Phase A 边界

Phase A 只允许用户显式配置的 `feed / sitemap / listing` recipe。每个 recipe
同时是网络权限清单：后端只能匿名访问精确 `allowed_origins`，不得继承系统
Cookie、Authorization 或代理环境，不得把任意搜索、浏览器自动化、账号画像、
互动指标、图片抓取或站内写操作包装成“通用 Web”。

20 个精确 N/A 键为：

1. `search.integration`
2. `profile.signals`
3. `profile.incremental`
4. `profile.refresh-mode`
5. `extension.task`
6. `extension.task-marker`
7. `extension.background`
8. `extension.early-response`
9. `extension.cookie-sync`
10. `surface.setup`
11. `surface.credentials`
12. `engagement.view`
13. `engagement.like`
14. `engagement.favorite`
15. `engagement.comment`
16. `engagement.share`
17. `engagement.danmaku`
18. `media.image`
19. `media.deep-link`
20. `media.native-save`

每个键在 contract 中都有非空产品理由，并各自引用
`tests/test_web_source_contract.py` 中一个无 skip/xfail 的精确 test nodeid。
这 20 个 nodeid 已实际执行通过；它们只证明排除边界，不替代 required 能力的
实现与 E2E。

## Phase B URL authoring probe 边界

Phase B 不是新的 formal discovery mode，而是用户触发的只读 recipe authoring capability。
`POST /api/sources/web/probe` 只接受 `{url}`：先从输入冻结一个 exact origin，再按
Feed → Sitemap → deterministic listing 的能力顺序识别直接 Feed/Sitemap、HTML 同源
`rel="alternate"`、同源 `/sitemap.xml` 与可由现有 listing parser 重放的静态列表结构。
单次最多 4 个候选、每个最多 3 条样例，所有目标读取共享有界 deadline。

probe 匿名执行，不携带 Cookie、Authorization 或环境代理，不使用登录态、JavaScript、LLM、
CDP/Playwright，也不能用 redirect 扩大 origin。响应中的 recipe 只是经 compiler 自检的草稿；
桌面 Web 与 Popup 的「采用」只回填表单，必须再显式点击「添加网站」才保存。CLI
`probe-web <URL>` 同样不保存。probe 不写 `source_recipes`、Web state/seen、candidate、memory
或 profile，所以在 guided init 与 LLM degraded recovery 中仍可使用。移动端没有来源设置面，
明确不提供 probe/recipe CRUD，只继续消费已保存来源产生的 Web 推荐卡。

## 历史先例与修复复盘

- `d64b3ed6` 首次加入 `SourceAdapter`、recipe 持久化和 registry；经验是“有
  registry/CRUD”并不等于 runtime producer、scheduler、status 和推荐准入真实接通。
- `f901920b` 首次加入 CDP + LLM 的 `WebSourceAdapter`；它属于浏览器页面抽取，
  不能作为匿名、确定性、recipe-bounded HTTP 的 Phase A transport 证据。
- `3d397a8a` 的首次真实浏览器 E2E 发现两个数据真实性缺陷：JSON `null` 被
  `str(None)` 伪装为非空字符串，以及 `document.body.innerText` 丢失全部 href。
  因此本版要求类型化解析、canonical URL 身份和 fixture，而不依赖 LLM 猜测链接。
- `b6d417e2` 是一次完整来源接入先例；随后 `47580c3a` 又修复 V2EX Search 被
  inspiration 开关误关、长查询空召回的问题。经验是首次“全接入”后仍必须检查
  formal discover 的每个生成模式、真实召回和 evaluator/admission 链。
- Phase B 首轮 focused 暴露客户端 30 秒 timeout 短于后端允许的 60 秒，真实桌面
  Playwright 又暴露 probe 初始化顺序的 TDZ；客户端 timeout 收口到 65 秒并修复初始化后，
  最终联合 focused 与真实 probe→adopt-only 流程均重跑通过。中间失败只作为修复 provenance，
  不计入下方最终 gate。

## Gate ledger

| Gate | Applicability | Status | Evidence | Remaining risk |
| --- | --- | --- | --- | --- |
| Scope/worktree provenance | required | PASS | 独立 `feat/web-discovery-mvp` worktree、HEAD、import 与解释器路径已重新记录 | 最终提交后仍需记录新 commit |
| Frozen source contract | required | PASS | Phase B 已把 `web_probe.py`、用户提交 exact-origin 的只读草稿探测与零持久化边界补入同一 contract；当前 audit exit 0，contract tests 24 PASS（含 20 个精确 N/A nodeid） | 静态 contract 不代替 focused 语义测试或真实上游 |
| Historical precedent + repair review | required | PASS | `d64b3ed6`、`f901920b`、`3d397a8a`、`b6d417e2`、`47580c3a`；本次 Django RSS spike 与 fixture/parser 证据分开记录 | 单一真实 feed 不证明 sitemap/listing 的真实上游 provenance；后两者目前由确定性 fixtures 守卫 |
| Real upstream spike + redacted fixtures | required | PASS | Phase A RSS：显式 public resolver 下 HTTP 200 / 56,840 bytes / 3 条。Phase B 页面 probe：`https://www.djangoproject.com/weblog/` 在相同安全 transport 下 20 秒内 `ready`，识别 alternate Feed + well-known Sitemap 两个 high-confidence candidate，各 3 样例；`tests/fixtures/web/` 只作确定性回归 | 两次成功都使用固定公开 A `151.101.1.55`；Phase B 本轮 DoH curl 自身超时，默认 resolver 路径没有被证明 |
| URL authoring probe API / CLI / desktop / popup（Phase B） | required | PASS | 最终 Python 联合 focused 200/200 PASS；broader Web + desktop probe 332 PASS / 39 skipped；extension 1399/1399、typecheck、Chrome/Firefox build/assets PASS。真实桌面临时后端 Playwright 完成 probe → ready/样例 → adopt，server 只收到一次精确 `/api/sources/web/probe`，没有 `/api/sources` create | Popup 当前自动化已守卫 exact body、并发 fencing 与 adopt-never-saves，但最终 built extension 尚未做安装态真实后端 probe；它保留在独立真实 E2E 行，不能由本行冒充 |
| Canonical registry / identity / storage | required | PASS | audit `canonical.registry` PASS；Web focused 226 含 canonical URL/ID、跨 recipe 内容身份、recipe-scoped seen、plan reset、lease fencing 与删除清理；fixture 与真站纵向 E2E 都完成第二轮幂等，真站 candidate/seen 保持 3 | 生产进程 crash/restart 尚未执行 |
| Transport / normalizer / error taxonomy | required | PASS | audit `transport.implementation` / `content-types.registration` PASS；Web focused 226 覆盖非空 envelope 零 usable row → degraded、不提前提交 validator、bounded scan backlog、robots complexity 与 HTML 25k nodes/depth 256/512 scopes + shared text budget fail-closed；真实 Django RSS 经显式 DoH-derived resolver 完成 robots、HTTP 200、RSS MIME、56,840-byte body 与 3 条解析 | 默认 system resolver 在本机 Clash fake-IP 模式下不可用，但 fail-closed 行为正确 |
| Shared capability/auth readiness prerequisite | N/A | PASS | 唯一 required capability 为匿名 `discover`，没有 mixed/login-required capability | 不免除匿名 transport 去凭据证明 |
| Auth / credential / account resolution | required | PASS | audit 中 provider / verify / no-write / status / credentials 注册全部 PASS；`test_web_source_auth_cli.py` 验证 `no_auth` 与 `discovery_state` 正交、无 credential 且 verify 不出网；真实公网纵向 E2E 使用 canonical anonymous `SafeWebTransport` | 未接入账号能力，后续也不得把 resolver 注入扩成 auth/proxy 入口 |
| Browser task / MV3 recovery | N/A | PASS | 5 个 extension exclusion nodeid PASS；`task = "none"` | popup 配置表面仍是 required，不能混同浏览器 task |
| Bootstrap / event / init | N/A | PASS | discovery-only 不导入账号画像；`profile.signals` exclusion nodeid PASS | Web candidate admission 与 evaluator 分属下方 required gates |
| Post-init incremental lifecycle | required | PASS | Web focused 226 验证 cadence、ETag/Last-Modified/validator URL、Retry-After、partial 不推 checkpoint、plan/lease fencing、seen handoff、Sitemap 两层 clean-sweep recovery，以及 normal cursor 的 root/child URL-order topology rebase（含 newest-first head insert）；fixture 纵向 E2E 第二轮不重复入队；真站纵向 E2E 第二轮收到 304，candidate/seen 仍为 3 | 真实 crash/restart E2E 尚未执行 |
| Formal discover / keyword dual-track / admission | required | PASS | search 按 contract 为 N/A；audit `discover.formal` / `discover.modes` PASS；Web focused 226 验证 feed/sitemap/listing adapter、producer、runtime wiring、formal CLI 与 candidate handoff；隔离与真站 E2E 均经真 `DiscoveryCandidatePipeline` 入队 | 真实 LLM evaluator/admission 放在下一 gate |
| Eval / publication time / recommendation | required | NOT_RUN | focused tests 已验证不用 sitemap `lastmod` / fetch time 伪造 publication time；audit recommendation surface PASS | 未执行真实 LLM evaluator → admission → recommendation 链 |
| Config / API / status convergence | required | PASS | Phase A audit config/API/shared keys/source-status 全部 PASS；API/status/CLI/wiring 在 Phase A Web focused 226 中通过 | Phase B probe route/init/degraded 动态证据归入上方增量 gate；三端真实后端 mutation 仍分别记录 |
| CLI formal / saved-recipe preview（Phase A） | required | PASS | `discover --source web [--limit] [--force]` 复用真实 adapter/producer/candidate pipeline，并按 `ok/valid_empty/throttled=0`、`partial/disabled/no_recipes/error=1` 诚实退出；`discover-web --recipe-id` 保持单 recipe、无状态只读 preview；Phase A Web focused 226、MyPy 与 Ruff PASS | 新增 `probe-web` 的动态结果不在此行，归入 Phase B 增量 gate |
| Setup surface | N/A | PASS | `surface.setup` exclusion nodeid PASS | Web recipe 保持可选 post-setup 来源 |
| Desktop surface（Phase A recipe editor） | required | PASS | audit static PASS；`tests/test_desktop_web_card_links.py` 10 PASS；本地 Playwright 实测独立 form 关联、Listing 字段切换和 recipe/main-config dirty 隔离 PASS；Phase B 又以临时真实后端验证 probe → adopt-only 且零 create | 创建、422、启停与删除的真实后端 mutation 仍未执行 |
| Mobile surface | required | NOT_RUN | audit 静态 PASS；contract 只要求 Web recommendation card。Phase B 明确排除移动来源设置、probe 与 recipe editor，不把产品不存在的 authoring surface 记 required | 移动 Web 文字卡真实数据展示尚未记录 |
| Extension popup surface（Phase A recipe editor） | required | PASS | audit `surface.extension_popup` PASS；Phase A extension 1396/1396、typecheck、Chrome/Firefox build 与资产校验全部 PASS；Chrome for Testing 152 安装态真实加载旧 Web editor，console 0 error / 0 warning | 这些产物早于 Phase B 最终文件；probe UI 的 suite/build/安装态与真实后端往返归入增量 gate，不能由本行替代 |
| Mobile credential management | N/A | PASS | anonymous/no-credential contract；`surface.credentials` nodeid PASS | 不得渲染虚构登录入口 |
| Image delivery | N/A | PASS | `media.image` nodeid PASS；`image = "none"` | 页面 HTTP 的 SSRF 边界仍在 transport required gate |
| Image proxy DNS / redirect / SSRF boundary | N/A | PASS | 没有 image proxy 或 image host 权限 | 不覆盖正文 transport 的逐跳 DNS/redirect 检查 |
| Mobile deep link | N/A | PASS | `media.deep-link` nodeid PASS；只声明 browser fallback | 未声明 generic native scheme |
| Native save | N/A | PASS | `media.native-save` nodeid PASS；mutating actions 为空 | 无站内收藏/书签写路径 |
| Static backend verification（Phase B current） | required | PASS | Ruff format check 628 files、Ruff check、MyPy 263 source files、contract audit PASS24/MISSING0/N/A20/MANUAL11、contract tests 24、source-contract metrics 6/6 全部 PASS | 静态门与类型检查不替代全仓 runtime suite |
| Focused + full backend verification（Phase A snapshot） | required | PASS | Web focused 226 PASS；Ruff PASS；MyPy 262 source files PASS；首次 full 8412 passed / 109 skipped / 1 fixture failure，fixture 修复并 focused 40 PASS 后最终 full 8413 passed / 109 skipped / 5184 warnings | 这些数字均早于 Phase B 最终测试/实现；只关闭 Phase A，不得作为 Phase B 增量 PASS |
| Chrome + Firefox tests/build/assets（Phase A snapshot） | required | PASS | `npm test` 1396/1396；typecheck、Chrome/Firefox build/assets 与 package PASS；Chrome ZIP 完成安装态 smoke | 这些产物早于 Phase B Popup probe；只关闭 Phase A，不得作为增量产物证据 |
| Safe real E2E | required | PASS | Phase A Django RSS recipe → producer → candidate pipeline 首轮 3/3、第二轮 304/0/0。Phase B `probe_public_web_url()` 对 Django News 页面返回 ready，2 candidates（alternate Feed、well-known Sitemap）× 3 samples；两者都保留 exact-origin、robots、public-IP 校验、socket pin、TLS SNI/Host 与 `trust_env=false` | Phase B probe 的零 DB 写由 focused contract 测试定义；本次真站函数本身没有接收 DB。默认 resolver/DoH curl 本轮均未证明可用，不外推 |
| State-changing E2E | N/A | PASS | contract `mutating_actions = []`，用户未授权任何上游 mutation | 不得自行扩权 |
| Documentation / release-readiness | required | PASS | Phase B 已同步 Web/API/CLI/discovery/extension 模块、architecture/spec/README 图、docs index/HTML/diagram、privacy、Docker、changelog、contract 与本报告；最终 `git diff --check`、陈旧“任意 URL 不支持探测”文案扫描、contract audit 与 24 contract tests 全部 PASS | 尚未执行 commit/tag/publish；若后续动态数字变化，合入前需再次同步本报告 |
| Commit/version/tag/push/publish mutations | N/A | PASS | 本步骤未请求也未执行 release mutation | 最终提交由主任务统一处理 |

## 最终静态 audit 结果

`--check --json` 已以 exit `0` 完成：`PASS 24 / MISSING 0 / N/A 20 /
MANUAL 11`，`required_missing = 0`，`registration_check_passed = true`。
该结果已在 Phase B 将 `web_probe.py` 和 authoring-probe 零持久化边界写入 contract 后复跑。
由于 11 个语义项仍由验收证据人工裁决，audit 的 `fully_verified = false`；不能把
“没有静态缺口”写成“全部 E2E 已自动验证”。
旧快照中的 API models/status/credentials、source-auth provider/verify/no-write、
shared source keys、CLI、Web 模块文档、formal discover/modes、extension Popup 与
shared source status 缺口均已关闭。

11 个 MANUAL 项仍为 `identity.semantics`、`auth.identity-evidence`、
`upstream.response-contract`、`engagement.branch-coverage`、
`discover.operational-contract`、`e2e.action-boundary`、
`transport.fallback-ownership`、`events.mapping-and-scope`、
`task.runtime-bounds`、`e2e.smoke-sinks`、`manual.semantic-e2e`；静态 PASS
不会自动关闭这些项。focused 测试、隔离纵向 E2E、真站 E2E 与双浏览器 build 已覆盖其中
一部分语义，package 与 Chrome 安装态 smoke 又补充了产物 provenance；它们仍不能替代真实
evaluator/recommendation、移动端真数据卡或安装态 popup 对真实后端的 CRUD mutation。

## Transport、身份与数据契约

- Primary / fallback owner: `backend / none`
- Auth comparison: 只声明 `anonymous`；必须验证 Cookie、Authorization、userinfo、
  Referer 和环境代理均不会进入请求。
- Account resolution and mismatch behavior: 无 upstream account；recipe owner 只是本地
  provenance，不能显示为已验证账号。
- Stable identity / URL / dedupe: canonical URL -> `url-<24 hex>` ->
  `web:url-<24 hex>`；跨 recipe 以 canonical URL 全局去重，recipe id 只隔离状态。
- Upstream envelope / pagination / empty / partial / rate-limit: 已冻结 MIME、
  1..5 页、1..50 项、显式 empty selector、partial 不提交 cursor、Retry-After；
  Web focused 226 已提供确定性 feed/sitemap/listing、MIME、错误分类、分页/上限、
  lease/cursor/validator、recovery clean sweep 与 positional cursor topology rebase 的实现证据。
- Engagement availability and publication time: 六项全部 unavailable；只接受上游明确
  publication metadata，禁止用 discovery time、HTTP Date 或 sitemap lastmod 伪造；
  focused 测试已覆盖该拒绝边界。
- 公网 transport 已由显式 resolver 注入关闭：对 `www.djangoproject.com` 查询
  Cloudflare DoH 得到四个公开 A，E2E 固定首个 `151.101.1.55`；真实 Django RSS 在
  不放宽 robots/TLS SNI/Host/IP pin/SSRF 边界下
  返回 HTTP 200、RSS MIME 和 56,840 bytes，确定性解析 3 条。默认 system resolver 在
  本机仍返回 Clash fake-IP `198.18.0.5` 并被 `blocked_address` fail closed；这条失败
  必须保留，不能把显式 resolver 的成功外推成默认 resolver 在该环境可用。
- Phase B authoring identity / persistence: probe 只把用户输入编译成单一 exact origin，并返回
  最多 4 个候选、每个最多 3 条样例；candidate `recipe` 是可编辑草稿，不是 source identity、
  scheduler state 或已验证账号。`ready` 不代表保存，只有独立 `POST /api/sources` 才能持久化。

## 命令与自动验证

| Command | Exit | Summary / artifact |
| --- | ---: | --- |
| `PYTHONPATH="$PWD/src" .venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.web.toml --check --json` | 0 | PASS 24 / MISSING 0 / N/A 20 / MANUAL 11；`required_missing=0`；registration check PASS；`fully_verified=false` |
| contract 中 20 个 exact exclusion nodeid（无 skip/xfail） | 0 | 20 passed |
| `.venv/bin/pytest -q tests/test_web_source_contract.py` | 0 | 24 passed |
| `.venv/bin/pytest -q tests/test_web_public.py tests/test_web_recipe.py tests/test_web_adapter_public.py tests/test_web_source_state.py tests/test_web_producer.py tests/test_web_runtime_wiring.py tests/test_web_wiring.py tests/test_web_source_api.py tests/test_web_source_auth_cli.py tests/test_web_source_contract.py` | 0 | 226 passed；覆盖最终 robots/parser/topology/recovery/producer/API/CLI hardening |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/test_web_probe.py tests/test_web_source_api.py tests/test_web_public.py tests/test_web_recipe.py tests/test_web_adapter_public.py tests/test_web_source_auth_cli.py tests/test_desktop_web_probe.py` | 0 | 200 passed / 56 warnings in 17.25s |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/test_web*.py tests/test_desktop_web_probe.py` | 0 | 332 passed / 39 skipped / 60 warnings in 46.27s；当前全部 Web backend + desktop probe 测试 |
| `cd extension && node --test --experimental-strip-types tests/web-recipe-settings.test.ts` | 0 | 6 passed；覆盖可访问 recipe/probe UI、fixed endpoint + trimmed-only body、422 detail、并发 fencing 与 adopt-never-saves |
| `cd extension && npm test`（Phase B 当前文件集） | 0 | 1399/1399 PASS in 21.37s |
| `cd extension && npm run typecheck`（Phase B 当前文件集） | 0 | PASS |
| `cd extension && npm run build && npm run verify:assets`（Phase B 当前文件集） | 0 | Chrome build PASS；19 个 manifest scripts/WAR assets 全部存在 |
| `cd extension && npm run build:firefox && npm run verify:assets:firefox`（Phase B 当前文件集） | 0 | Firefox build/typecheck PASS；19 个 manifest scripts/WAR assets 全部存在 |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m ruff format --check src tests`（Phase B 当前文件集） | 0 | 628 files already formatted |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m ruff check src tests`（Phase B 当前文件集） | 0 | All checks passed |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m mypy src`（Phase B 当前文件集） | 0 | Success: no issues found in 263 source files |
| `PYTHONPATH="$PWD/src" .venv/bin/python scripts/source_contract_metrics.py --check`（Phase B 当前文件集） | 0 | 6/6 metrics PASS |
| `openbiliclaw discover --source web ...` 本机实跑（参数/exit 未随结果记录） |  | 不再报 unknown source；到达项目通用 runtime 配置门后因本机缺 LLM key 退出，未执行上游取数 |
| `.venv/bin/pytest -q tests/test_desktop_web_card_links.py` | 0 | 10 passed in 0.15s |
| 本地 Playwright 桌面表单检查（浏览器版本与命令未随结果记录） | 0 | form 关联、Listing 切换、main config / recipe dirty 隔离 PASS |
| 临时真实后端 + Playwright Phase B probe（命令与浏览器版本未随结果记录） | 0 | probe → `ready` 候选/3 字段样例 → adopt；焦点进入 `webRecipeName`，表单回填且 settings 保持“没有未保存的修改”；server 只收到一次精确 `/api/sources/web/probe`，无 `/api/sources` create；截图 `output/playwright/web-probe-adopt.png`（gitignored） |
| 隔离纵向 E2E harness（`TemporaryDirectory` DB + fixture transport；命令未随结果记录） | 0 | 第一轮 1/1 并提交 state/validator/seen；第二轮 1/0，candidate 总数仍为 1 |
| `curl -H 'accept: application/dns-json' 'https://cloudflare-dns.com/dns-query?name=www.djangoproject.com&type=A'` | 0 | 四个公开 A：`151.101.1.55 / 151.101.65.55 / 151.101.129.55 / 151.101.193.55`；E2E 固定第一个地址 |
| `PYTHONPATH="$PWD/src" .venv/bin/python` inline 真站纵向 harness（`TemporaryDirectory` DB + 显式 Cloudflare DoH-derived resolver） | 0 | `2026-08-12 18:27–18:32 CST`；resolver 断言 `www.djangoproject.com:443` 后返回 `151.101.1.55`；首轮 HTTP 200 / RSS MIME / 56,840 bytes、3 discovered / 3 enqueued / 3 pending_eval；第二轮 304 / 0 / 0，candidate/seen 保持 3；state/Last-Modified/exact validator URL/scope 提交；forbidden sinks 为 0，DB 已删除 |
| `probe_public_web_url('https://www.djangoproject.com/weblog/', timeout_seconds=30, transport_factory=partial(SafeWebTransport, resolver=resolver))` | 0 | `2026-08-12` 当前 worktree，20 秒；resolver 固定 `www.djangoproject.com:443 → 151.101.1.55`，仍执行 public-IP 校验/socket pin/TLS SNI/Host/robots，`trust_env=false`；`ready` / `News & Events`，alternate Feed + well-known Sitemap 两个 high-confidence candidate，各 3 samples。该函数无 DB 参数；零 DB 写的应用契约由 focused 测试守卫 |
| `git diff --check -- README.md README_EN.md docs` | 0 | PASS |
| `PYTHONPATH="$PWD/src" .venv/bin/pytest -q --tb=short`（首次最终候选） | 1 | 8412 passed / 109 skipped / 1 fixture failure；fixture 随后改用 `build_web_recipe()`，相关 focused 40 PASS；本行保留失败历史，不冒充最终 PASS |
| `PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q --tb=short`（最终） | 0 | 8413 passed / 109 skipped / 5184 warnings in 1058.14s (0:17:38) |
| `cd extension && npm test` | 0 | 1396/1396 PASS |
| `cd extension && npm run typecheck` | 0 | PASS |
| `cd extension && npm run build && npm run verify:assets` | 0 | Chrome build 与资产校验 PASS |
| `cd extension && npm run build:firefox && npm run verify:assets:firefox` | 0 | Firefox build 与资产校验 PASS |
| `cd extension && npm run verify:assets`（Firefox build 后再次校验 Chrome 目标） | 0 | Chrome manifest / WAR 共 19 assets PASS |
| `cd extension && npm run package:only -- --archive-version 0.3.204` | 0 | Chrome ZIP，170 files，SHA-256 `f9d46674f128a2fc2412843068a6d808c1b3922c9d37b0fd468102b6b73b2709` |
| `cd extension && npm run package:firefox:only -- --archive-version 0.3.204` | 0 | Firefox ZIP，66 files，SHA-256 `f06a8ecdeac297329f05396d68f07f02610a206215e058325b0169d8780e5414` |
| Playwright installed-package smoke（Chrome for Testing 152，隔离 profile） | 0 | Chrome ZIP 解包安装；ID `pcijilgekbngaimchfpkkdlbgjndobfk`；MV3 service worker / popup 真实加载，公开 Web 面板、三策略与添加按钮可见，console 0 error / 0 warning；未执行真实 CRUD，临时 profile 已删除 |

## 真实 E2E

| Scenario | Applicability | Status | Counts / DB sample / idempotency | Diagnostic / artifact |
| --- | --- | --- | --- | --- |
| Anonymous / stripped credential | required | PASS | 首轮 real RSS HTTP 200 / 56,840 bytes / 3 parsed；第二轮 304 | canonical `SafeWebTransport` + 显式 DoH-derived public A；`respect_robots=true`、`trust_env=false`，无 Cookie/Auth/proxy fallback。默认 resolver 的 `198.18.0.5` 仍被 `blocked_address` 拒绝 |
| URL authoring probe → draft preview | required | PASS | real Django News 页面 `ready`；2 candidate × 3 samples | `html_alternate` Feed 与 `well_known` Sitemap 都是 high confidence；exact-origin anonymous transport，无 DB 参数。默认 system resolver 与本轮 DoH curl 均未证明可用 |
| Authenticated / verified identity | N/A | PASS | 0 | anonymous contract；无账号或 credential |
| Rejected/expired credential | N/A | PASS | 0 | 无 credential kind 或 write path |
| Empty vs no-observer vs partial vs rate-limit | required | NOT_RUN | real upstream 0 | focused fixture tests 已覆盖语义，但未在真实上游复现，故不报 real E2E PASS |
| Duplicate/retry/crash recovery | required | NOT_RUN | fixture：1/1 → 1/0，candidate 总数 1；真站：3/3 → 304/0/0，candidate/seen 保持 3 | validator 带回与 seen 幂等已 PASS；真实 crash/restart 尚未执行，故组合场景不报 PASS |
| `account → event → profile/init` | N/A | PASS | 0 | discovery-only，无 account/profile signals |
| `discover → pending_eval → real LLM → recommendation` | required | NOT_RUN | 真站 3 discovered / 3 pending_eval / 0 real LLM / 0 recommendation | 真 `WebDiscoveryProducer` 与 `DiscoveryCandidatePipeline` 已把 3 条 canonical Web candidate 写为 `pending_eval`，但未运行真实 LLM 与推荐投影 |
| Setup surface and actions | N/A | PASS | 0 | post-setup optional source |
| Desktop URL probe → adopt-only | required | PASS | 1 real backend probe；0 create | 临时真实后端 + Playwright：返回 `ready` 候选和 3 字段样例；采用后焦点进入 `webRecipeName`、表单回填，settings 仍显示“没有未保存的修改”；server 精确只见一次 `/api/sources/web/probe`，未见 `/api/sources` create。截图 `output/playwright/web-probe-adopt.png`（gitignored） |
| Desktop recipe CRUD / rejected input | required | NOT_RUN | 0 real backend mutation | 创建、422、启停与删除仍未执行真实后端往返 |
| Mobile surface and actions | required | NOT_RUN | 0 real data card | contract 只要求响应式移动 Web recommendation card；Phase B 明确排除移动来源设置、probe 与 recipe editor |
| Extension popup surface and actions | required | NOT_RUN | Phase A 安装态 UI smoke PASS；0 real-backend probe/CRUD | 旧 Chrome ZIP 的 MV3/popup/editor smoke 早于 Phase B；尚未对最终 probe UI 执行安装态探测、采用只回填或真实后端创建/422/启停/删除往返 |

- LLM provider / model / route: `NOT_RUN`; source fetch 本身不得依赖 LLM，正式推荐链
  仍须使用用户配置模型另行验收。
- Task-mode passive event delta: `N/A`；没有 extension task。
- Smoke projection deltas: `PASS`（隔离临时库）；Web-local `web_source_seen=3` 是预期
  provenance，禁止写入的全局 `seen_items=0`，同时 `events=0`、`content_cache=0`、
  `recommendations=0`、`profile_update_ledger=0`。本次没有 Web browser task、task-result、
  affinity、snapshot、schedule 或 memory/profile 写路径；临时数据库已删除。
- State-changing upstream actions actually run: `none`；没有授权。

## 最终结论

- Verdict: `incremental only`。Phase A 的静态注册、safe real-public 纵向链路、focused/static
  gates、extension suite/typecheck、双浏览器 build/assets/package 与 Chrome 安装态 UI smoke 已通过；
  Phase B 的 Django URL 真公网只读 probe、Python 联合 focused 200/200、broader Web + desktop
  probe 332 PASS / 39 skipped、extension 1399/1399、typecheck、双浏览器 build/assets 与真实桌面
  probe→adopt-only 也已通过。但 Phase B 当前文件集的全仓 backend full、extension 安装态和
  真实 CRUD 往返尚未全部记录，因此不能把旧 Phase A 数字外推成当前
  release-ready 声明。
- Required rows not PASS: Phase B 当前文件集全仓 backend full 与 extension 安装态证据；真实 LLM
  evaluator/recommendation；mobile 真实数据卡；extension 安装态真实后端 probe，以及 desktop /
  extension 的真实 CRUD/422；真实 empty/partial/rate-limit 与 crash/restart 组合。
- Intentional exclusions: 上述 20 个 contract keys；20 个精确 nodeid 已执行 PASS。
- Verified Phase A snapshot: audit `PASS 24 / MISSING 0 / N/A 20 / MANUAL 11`；Web focused
  226 PASS；Ruff PASS；MyPy 262 source files PASS；source-contract metrics 6/6 PASS；full backend
  8413 PASS / 109 skipped / 5184 warnings；desktop focused 10 PASS；隔离纵向 E2E
  第一轮 1/1、第二轮 1/0 且状态与幂等提交正确；真站纵向 E2E 3/3，状态、时间、
  validator、seen 与 forbidden-sink 隔离正确；extension 1396/1396、typecheck 及
  Chrome/Firefox build/assets/package PASS；Chrome for Testing 152 安装态 MV3/popup smoke PASS。
- Verified Phase B now: Django News 页面经 exact-origin anonymous safe transport 在 20 秒内
  返回 `ready`，识别 alternate Feed 与 well-known Sitemap 两个 high-confidence candidate，
  各 3 条样例；没有 DB 参数，且未放宽 robots、public-IP/socket pin、TLS SNI/Host 或
  `trust_env=false`。Python 联合 focused 200/200、broader Web + desktop probe 332 PASS / 39 skipped；
  Extension 当前文件集 1399/1399、typecheck、Chrome/Firefox build/assets PASS，targeted probe
  6/6 PASS；Ruff、MyPy 263 files、contract audit/tests 与 metrics 当前静态门 PASS；真实桌面
  probe→adopt-only 只发一次 probe 且零 create。
- Deferred work / blockers: Phase B 当前文件集全仓 backend full、安装态 Popup/真实 CRUD 证据，以及 11 个 MANUAL 中尚未由已执行
  证据关闭的项目、real LLM→recommendation、移动端真卡与安装态真实后端 UI 往返。默认 system
  resolver 在本机的 fake-IP 失败属于安全边界按设计生效：
  不得启用环境代理、放宽 DNS/robots fail-closed，或把显式 resolver 的成功冒充成
  默认 resolver 在该环境可用。
- Release mutations performed: none。

### 最后 hardening 覆盖

Phase A Web focused 226 已包含 robots terminal-anchor / bounded glob complexity、缺 URL row
fail-closed、Sitemap 两层 topology clean-sweep recovery、normal cursor URL-order fingerprint 与
newest-first head-insert rebase、index root-validator 隔离、嵌套 listing selector/text 工作量上限、
子请求 `429 Retry-After`、compiled recipe/legacy `web_extract` 隔离和 scheme/port 一致性回归。
Phase B 新增的 direct feed/sitemap、HTML alternate、well-known sitemap、listing 结构、登录墙、
private DNS、跨 origin redirect、错误净化、共享 deadline、API init/degraded/422、CLI exit、桌面
与 Popup adopt-only 已由当前 Python 联合 focused 200/200、broader Web + desktop probe 332 PASS / 39 skipped、
extension 1399/1399 与 targeted 6/6 守卫，并完成 Chrome/Firefox 当前 build/assets；桌面另有真实
临时后端 adopt-only 证据。当前全仓 full 尚未重跑，所以旧 full 仍须明确标为 Phase A 快照；
Phase B Ruff/MyPy/contract audit/metrics 已单独重跑。只有上文逐项记录的 gate 可外推为 PASS。
