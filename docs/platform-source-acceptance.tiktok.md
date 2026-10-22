# TikTok 来源验收报告

## 2026-10-21 默认站点权限增量

插件默认声明 TikTok / Instagram 站点权限；Instagram 设置保存不再单独申请或撤销访问。来源启用、登录、任务权限检查及既有全量验收限制保留。此增量仅完成代码、构建和打包验证，未操作已安装扩展或提交商店；详见[权限专项验收](testing/2026-10-21-default-source-permissions.md)。

## 2026-10-14 合并收尾与后续跟踪

合并后本地验证：版本一致性、Ruff、MyPy（318 文件）通过；TikTok/账号/增量/代理/保存身份/主线画像门控与聊天相邻回归 **602 passed**；扩展 **1814 passed**；Chrome/Firefox 构建和各 22 项资源检查通过。最终主线 CI 单独运行，不用这些专项结果替代全量结论。

用户确认按“来源首版可交付、收藏暂缓、非空账号/Firefox 真测保留缺口”的范围合回 main。#275 已先合并；#286 改为 main 目标并同步最新主线，最终合并状态以两个 PR 为准。保留主线 v0.3.228 版本、聊天跟进修复与 TikTok 未发布记录；保存身份测试中双方重复修复的动态日期统一采用主线“七天前”的时间无关 fixture，不改变产品逻辑。

原来源需求 [#88](https://github.com/whiteguo233/OpenBiliClaw/issues/88) 在首版合并后关闭；收藏持久化、非空真实画像/周期增量、Firefox 安装验证集中跟踪 [#295](https://github.com/whiteguo233/OpenBiliClaw/issues/295)，不因关闭原需求而冒充全能力通过。本轮不发版、不改变生产增量开关或系统/浏览器代理。

工作区清理前，对 TikTok 四个旧 worktree 的未提交文件、本地配置、数据和构建做权限受限的本地归档并逐文件校验；不将凭据或数据上传。Chrome 已加载的 extension 目录需原址保留，仅解除旧 Git worktree 注册；合并历史和回收步骤另存本地备份。其他功能分支与 worktree 不在此次清理范围。

## 2026-10-14 全面复核：发现推荐可交付，完整账号能力保留缺口

本轮范围 **audit-only**，受测提交 `41fb9e06be1dc8150808ed25f90c827bec10bec2`。用户明确暂缓站内收藏；本轮没有任何 TikTok 收藏、点赞或关注写入，上一轮收藏持久化失败仍保留，不能改写为 PASS 或能力 N/A。观看历史沿用此前用户确认的可选范围。此前等待手机 App 收藏对照的请求不再阻塞本轮独立验收。

**结论：TikTok 的发现→评估→推荐及桌面/手机消费面可以作为首版交付；full contract 总体仍为 incremental only。** 本轮没有发现新的主要实现故障，但不能把缺少非空账号证据当成已通过。画像、周期增量与 Firefox 安装验证的限制如下；尚未合并或发布。

| 验收项 | 结果与证据范围 |
| --- | --- |
| 最新受测提交全量 CI | 四项 SUCCESS：[run 38435896568](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/38435896568)，后端 10316 passed / 131 skipped；包括 Windows 自启动、Firefox smoke build、引导初始化浏览器检查。后续文档提交的 CI 应独立查询。 |
| 本地后端与代理/账号回归 | TikTok、bootstrap、周期同步、代理隔离等 426 passed；手机相关 62 passed；Ruff 通过、MyPy 318 文件通过。需要本机监听的测试在允许监听环境执行。 |
| 扩展 | 全量 1814 passed / 0 failed；Chrome/Firefox 构建、类型检查及各 22 项资源检查通过。首次受限沙箱执行因本机监听权限失败，放开测试监听后重跑通过。 |
| 真实发现/模型/推荐 | 沿默认 loader 的实际模型与 system 代理，真实发现 4、评估 4、入池 4、文案 4；embedding 768 维，推荐 API HTTP 200 返回 4 条且 TikTok 身份正确。使用隔离数据库、显式合成 science 兴趣与 ASGI API transport，不冒充真实账号画像或本轮正常 TCP 后端 E2E。 |
| 桌面与手机展示 | 当前源码渲染本轮真实 DTO，封面经过真实下载路径。桌面 1440×1000 和手机视口 390×844 各展示 4 条，TikTok 标签、互动数据、4/4 封面正常，无横向溢出；桌面来源筛选选中，手机打开按钮新页指向对应 TikTok HTTPS 视频。fixture 不提供实时 WebSocket，重连错误不算正式后端故障，也不算实时流已验收；未用 fixture 写响应证明保存成功。 |
| 登录与账号列表 | 沿用同日已安装正式包的真实证据：会话验证成功，喜欢/收藏/关注三范围明确 empty/complete。与 10 月 10 日的真实 Cookie 同步/登录搜索证据合并判断；本轮没有新凭据或上游写入。 |
| 画像与周期增量 | 实现、调度接线、双重 opt-in、跨轮去重和非空 fixture 回归通过；真实账号没有可用非空信号，因此真实模型画像与后续非空增量仍未验收，生产周期同步没有开启。 |
| Firefox | 构建及 CI smoke 通过；安装后真实账号流程 NOT_RUN。 |
| 站内收藏 | 用户暂缓本次门禁；上轮实际失败保持为已知限制，不能宣称已修复。 |

来源审计 **46 PASS / 14 MANUAL / 2 N/A / 0 MISSING** 仅证明注册齐全；14 个 MANUAL 是语义/真实环境核对提示，不等于 14 个新 bug，也不自动证明 full contract 通过。其余真实证据与失败历史保留在下文。

[本轮脱敏汇总](testing/assets/2026-10-14-tiktok-acceptance/acceptance.json)、[真实管线结果](testing/assets/2026-10-14-tiktok-acceptance/pipeline.json)、[桌面截图](testing/assets/2026-10-14-tiktok-acceptance/desktop.png)、[手机截图](testing/assets/2026-10-14-tiktok-acceptance/mobile.png)。专用浏览器与展示服务验收后关闭；生产配置、数据及已安装插件没有变动。本轮只补验收文档，无接口、模块、配置、安装流程或架构变化。PR #286 仍叠在 #275 上，合并顺序为 #275 → #286；不以本报告替代合并后检查或发布验证。

## 2026-10-14 正式修复包热加载与输入方式对照

修复前最后一个实际安装包没有请求完成等待补丁。本轮使用既有本地 `POST /api/extension/reload`：在正常 `create_app` 隔离后端确认扩展连接，核对注册目录的 manifest 与修复分支完全一致、保留其已有未提交改动，只更新重新构建后不同的 TikTok bundle、source map 和类型声明。reload 返回 delivered=true，扩展重连 presence=1。此后正式 native-save 通过新建任务页加载本轮构建，返回 **failed / native_confirmation_not_observed**；前文「时序修复未热加载」的当前状态至此解除，但持久化仍未通过。

对原授权猫咪视频检查输入差异：真实 CDP 内层按钮点击 1 次未观察到选中/目标请求；确认仍未选中后，外层无障碍控件 Enter 1 次触发网页自然发出的精确目标 `action=1` 收藏 POST，HTTP 200、业务状态 0，图标变金色。临时被动诊断仅提取数值状态，不改写请求/响应、不重放 API。真正 reload 后探针消失、视频 readyState=4、图标再次未选中。正常来源验证接口同时确认 passport beat 会话有效；它不证明收藏权限或保存成功，不能据此判定账号/代理完全正常，也没有证据归因于 Intel CPU。

热加载后的正式三 scope 采集完成 **ok/completed**：喜欢、收藏、关注均 **empty/complete**、0/0/0。没有非空账号信号，不伪造画像，不重复无输入模型初始化、不开启生产周期同步。站内收藏继续 **FAIL**，非空真实画像/周期增量继续 **BLOCKED**。已请求用户在同账号手机 App 做保存后重开对照，以区分网页入口与账号层问题，尚无结果。

验证：Chrome build 与 22 项 manifest 资源检查通过，原生收藏相关扩展回归 **61 passed**，文档索引 **5 passed**。本轮没有功能源码修改，不冒充全量回归。隔离后端复用现有真实 openai_compatible 配置与 system 路由，生产配置/代理不变；服务和临时标签关闭，私有数据及诊断清理，已更新的正式插件产物保留。[脱敏证据](testing/assets/2026-10-14-tiktok-installed-favorite/real-e2e.json)。

## 2026-10-13 另一个公开视频的单次收藏对照

用户明确允许从当前页面另选一条可播放公开视频收藏。基于 `12007157`，复用现有登录 Chrome 与浏览器代理，选择首页实际播放的另一条视频，确认 readyState=4、白色未收藏后，通过 OpenCLI/CDP 对唯一对应按钮点击 **1 次**。图标变金色、计数 0→1；没有点赞、关注、取消收藏、请求重放或其他视频收藏。

同址 `open` 复用了原文档，页内探针仍存在，因此不计作刷新证据。随后真正 `location.reload()`，确认 navigation.type=reload、探针消失、视频仍可播放，书签恢复白色、计数归零。进入本人「收藏」分页后明确显示「你收藏的作品将显示在这里」，视频链接 0 条、目标不存在。原生持久化 **FAIL**；反例已扩展到另一条视频，不能再将问题仅归于最初猫咪视频，也没有证据归因于 Intel CPU。

本次被动 Resource Timing 与浏览器网络捕获未观察到目标收藏业务请求完成，不据此断言请求绝未发送，也不沿用上轮 HTTP 200/业务状态 0 作为本轮证据。未观察到显式登录、验证码或限流提示。未修改生产配置、代理、扩展安装包或功能代码；之前请求完成时序修复仍未热加载，不冒充最终正式 executor 验收。非空真实画像与真实周期增量仍 **BLOCKED**，没有新增信号，不重复无输入的模型初始化。

`12007157` 的 GitHub CI 四项全部 SUCCESS：test、Windows HKCU autostart E2E、Smoke-build Firefox extension、Web guided-init E2E。本次仅补验收记录，文档索引测试通过。临时标签已关闭、浏览器 lease 已释放，无 Cookie、原始 DOM/响应或账号标识入库。[脱敏对照证据](testing/assets/2026-10-13-tiktok-alternate-favorite/real-e2e.json)。

## 2026-10-13 历史改为可选、正式周期接线修复

本轮用户明确表示没有历史可跳过，因此 **默认初始化/周期任务的历史能力为 N/A（用户范围排除）**，不是把失败改成 empty。`enqueue_tiktok_bootstrap()` 默认只请求喜欢、收藏、关注；显式历史任务仍支持原协议。旧四 scope 契约结果不复用为新的三 scope。CLI 提问、进度、结果和契约文档同步。

修复一处真实周期阻塞：`RuntimeContext` 创建 `SourceIncrementalSync` 时漏传 TikTok 启用状态，导致逐源配置开启也不能安排任务。实际 runtime builder 回归先因缺少 tiktok 失败，修复后覆盖启动/异步热重载、全局与逐源 opt-in、真实 SQLite 入队、完成后推进周期、25 小时后下一轮以及关闭后取消排队。没有开启生产周期同步。

真实后端（本修复 checkout、正常 `create_app`、隔离存储、现有真实模型配置和 system 代理）+ 已安装扩展完成 **三 scope PASS（明确空态）**：`tt_like / tt_collect / tt_follow` 全部 empty/complete，0/0/0，任务最终 ok/completed。随后正式 `POST /api/init` 返回 202，复用匹配的结果并结束为 **empty_signals**，不再因历史失败阻塞，但也没有生成伪造画像。源数据为空，非空真实画像与真实周期增量仍 **BLOCKED**。非空 fixture 的正常 API→账号绑定→幂等事件→generic 画像 owner 回归通过：第二轮重复项不再写入，新项接受；不把它当作真实账号/模型 E2E。

收藏侧只读确认：原授权视频可正常播放，本人收藏列表仍明确为空，本轮未再点击写入。已询问是否允许另选一条可播放视频对照，在得到回答前不扩大授权范围。上一轮 request completion 修复仍未热加载，本轮扩展包未改；不能认定原生持久化成功。

验证：相关集成 **142 passed**；更广后端 **374 passed**，5 项代理回归先因沙箱禁止本机监听阻断，另在允许监听环境重跑 **5 passed**。Ruff、MyPy（318 文件）、config-show 通过；来源审计 46 PASS / 14 MANUAL / 0 MISSING，只证明注册完整。真实服务已停止，临时账号存储删除，生产配置保持不变。[脱敏证据](testing/assets/2026-10-13-tiktok-optional-history/real-e2e.json)。

## 2026-10-13 原生收藏请求完成时序修复

范围 `capability-increment`，基线 `8070c731`。OpenCLI 真实浏览器对照中，当前轮多次（4 次）授权 UI 点击均先确认目标未收藏，未执行取消收藏或 API 重放；最终一次捕获的原生 `/api/item/collect/` POST 确认为目标视频、`action=1`、HTTP 200、`status_code/statusCode=0`，约 343ms 完成，无错误文案。关闭公约更新通知后仍一样：等待 3 秒，再真正刷新，新文档 complete 后书签仍白色。原生持久化继续 **FAIL**，不能归因于合成点击、通知弹层或 Intel CPU，也不能把成功响应作为账号收藏证明。

发现独立的插件时序缺陷：按钮先乐观变金色，content 立即返回，runner 可能在网页请求尚未完成时刷新。先添加回归，得到 **FAIL（0 次等待，预期 4 次）**；修复后点击前启动只读 Resource Timing observer，等待同源、同视频、`action=1` 的新请求完成，再交由既有的新文档只读确认。观察不到完成仍有界失败，观察器不可用则不点击。新增完整 content→runner 测试确认请求未完成时不会刷新，并保留刷新后持久化证明；没有用任意固定延迟或 HTTP 200 代替成功条件。

验证：相关扩展 **61 passed**；全量扩展 **1814 passed**；选定 TikTok/Instagram 契约、native-save 与文档后端套件 **347 passed**，其余 5 个代理回归先因沙箱禁止监听失败，在允许本机监听后 **5 passed**。Chrome/Firefox 构建、资源检查、TypeScript、Ruff、MyPy（318 文件）通过。没有重跑全量 pytest，没有宣称非空画像或真实周期增量通过。历史入口缺失沿用此前真实观察，本轮未复测；有效的其他 scope 行仍可被画像使用，历史失败本身不等于必须丢弃其他数据。

本轮修复仅构建验证，尚未替换/重新加载用户安装的扩展；不将单元回归冒充更新后的账号 E2E。已真正刷新清除临时页内探针、确认探针不存在、关闭本次 trace 标签并释放两个浏览器 lease。未保存 Cookie、原始响应或账号 DOM。[脱敏证据](testing/assets/2026-10-13-tiktok-native-request/real-e2e.json)。

## 2026-10-13 代理路由修复与真实点击对照

范围 `capability-increment`，基线 `844485bc`，独立 repair worktree。默认 `system` 路由已存在，但 TikTok 的 curl_cffi 未物化 macOS OS-only 代理；`proxy=""` 被库忽略，direct 仍继承环境代理；custom 又会被环境 NO_PROXY 绕过。本机真实 libcurl → origin/proxy 回归先 **3 failed / 2 passed**，统一适配后 **5 passed**。Web API、登录探针和 TikTok 封面使用同一适配器；封面仍固定已验证公网地址和 TLS。Instagram/TikTok 浏览器任务沿用浏览器代理，未新增权限、改系统代理、切节点或重写用户配置。

当前真实配置 `mode=system`，修复后的无 Cookie TikTok 首页 HTTPS 请求经已物化代理返回 **200 / 407811 bytes / 0.7 秒**；只证明公开网络通路，不冒充账号、画像或推荐链路通过。

Chrome 的 CUA 查询仍超时，但 OpenCLI 桥接一度恢复可用，完成指定授权视频的单次 **CDP 真实鼠标点击（isTrusted=true）**。点击前白色未收藏，点击后金色；打开同 URL 没有新文档，未作为证明；随后执行真正 reload，确认页面内探针已消失、新文档 complete 后又是白色未收藏。仍 **FAIL**，因此不能只归因为 DOM 合成点击，也不能宣称代理补丁修复了站内收藏。未取消收藏或重放上游请求。

同一 Chrome 会话用真实菜单进入 `/setting`，账号、隐私、广告、屏幕时间等栏目已加载，仍没有观看历史/活动中心入口。历史仍 **FAIL**。非空画像、周期增量仍 **BLOCKED**。本轮稍后读取网络诊断超时、Instagram 首次渲染检查连接中断；桥接复查恢复后，只读重试确认 Instagram 文档 complete、正文已渲染、21 个交互控件。未认定登录态采集或真实来源 E2E 通过。无需用户重复此前的手动点击对照。

验证：路由与相邻套件 **125 passed**；认证、发现、图片边界、Instagram 契约、TikTok 初始化/增量/任务及配置套件 **848 passed / 37 skipped**；文档索引 **5 passed**；Ruff、MyPy（318 文件）通过。扩展源码/权限/安装包未改，不重复构建；未重跑全量 pytest，不改写此前两项失败。没有新增模块依赖、接口配置项或数据流，模块文档与 changelog 已同步。两个临时浏览器会话已释放，页内探针在真正刷新时已移除；未创建后端/诊断服务，未保存 Cookie、原始 DOM 或网络返回体。[脱敏证据](testing/assets/2026-10-13-tiktok-proxy/real-e2e.json)。

## 2026-10-13 只读续验

用户要求继续处理后，基于 `d0fbabc5` 重建隔离正常后端，真实模型/network 配置不变、所有自动写入与周期同步保持关闭。Chrome 浏览器可枚举但标签页查询仍超时，无法完成真实鼠标点击对照；项目扩展的 WebSocket 和登录态同步正常。

首个正式包 `tt_collect` 任务超时，不能推断列表为空。随后临时只读阶段诊断观察到页面从未就绪进入正向登录身份、本人资料页和已证明的收藏 root，任务最终 empty/complete、0 行。恢复正式包后再执行只读收藏采集，同样 completed、empty/complete、0 行。没有看到此前测试视频延迟保存，也没有据此判定 Cookie 失效、Intel 不兼容或所有超时都已解决。没有再次收藏/取消收藏；未用失败结果或空态生成画像。

本轮无功能代码修改，新增[脱敏续验证据](testing/assets/2026-10-13-tiktok-readonly-resume/real-e2e.json)。真实收藏持久化仍待手动点击刷新对照，非空画像与周期增量继续 **BLOCKED**；历史本轮未重跑，上一轮 `history_entry_not_found` 未解除。正式包已按摘要恢复，源码和产物均不含临时探针；隔离后端、诊断服务已停止，私有配置/Cookie/数据库/原始诊断/日志及依赖软链已清理。文档索引测试 5 passed，diff 检查通过。

## 2026-10-12 明确视频授权后的原生收藏续修

范围 `capability-increment`，基线 `68f0fb4f`，repair worktree `.worktrees/tiktok-full-readiness`。结论仍为 **incremental only**。用户已明确授权指定公开视频加入 `TikTok Favorites`，不再缺少授权；下面的实际结果取代旧段落中的“待授权/未执行”，历史记录不追改为通过。

本轮修复：

- 实机同页存在两个 `favorite-icon`：当前按钮 y=499，下一条预加载视频按钮 y=1222，而视口高 723。后者 CSS 仍可见，导致 `native_control_not_found`。现在只接受完整视口内的控件，拒绝零尺寸、裁切及歧义；优先点击唯一内层原生 button。
- 当前桌面收藏图标变金色时，aria 文案仍为“添加到收藏”。补充只针对 `favorite-icon` 内唯一 `SpanBookmarkWrapper` SVG 的已观察金色识别，避免已选中时反向点击。
- 四列表正式复查先出现喜欢完成后 `task_idle_timeout`；新增内容端持久接收 ACK 和后台有界核对，复现并修复空/错配 ACK 被误当投递成功。修复后三个普通列表在同一四 scope 任务中 empty/complete，历史单独返回缺少入口。一次成功不能证明所有时序都已稳定。
- **金色并不是持久化证明**：TikTok 即时 `synced` / `already_synced` 也必须经过新文档的刷新只读确认。缺失 document instance、刷新失败或结果不再选中时均保留 `native_confirmation_not_observed`，不重复点击。新增成功/失败刷新、无文档与单次点击回归。

| 链路 | 状态 | 本轮实际证据与限制 |
| --- | --- | --- |
| 精确授权与正式后端 | PASS | 当前用户明确确认同一公开视频；正常 Uvicorn/create_app + 真实 Chrome 已安装扩展/登录态，数据与 SQLite 隔离，模型/network 配置保持真实值。自动同步和周期调度均保持关闭。 |
| 指定视频站内收藏 | FAIL（已真实执行） | 页面自然发出指定视频的收藏 POST，action=1、HTTP 200、业务状态 0，图标临时变金色；刷新后 0/3/8/15/25 秒均未收藏，四次只读收藏采集均显式 empty/complete。不得以 HTTP 成功或临时颜色宣称持久化成功。每次未确认后先只读核验，未盲目重复 toggle，未取消收藏。 |
| 最终连续账号采集 | 三 scope PASS / 历史 FAIL | 任务 `92100a57-8d9c-4cf2-bc00-f422e74fd635` 的喜欢、收藏、关注均 empty/complete；历史 `history_entry_not_found`，整个四 scope 任务仍 failed。没有把缺入口当空历史或 N/A。 |
| 非空画像与周期增量 | BLOCKED | 没有已持久保存的非空账号信号。未伪造事件/复制画像，未开启生产同步。正式 init 的上一轮 empty_signals 仍未解除；本轮未重跑无信号的模型链路。 |
| 手动点击对照 | WAITING | 已请用户对同一视频手动收藏并刷新，区分程序点击与账号/网站持久化问题。Chrome 直连标签页查询仍超时；没有证据归因于 Intel CPU。 |
| 最终正式 native-save 构建 | unit PASS / real NOT_RUN | 最后加入金色识别与强制刷新后，暂停进一步写入等待对照；上述请求/刷新证据来自前序构建和临时诊断。不得冒充最终正式 executor 全链路通过。 |

[脱敏证据](testing/assets/2026-10-12-tiktok-authorized-native-save/real-e2e.json) 与 [六字段原生结果](testing/assets/2026-10-12-tiktok-authorized-native-save/native-save-result.json) 分开保存，不含 Cookie、账号 ID、完整 URL、原始 DOM、请求体或返回体。临时网络诊断只观察页面自行发出的请求，不重放接口或签名。

验证：针对性新增用例先红后绿；扩展全量 **1809 passed / 0 failed**；原生保存针对性 **56 passed**，scope ACK 三项回归先红后绿、相关生命周期 **37 passed**；后端账号队列/初始化/readiness/增量及文档索引 **112 passed**。Ruff 与 MyPy（318 文件）PASS；Chrome/Firefox 正式构建及各 22 assets 均 PASS；Firefox 安装版真测 NOT_RUN。Python 实现未改，不把上一轮全量 pytest 的两个既有失败改写为通过。

最终安装包摘要 `206e517b18a5462c8ae8742812dab6c57304ccf8e5397f5e9ccb7632d6e0524d`，已复制到注册目录并热重载；不含临时诊断。沿既有任务通道补充接收 ACK，架构图与模块文档已同步；CLI、配置、安装流程与权限未改变。本轮未合并或发布。隔离后端与私有诊断服务已停止；临时配置、Cookie、SQLite、原始诊断、日志、控制脚本和依赖软链已清理，正式构建保留在注册目录。

## 2026-10-12 慢加载与连续列表交接修复

范围 `capability-increment`，基线 `0077f7f8`，独立 repair worktree；结论 **incremental only**。本轮正式构建的三 scope 空列表连续采集通过，但不能据此声明非空画像、历史和原生收藏已验收。脱敏记录见 [real-e2e.json](testing/assets/2026-10-12-tiktok-readiness/real-e2e.json)。

修复与直接证据：

- 实机诊断里文档 complete 后登录证据仍晚到超过 12 秒。原生保存原先把“还没有登录控件”立即当作未登录；现在区分明确登录墙/登出与未就绪，save/verify 等待登录和内容同时就绪，最多 20 秒。匿名 upload 按钮不再是登录证明。共享 helper 默认仍 10 秒，并增加墙钟上限。
- 本人资料和收藏 tab 已正确选中时，内容区域仍可能未挂载。已证明身份/范围后将 scope root 等待增至 60 秒（关注仍 20 秒），不把空白或未知容器当作 empty；task idle/absolute 上限保持优先。
- 连续任务仍超时后，回归复现了 content 持锁等结果 ACK 时忽略下一 scope 的竞态。现在结果持久化后先释放采集锁，旧完成回调不能释放新 scope 的锁；同文档交接回归先红后绿，随后正式安装包的三 scope 连续实测通过。不能据此断言此前每次超时都来自同一原因。

| 实际链路 | 结果 | 证据与限制 |
| --- | --- | --- |
| 后端/安装来源 | PASS | 正常 create_app/Uvicorn 8420 + 注册 Chrome 扩展，隔离配置/数据/SQLite，模型与 network 配置对象保持一致。构建和 22 assets 通过后部署，复制前后摘要一致、热重载已送达。 |
| 连续喜欢/收藏/关注 | PASS（正式构建空态） | 任务 `fe047cac-a661-46ac-90b5-4e57cf7153b8` completed，三 scope 均 empty/complete，真实接受行数 0；正式包摘要 `50a556ae4bde6043778a40fed66193e1d23255fdd5347fe3b148095c175c5d23`。不是非空采集证明。 |
| 历史入口 | FAIL | 诊断构建确认当前账号 `/setting` 保有 viewer，但设置项没有观看历史/活动中心入口，返回 history_entry_not_found；不伪造空历史，不改为 N/A。 |
| 正式初始化 | FAIL | POST /api/init HTTP 202，新建 profile_update=true 四 scope 任务；收藏/关注 empty，喜欢仍出现 scope_root_not_proven，历史 history_entry_not_found。终态 empty_signals / initialized=false，无画像；没有复用只读三 scope smoke，正式连续成功一次不代表重复执行稳定。 |
| 周期增量 | BLOCKED | 没有该账号本轮初始画像/非空事件，全局和来源周期开关保持关闭。 |
| 原生收藏写入 | BLOCKED | 用户已一般性允许；自动审批要求明确确认助手提出的那条具体视频，确切目标问题仍待回复。拒绝发生在工具执行前，没有写入上游收藏或本地测试 membership。 |

正式初始化失败后追加三次只读诊断：第一次 idle timeout，后两次分别两 scope、三 scope 均 empty/complete。记录显示收藏选中后内容还可晚到约 12 秒；未稳定复现正式初始化的喜欢页失败，不把诊断成功追认成正式初始化成功。

自动化验证：原生冷加载与慢列表回归先出现 3 项失败；同文档 scope 交接新增 1 项失败，修复后针对性 **31 passed**；扩展全量 **1799 passed / 0 failed**；相关后端 `test_tiktok_tasks` / `test_tiktok_guided_init` / `test_tiktok_readiness` / `test_source_incremental_sync` **107 passed**；文档索引 **5 passed**。Chrome/Firefox build + 22 assets 均通过，Firefox 未做安装版真测。扩展全量测试首次受沙箱禁止监听 localhost 阻断，获得本机端口权限后全量通过。注册审计 PASS 46 / MANUAL 14 / N/A 2 / MISSING 0，只证明登记完整。本轮 Python 实现未改动，没有把上一轮全量 pytest 的两个失败记作本轮全绿。

本轮结束保留已验证的正式包（上述 50a556… 摘要）在 Chrome 注册目录；临时诊断未留在源码或安装包。原静态安装包另存本机私有回退压缩包；隔离后端已停止，临时配置/Cookie/数据库/原始诊断/日志和依赖软链已删除。用户主数据、上游收藏及周期同步开关未写入。未合并、未发布。

## 2026-10-11 重新加载后的账号页面续修

范围 `capability-increment`，基线 `1df4e59f`，独立 repair worktree；结论仍为 **incremental only**。本节更新上一次“需手动重新加载”的运行状态，不把局部诊断成功升级为 full 验收通过。脱敏记录见 [real-e2e.json](testing/assets/2026-10-11-tiktok-account-resume/real-e2e.json)。

已修复当前页面中可重现的缺陷：

- 喜欢、收藏标签无 `aria-controls` 时，识别属于本人且已选中 scope 的专属显式空态；关注弹窗要求标题精确匹配 viewer 和 following 专属空文案。隐藏、他人、followers、空白或通用错误不作 empty。
- 设置页没有 nav-profile 链接时，从 canonical app-context session user 取身份，并要求额外正向登录证据；显式 logout 和冲突身份拒绝。使用实际更多菜单的 `settings-entrance`（中文“通用”）进入 `/setting`，整页恢复不再重开设置。
- 登录/本人页/关注弹窗分阶段等待；scope 基础预算 90 秒，360 秒绝对上限和 120 秒 idle 不变。内容脚本尚未注册时最多重投 20 秒，同一任务/scope 才可继续，清理后停止，重投不延长 deadline。
- 初始化只复用匹配 profile_update、完整 scope、预算及增量 owner 的在途或最新权威成功任务；只读 smoke、部分 scope、失败与 degraded 任务不再替代正式初始化。SQLite 回归证明修复前 smoke/partial/failed 三类失败，修复后全部通过。

| 实际链路 | 结果 | 证据与限制 |
| --- | --- | --- |
| Cookie / 后端 / 安装产物 | PASS | 扩展真实同步、passport HTTP 200 verified；正常 Uvicorn 8420、隔离配置和 SQLite，模型及 network 对象保持一致。逐次完整 build/assets 通过后才备份/替换注册目录并热重载、核对摘要。一次传输失败不标记 Cookie 失效。 |
| 喜欢、收藏、关注显式空态 | PASS（诊断构建的独立空态） | 真实 Chrome 分别确认本人三种空列表，接受行数 0。诊断构建只增加只读证据，不代表最终正式构建全链通过，不证明新版非空列表采集。 |
| 四 scope 正式构建 | FAIL | 新隔离后端依然出现 sendMessage_failed、scope_root_not_proven、logged_out、idle timeout。补投递重试后的最后一次三 scope 任务均 logged_out，无消息投递失败；仅表明缺少正向登录证据，不据此推断 Cookie 过期或 Intel 不兼容。 |
| 历史入口 | FAIL | 已成功导航当前账号 `/setting` 且保留 viewer；渲染设置项里没有找到历史/活动中心入口，返回 history_entry_not_found。未伪造空历史。 |
| 正式初始化 | FAIL | 真实 `POST /api/init` 返回 202，并新建 profile_update=true 四 scope 任务，未复用前面的 smoke。任务无有效信号，最终 reason=empty_signals / initialized=false，没有画像产物。 |
| 周期增量 | BLOCKED | 尚无该真实账号的有效初始画像；全局/来源周期开关保持关闭。未用已有画像替代本轮账号初始化。 |
| 站内收藏 | NOT_RUN | 未执行收藏/取消收藏。真实写操作仍缺用户指定的允许测试视频，未用任意推荐视频替代授权目标。 |

测试结束已按 SHA-256 恢复原注册目录安装包，停止本轮隔离后端，清除私有 Cookie、SQLite、原始 DOM 诊断及日志；用户主数据和上游收藏未作为写入目标。

验证：扩展全量 **1791 passed / 0 failed**；新增后端 bootstrap 回归及同文件测试 **114 passed**；相关后端套件 **312 passed / 37 skipped**；文档索引 **5 passed**；Chrome/Firefox build + assets、Ruff、MyPy（318 文件）通过。注册审计 PASS 46 / MANUAL 14 / N/A 2 / MISSING 0，只证明登记完整。


全量 `pytest -q` 实际结果为 **10428 passed / 65 skipped / 2 failed**（2011.94 秒），不是全绿。`test_login_codex_import_uses_source_path` 出现 RemoteProtocolError，目标分支与基线的定点重跑均通过；`test_run_guided_init_bounds_stage1_and_stops_extension_worker` 的 30ms 超时/线程取消断言在未改动基线 `1df4e59f` 上单独连续三次复现失败。未扩大本次 TikTok 修复去修改这两个既有测试。

## 2026-10-11 账号入口续修

范围 `capability-increment`，基线 `618843fb`，原修复 worktree 开始干净。结论 **incremental only**：本轮修复两处可复现缺陷，尚不能把下方账号 FAIL/BLOCKED 改为 PASS。

- 实际 Chrome 的本人标题先于标签挂载；修复独立等待喜欢/收藏入口。真实测试从 `scope_tab_not_found` 推进到本人 URL + 精确身份副标题 + `liked-tab[aria-selected=true]`，随后仍报 `scope_root_not_proven`，接受行数 0。具体 [脱敏证据](testing/assets/2026-10-11-tiktok-account-repair/real-e2e.json)。
- 结果回传的单次请求原来没有超时；补默认/最大 10 秒 deadline、AbortSignal 与迟到 URL 守卫，保持持久 FIFO/原 body 重放。该缺陷由挂起 transport 回归证明，不声称是上一轮实机停顿的唯一原因。
- 后续诊断 TypeScript 构建失败，但临时部署脚本未停在失败处，导致扩展连接断开；完整有效产物已重建；结束前按 SHA-256 校验恢复原 manifest/dist/icons/popup，取消本次待领取任务。扩展 WebSocket 仍未连接，需在 Chrome 扩展页手动重新加载一次。属于本次测试控制器错误，不归因于 Intel CPU、用户登录或 TikTok。账号列表、画像初始化、周期增量与站内收藏仍需继续实测，未执行上游写操作。


验证：扩展全量 **1783 passed**；后端相关 **312 passed / 37 skipped**；文档索引 **5 passed**；Chrome/Firefox build + 22 assets、Ruff、MyPy（318 文件）通过。source audit 为注册检查，不能替代账号真测。隔离 smoke 未生成画像，真实配置/数据未被测试写入。

## 2026-10-11 真实请求续验（PR #286）

本轮结论仍为 **incremental only**：公开发现的真实推荐链路通过；账号采集实际运行后仍失败，不能用单元测试或 Cookie 校验替代四项账号能力验收。脱敏证据见 [real-e2e.json](testing/assets/2026-10-11-tiktok-full-live/real-e2e.json)。

| 链路 | 结果 | 实际证据 |
| --- | --- | --- |
| 正常后端 / 登录凭据 | PASS | 目标 worktree 的 `create_app()`，真实 Uvicorn/TCP 8420；隔离配置、数据与 SQLite；扩展通过真实 WebSocket 自动同步 Cookie；TikTok passport 校验 HTTP 200 / verified。有效模型与 network 配置对象保存前后相等，无 mocks 或 ASGITransport。 |
| 搜索 → 评估 → 推荐 | PASS | 使用隔离副本中的真实已有画像，两次真实搜索共 fetched=12 / enqueued=12 / evaluated=12 / admitted=1 / rejected_low_score=11 / copy_ready=1；真实 GET recommendations HTTP 200 返回 1 条 TikTok 内容，来源与 HTTPS 视频标识正确。本地 Ollama embedding 与配置中的远程聊天模型均真实调用；经历 429 和首轮 180 秒测试窗口超时后，经配置的后备链重试完成。不是 TikTok 初始化生成的画像。 |
| 实际安装产物 / dispatch | PASS | Chrome 注册目录原为 tiktok-source-readiness。逐次备份 manifest/dist/icons/popup，校验候选构建摘要后经 `/api/extension/reload` 热重载；实际扩展领取任务（pending → in_progress），最后恢复原安装产物。诊断构建仅增加只读、无凭据字段，不当作未经修改的发布构建。 |
| 四 scope 账号采集 | FAIL | 原构建四 scope 均误报 login_wall。诊断证明页面没有可见登录提示，实际读到了隐藏脚本文案；修复后推进到本人页/列表确认，仍出现 profile_entry_not_found、scope_root_not_proven，以及一次重载后才结算的 task_idle_timeout。新建无画像的正常后端上再次实测，普通 smoke 仍未稳定产出本人列表。 |
| 普通 smoke 数据边界 | PASS（干净运行） | 新隔离后端无画像，profile_update=false 的失败任务未创建画像。之前含已有画像的诊断进程发生后台学习，因此那次原始画像文件差异不记通过；后续 Cookie 刷新禁用额外模型预热。生产数据未作为测试写入目标。 |
| 画像初始化 / 周期增量 | BLOCKED | 尚无通过本人归属和 scope 验证的真实账号信号，未把已有画像或公开搜索当作账号初始化。 |
| 站内收藏 | NOT_RUN | 未向 TikTok 执行任何收藏/取消收藏写入。 |

已修复：迟到 Cookie 未刷新访客客户端；隐藏登录翻译误报；登录/资料/列表 hydration 等待；本人资料显示名与账号副标题兼容；本人页重复导航；scope 基础预算覆盖导航等待。可见登录墙与账号边界仍失败关闭。完整浏览器页面检查目前仍受 Codex Chrome 控制连接超时限制；账号门未通过前不得声明 full 完成。

验证命令：`pytest tests/test_source_auth_contract.py tests/test_tiktok_producer.py tests/test_tiktok_web.py tests/test_tiktok_readiness.py tests/test_tiktok_contract.py tests/test_docs_index.py`；`ruff check src/ tests/`；`mypy src/`；扩展 `npm test`、Chrome/Firefox build（均从目标 worktree，Python 显式设置 PYTHONPATH）。最终结果：后端相关套件 **375 passed / 37 skipped**；扩展全量 **1781 passed / 0 failed**；Ruff / MyPy（318 文件）及 Chrome/Firefox build + assets 全部通过。本轮未把先前 baseline 的全后端 CI 当作新补修的全量结果。


本轮续验日期：2026-10-10。登录态搜索、安装版 Chrome 扩展 Cookie 自动同步和真实模型管线已通过；当前详细结果见文末续验。历史段落保留其当时的证据边界，不代表当前仍缺 Cookie。discovery-only 范围的真实功能验收已补齐；本机全量与修复后回归结果见文末，当前分支 CI 以 [PR #275 checks](https://github.com/whiteguo233/OpenBiliClaw/pull/275/checks) 为准。

## PR #286 独立验收与修复（2026-10-11）

固定基线：`a9d9b0bc...7a6386ba`（原分支实际包含 7 个提交，含契约提交）；修复 worktree `.worktrees/tiktok-full-readiness`，分支 `fix/tiktok-full-readiness`。本轮为 full 范围验收与修复，原分支/主工作区开始均干净；不合并、不发布、不切换生产服务。

原交付不能判为“全部实现并交付”：真实 backend bootstrap_profile 被 dispatcher 拒绝、全局 DOM 采集可误归类、未 ACK partial 可被 timeout 覆盖、普通 smoke 可写画像、后端缺冻结预算/账号边界；原 head CI 还因两处 E501 提前失败。下方原 ledger 的实现 PASS 属历史注册/单测证据，不能覆盖这些缺陷。

| 本轮门 | 结论 | 证据/限制 |
| --- | --- | --- |
| 实际任务协议 / CLI 等待 | 修复并专项通过 | Python next-task JSON → TS dispatcher → API event ingress，四 scope 顺序和累计 cap；CLI 480 秒覆盖最多 360 秒执行与领取余量；synthetic DOM rows，不冒充真站 |
| 本人列表/登录墙/导航恢复 | 修复并专项通过 | exact own profile + proven active scope root，拒绝全局 DOM，登录墙优先；content lifecycle / scope tests |
| durability / scope / account / profile ownership | 修复并专项通过 | FIFO ACK replay、独立持久 idle/absolute deadlines、scope caps 与 sticky degraded、SQLite first-account fence（handle hash 稳定，可选 secUid 冲突守卫）、smoke 无画像传播；观察身份不冒充 verified |
| native-save SPA 身份 | 修复并专项通过 | 等待/确认阶段 URL 改变时零误点、零误报成功；无真实站内写操作 |
| 全量回归/构建 | 部分通过，全量进行中 | Ruff/MyPy 318 文件、Chrome/Firefox build+assets 通过；最终插件 1779/1779，后端受影响回归 232 passed，最后两项边界修复后相关回归 113 passed，CLI/文档 20 passed；首轮完整后端回归（早于最后两项边界修复）为 10409 passed / 1 failed / 65 skipped，失败是旧身份迁移测试固定日期过期；在原 head 复现后仅修正测试日期，生产时效逻辑不变，修复后该文件 14 passed；最终 head CI 见 PR #286 checks。CI 显式使用 Node 22 执行 Python→TS 协议桥 |
| 已安装登录态 bootstrap→真实模型画像 | BLOCKED | Chrome inventory 两次失败，当前无可读标签页；不能重用 #275 的发现链路证明 |
| 已安装增量/站内收藏真实验证 | NOT_RUN | 依赖同一可控已登录浏览器和新版扩展；收藏另须 exact item/action/target 的具体授权 |

总体仍为 **incremental only**；代码修复与 fixture/protocol 测试不等于 full 验收 complete。未执行上游点赞、收藏、关注或取消操作。

## Full 升级验收 ledger（2026-10-11 起，进行中）

本轮把 integration level 从 `discovery-only` 升级为 `full`：账号行为采集（点赞/收藏/观看历史 bootstrap + 周期增量）、guided init 画像初始化、站内收藏 native save。分支 `feat/tiktok-full-source`（worktree `.worktrees/tiktok-full`，基于 `feat/tiktok-source` 头 `a9d9b0bc`），stacked 于 PR #275 之上。

契约门基线：契约更新为 `full` 后 audit `required_missing=11`（transport.implementation、source-auth.capability-readiness、profile.signals、profile.incremental、profile.bootstrap-registries、extension.task、extension.task-marker、extension.background、surface.setup、engagement.danmaku 排除测试改名、media.native-save），对应下表 NOT_RUN 项；全部 required 行 PASS 前总体 verdict 为 **incomplete**。2026-10-11 收尾后 audit `required_missing=0`、`registration_check_passed=true`（PASS 46 / MANUAL 14 / N/A 2）；MANUAL 14 项为语义类人工验收，真实登录态受控 E2E 见「真实验收」行。

| Gate | Applicability | Status | Evidence / remaining risk |
| --- | --- | --- | --- |
| Contract gate（冻结 full 契约） | required | PASS | 契约已更新为 full；audit 基线 11 MISSING，见上 |
| 后端任务基建（tiktok_tasks 队列表 + 五处注册 + next-task/task-result 路由） | required | PASS | 提交 4644dded；`TiktokTaskQueue` + `tiktok_tasks` 表、`_TASK_TABLES` / `_BOOTSTRAP_TASK_TABLES` / `bootstrap_state` / `source_incremental_sync` 五处注册、`GET next-task`（204）/ `POST task-result`（两阶段 staged completion）/ `POST kick`；23 项队列与路由测试 |
| 插件采集端（tiktok-task-dispatcher + content/tiktok + manifest 注册，两阶段 staged completion） | required | PASS | 提交 8799b0aa / e65d7e21：`tiktok-task-dispatcher.ts`（poll → claim → marker tab → 两阶段 staged completion，pending-result 先落 `chrome.storage.local` 再有界重试至 2xx ACK）、`content/tiktok.ts` + `tt/`（DOM-only，零 dy 依赖）、双 manifest content_scripts 注册；canonical 路由键模块 `content/tiktok/task-executor.ts`；audit `extension.task` / `extension.task-marker` / `extension.background` PASS |
| 画像初始化（guided init 接入，init 真实信号） | required | PASS | `_INIT_SOURCE_ORDER` / `_PLATFORM_SOURCE_FIELDS` 放行，`guidedInit: true`，CLI `--yes-tiktok` / `--no-tiktok`，`_enqueue_tiktok_bootstrap_task` / `_collect_tiktok_bootstrap_events` 接入 init 第 1 阶段，事件经 `_tiktok_events_to_history_items` 进画像；`tests/test_tiktok_guided_init.py` 15 项；audit `profile.signals`、`surface.setup` 转 PASS（真实登录态采集证据仍属「真实验收」行） |
| 周期增量（source_incremental_sync，默认关不变） | required | PASS | 提交 4644dded；`SOURCE_ORDER` / `_TASK_SPECS` / `_SOURCE_CONFIG_ALIASES` / `_SOURCE_CONFIG_NAMES` / `_SOURCE_INTERVAL_FIELDS` 注册，`source_incremental_enabled` 与 `sources.tiktok.incremental_enabled` 双默认关不变；`seed_guided_init_attempts` 把 tiktok 归入 ok-only 组 |
| 站内收藏（ExtensionNativeSaveBroker + native-save/tiktok.ts，auto_sync 默认关，精确授权 E2E） | required | PASS | 提交 e65d7e21 / 0cb57594：broker / adapter / identity 注册（`is_native_save_local_only("tiktok")=False`），executor 目标 `TikTok Favorites`，auto_sync 默认关闭；`test_extension_native_save_broker.py` / `test_saved_sync_extension_adapters.py` 等新增用例通过；真实受控 E2E 仍待人工授权运行（见下行） |
| 契约测试反转（test_tiktok_contract.py 排除断言改写为正向） | required | PASS | 排除断言全部改写为正向注册断言并通过（12 项）；契约登记的 `test_tiktok_engagement_danmaku_is_unavailable` / `test_tiktok_media_deep_link_uses_https` 精确存在；audit `required_missing=0`（PASS 46 / MANUAL 14 / N/A 2 / MISSING 0，`registration_check_passed=true`） |
| 真实验收（登录态 bootstrap/增量/native-save 受控 E2E） | required | FAIL | 本轮实际装载构建并执行账号任务，但本人页/列表确认仍失败；初始化与增量 BLOCKED，native-save NOT_RUN，见顶部真实续验。 |
| 文档/交付（模块文档、CHANGELOG、架构图、PR） | required | PASS | `tiktok.md`（账号 bootstrap / guided init / native save 章节）、`cli.md` / `init.md` / `api.md` / `config.md` / `extension.md` / `saved-sync.md`（六平台→七平台）已同步；CHANGELOG 三个升级条目；PR 待上层统一提交 |

总体 verdict：**incremental only** —— required 行中「真实验收」仍 FAIL，登录态真实受控 E2E 完成前不得宣称 complete。

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
| Auth / capability readiness | required | PASS | 2026-10-10 已安装扩展自动同步 → passport verified → 真实关键词搜索 3 条；正式搜索管线 4 条入池，见续验 |
| Browser task / MV3 recovery | N/A | PASS | 后端发现，无内容采集任务；contract 排除测试。Cookie 同步不属于浏览器任务 |
| Bootstrap / post-init incremental | N/A | PASS | discovery-only 排除测试；不把匿名推荐流当账号画像 |
| Formal discover / keyword dual-track / admission | required | PASS | unit/static + full-pipeline；planner 与 inspiration 专项测试，guest 多词转 hashtag，失败词单独 mark_failed |
| Eval / recommendation | required | PASS | full-pipeline；真实配置模型评估 4 条，3 条入池，3 条生成推荐文案，API HTTP 200 返回 3 条 |
| Config / API / status convergence | required | PASS | unit/static + config-show；已更新凭据 verified 文案，明确搜索需另测 |
| Setup surface | N/A | PASS | guidedInit=false，contract 测试 |
| Desktop / mobile recommendation rendering | required | PASS | 2026-10-05 正常 Uvicorn 后端 + 独立 Chrome 普通 UI，真实封面/推荐/跨端本地保存；见续验 |
| Desktop settings / mobile consumption actions | required | PASS | 桌面来源开关、mode/region/budget 保存及过滤、两端本地保存与状态一致通过；手机来源设置/凭据/verify 按规范 §0.3 排除 |
| Extension popup credentials | required | PASS | 2026-10-10 独立 Chrome 测试 profile 安装同 worktree 扩展，真实后端设置保存/刷新/验证/关闭再开启通过；用户 Chrome 自动 Cookie 同步另有证据 |
| Mobile credentials / source settings / verify | N/A | PASS | 接入规范 §0.3 的全产品范围排除，contract 显式声明 + test_tiktok_mobile_consumption_excludes_credential_management |
| Image delivery | required | PASS | live-transport；3 个真实 cover 通过应用 fetch_cover_bytes，均为 image/*，见 covers.json |
| Image proxy DNS / redirect boundary | required | PASS | TikTok 专用路径逐跳 DoH 解析、拒绝非 global/过渡地址并固定连接 IP；22 项安全测试 + 3/3 真实代理封面下载，见 covers-pinned.json。其他来源的旧下载路径不在此证明范围 |
| Mobile deep link | N/A | PASS | HTTPS browser fallback，contract 测试 |
| Native save | N/A | PASS | TikTok local-only，contract 测试；未调用站内 mutation |
| Focused backend verification | required | PASS | 244 个专项测试通过；后增 readiness 18 项通过，图片/CLI/API/声明/版本相关 118 项通过 |
| Full backend verification | required | PASS | 代码基线 `1c632c29` 云端全量 9970 passed / 111 skipped / 0 failed；其后资料缺 ID 补丁的相关 157 项通过。最新 head 的完整状态以 PR CI 为准，分阶段证据不冒充同一次执行 |
| Chrome + Firefox tests/build/assets | required | PASS | 两目标构建、类型检查、资源检查通过；扩展测试 1536/1536 |
| Safe real E2E | required | PASS | 匿名 tag → pending_eval → 真实 LLM → 推荐 API；限于此明确切片 |
| Authenticated / rejected credential recovery E2E | required | PASS | 2026-10-10 有效→受控无效→安装版扩展重新同步→有效通过；无账号登出，自然过期未执行，不与受控拒绝混同 |
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

以下是 2026-10-04 的历史前提（截至 2026-10-10 已完成 Cookie 同步、passport、真实关键词搜索、受控凭据拒绝恢复和桌面/popup 设置；移动凭据按规范不适用）：当时需要可用的 TikTok 登录会话及装有本次构建的浏览器。图片 DNS 边界已补齐并实测。所有公开来源实现修复可独立交付，账号行为接入属于 contract 明确排除的另一个功能范围。

## 最终复核补充

完整扫描耗时 34 分钟：5 项失败分别为旧 hashtag 断言、测试运行期间主线版本更新引发的两项版本一致性断言、推荐服务地址测试隔离和生成式第三方声明不同步。已修正断言、合入 main 的隔离 fixture，并把 signer notice 写入生成器而非手改生成结果；这 7 个相关测试文件合并复跑 178 项通过。安全下载路径与图片 API/CLI/声明/版本的 118 项测试另外通过。随后 `1c632c29` 的 [完整 CI](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/37207105480) 全绿：后端 9970 passed / 111 skipped，独立引导初始化浏览器测试 39 passed，Windows 和 Firefox 检查通过。其后资料查找缺少 ID 的补丁单独通过 157 项回归；最新 CI 状态以 PR 当前 head 为准。

TikTok 封面经 `runtime/tiktok_images.py` 处理：Cloudflare DNS-over-HTTPS 仅接收公开 CDN hostname，服从现有 network 路由；固定 IP 用 curl CONNECT_TO，Host/SNI/证书验证保留原域名。每次跳转重新执行白名单、443 端口、解析地址校验并使用新会话；DNS 失败时关闭本次下载，不回退未经验证的本地解析。正文流有 10MB 上限，拒绝后主动取消下载。真实网络最终结果见 [covers-pinned.json](testing/assets/2026-10-04-tiktok/covers-pinned.json)。此路径增加对 Cloudflare DoH 可达性的依赖，不携带账号 Cookie。

实现依据：[libcurl CONNECT_TO](https://curl.se/libcurl/c/CURLOPT_CONNECT_TO.html)、[Cloudflare DoH JSON API](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。初次本地 DNS 地址固定测试超时，随后改为沿配置代理的 DoH 解析并完成 3/3 真图复验；不将中间失败结果写成成功。

## 登录环境就绪后的执行步骤

1. 使用隔离后端配置/数据目录和独立端口，将本次构建的扩展连接到该后端。Chrome 的装载根为修复 worktree 的 `extension/`，Firefox 为 `extension/dist-firefox/`；先核对 builds.json 的版本与树摘要。
2. 在这个装有扩展的浏览器登录 TikTok，确认统一凭据端点收到 Cookie 同步并通过 passport 心跳。分别记录未登录、已验证、过期/拒绝三种状态；不以“Cookie 存在”替代验证成功。
3. 在同一隔离配置下执行 `openbiliclaw discover-tiktok --mode search --query "science experiments" --limit 3`，记录实际错误分类或条目数。再让正式 producer 消费同一短语，核对 pending keyword 的 used/failed 状态和候选来源；搜索必须保留短语，不能转 hashtag。
4. 桌面与已安装 popup 操作 TikTok 启用、mode、预算、地区、验证/凭据管理；桌面/手机验证推荐过滤与消费。刷新后核对后端配置与状态一致。手机凭据/来源设置按 §0.3 不适用。暂存/收藏只验证本地语义，不调用 TikTok 站内收藏接口。
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


### 验收范围纠正（2026-10-05）

此前将手机缺少凭据入口列为实现缺口不准确。[接入规范 §0.3](platform-source-integration.md#03-桌面与插件测试连接按钮移动端有意排除) 明确排除移动凭据管理和测试连接；§4 的来源设置要求针对桌面与 popup。已在 TikTok contract 增加 mobile_credentials/mobile_source_settings/mobile_verify=false，并用契约测试锁定。手机 required 消费面（真实推荐、封面、本地保存、跨端保存状态）已通过；这不是为了跳过缺测而缩小范围。旧段落中的“手机无凭据入口”是观察事实，不再作为未完成项。

### 已登录 Chrome 的安装预检（2026-10-05）

用户确认 Chrome 已登录 TikTok。通过 Chrome 安装记录确认 OpenBiliClaw 是商店版 0.3.226（ID `cdfjfkdjjhdaccbldipkjhpibnfbiamg`），其 manifest 无 TikTok host permission，不能靠重启加载出分支新增能力。分支 Chrome build、verify:assets 与 package:only 全部完成，[构建与预检摘要](testing/assets/2026-10-05-tiktok/chrome-update-preflight.json) 记录安装包 SHA-256。后端重载广播返回 delivered=true，但它只证明广播被订阅者接收，不能证明新构建部署或 Cookie 同步成功。

Chrome 连接工具可列出浏览器，但会话命名/标签控制均超时；已有本地调试端点也握手超时。未改写商店版受校验文件、未读取 Cookie 原值，等待恢复浏览器控制后加载分支构建再执行真实登录链路。用户已登录的陈述与工具连接不可用是两个独立事实，不能因后者声称用户未登录。`6ca8f5cc` 的完整 CI 已全绿（run 37215382565）。

## 已安装 Chrome 与登录搜索续验（2026-10-10）

- 用户手动加载修复 worktree 的 `extension/`；Chrome 安装记录指向该目录，商店版停用。启动隔离 loopback 8420 的正常 `create_app()` 后，扩展通过 runtime-stream 请求自动同步登录 Cookie。真实 passport 探针确认登录，`science experiments` 搜索返回 3 条；[脱敏结果](testing/assets/2026-10-10-tiktok/installed-cookie-search.json)。未修改用户 TikTok 登录状态。
- 在 `11ccce37` 上使用既有 smoke 脚本的临时 search 变体：保留实际配置模型、使用合成 science 兴趣，Cookie 仅复制到权限受限的临时数据目录；正式 `TiktokDiscoveryProducer` 领取完整短语（不压缩为 hashtag），关键词状态 used。4 discovered / 4 enqueued / 4 evaluated / 4 cached / 0 rejected，768 维 embedding，4 条文案；直接推荐调用 served=2，ASGI 推荐 API HTTP 200 返回 4 条且身份均正确。[管线](testing/assets/2026-10-10-tiktok/authenticated-pipeline.json)、[关键词](testing/assets/2026-10-10-tiktok/keyword-status.json)。这是正式 producer/evaluator/recommender 的集成证据；API 使用既有脚本的 ASGI transport 和合成画像适配器，不冒充正常 TCP 后端全链路或无人值守调度。
- 最新 main 合并带来 Instagram 来源；冲突保留两者的清单、别名、网络路由与 local-only 保存语义，并同步来源数量为十四。

- 受控凭据失效恢复：正常 verify 为 verified；只替换隔离文件为无效测试 session 后，真实 verify 为 failed；删除测试文件并向已连接扩展发重载事件后，自动同步回原真实 Cookie，verify 再次 verified。每次 verify 间隔超过共享 10 秒 debounce，均 replayed=false。[结果](testing/assets/2026-10-10-tiktok/auth-recovery.json)。第一轮连点重放了旧结果，已重跑排除；不是平台误接受无效凭据。此测试不声称自然过期或用户重新登录，不会注销浏览器会话。

- 安装版 popup UI：在独立 Chrome for Testing profile 加载同 worktree 扩展，连接真实隔离后端（不复制用户 Chrome profile）。实际选择 auto、region=US、search budget=4，保存并刷新后 UI/API 一致；点击测试连接后 verified；关闭保存后 API enabled=false，修复祖先 aria-disabled 后能重新开启并保存为 true。[字段证据](testing/assets/2026-10-10-tiktok/popup-settings.json)。第一次模式保存被临时 config.local 覆盖，移除该测试覆盖后重新验证；不把覆盖中的运行结果算通过。
- 新发现的 popup bug：关闭来源后 `.source-card-face[aria-disabled=true]` 会使嵌套启用框也不可操作。移除祖先 ARIA 禁用，仍用 sourceOff 阻止展开。执行实际状态同步函数的回归先失败后通过（39/39），浏览器关闭→重新开启和后端回读也通过。

- 合并 main 后再次运行同一真实搜索管线，仍为 4 fetched/evaluated/cached、4 文案、HTTP API 4 条；关键词 `science experiments` 的 id=1/status=used，所有候选的 source_keyword_id 均为 1，确认归因未丢失。[合并后管线](testing/assets/2026-10-10-tiktok/authenticated-pipeline-merged.json)。

### 合并后验证进度

- Ruff 全 src/tests 通过；MyPy 317 文件通过；Chrome / Firefox 构建及资源检查通过；修复后扩展 1681/1681 通过。
- 首轮专项 172 passed / 1 failed（main 的海外路由清单漏预期 TikTok），修正断言后相邻 25 项全部通过。第三方声明 16 项通过。
- 合并时桌面 alias 表产生语法错误，已修正并通过 Node 语法检查；涉及的桌面对话布局、autoload、issue-98 浏览器用例重跑 34/34 通过。
- 本机完整 pytest 首轮结束：10323 passed / 65 skipped / 7 failed（34 分 17 秒）。5 项桌面浏览器失败已由修复后 34/34 重跑覆盖；余下两项是 main 的初始化名单测试未排除 discovery-only TikTok，以及架构图仍写 13 来源。修正后相关 Instagram / 微博 / TikTok 契约 / docs index 共 82/82 通过。首次失败与重跑分开记录，不冒充同一次全绿。
- 扩展修复后 1681/1681；Ruff、MyPy 317 文件、Node 语法检查、两种扩展构建/资源检查均通过。测试配置的 LLM 与 network 对象同生产配置一致。临时服务器、浏览器已关闭，测试配置/Cookie/数据库已清理，只保留脱敏证据。
- 合并冲突已消除，原 feat/tiktok-source worktree 同步且原有未跟踪文件保留；未合并 PR、未发版、未切换正式后端。后续 CI 状态直接查看 PR checks，不以本文冻结运行中状态。
