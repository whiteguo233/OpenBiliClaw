# 安全与评估元信息验收（2026-11-06）

## 范围与边界

本次来源工作是既有能力修复（capability-increment），没有接入新平台或扩大站点权限。检查标准化对象、候选 SQLite、single/batch prompt、canonical sparse JSON 和 Jev 源片段投影。真实模型质量、所有平台的实时抓取可用性不由这些回归证明。

安全部分收紧默认远程访问；Rust TLS/账本补丁及Mac旧钥匙串兼容已合入私有PR7/8。五平台安全SDK [2026-11-06.1](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/sponsored-runtime-2026-11-06.1)已公开发行，本PR更新Core固定目录；主应用整包与现有用户设备升级另行推进。

## 确认的字段传递问题

| 数据 | 断点 | 修复 |
| --- | --- | --- |
| V2EX 原生回复数 | 来源与 SQLite 有 reply_count，默认 sparse 白名单未包含 | 保留正数 reply_count；不伪造 comment_count |
| X / 微博原生互动字段 | 原生回复/转发/书签被 sparse 省略；旧通用别名仍有对应值，X share 还含 quote | 保留原生计数；提示不叠加同义字段，不把此问题夸大为所有互动都丢失 |
| GitHub 仓库语义字段 | SQLite raw_payload.source_metadata 完整，但 engine prompt helper 未投影 | 创建/更新/推送时间、语言、可见性、fork/archived/disabled、fork/issues/watchers 计数、许可证白名单进入 LLM/Jev；内部 ID/原始响应/令牌排除 |
| 仅有原始时间标签的条目 | published_label 已入库，但 sparse 只保留 published_at | 无绝对日期时保留标签，禁止臆测绝对时间 |
| 小红书搜索卡片正文/标签 | 原生 note_card 的 desc/tag_list 不在扩展 DTO，后端也未映射 body_text/tags | 只转交当前笔记的公开 desc/description 和 tag 名；正文最多8000字符、32个128字符标签，不转交整包；入库后完整正文进入评估，避免400字符简介裁切 |

小红书 DOM 列表卡片本身没有正文时仍保持未知；没有为了补字段请求未授权接口，也不从 note ID 猜发布日期。知乎、YouTube 当前代码已有显式发布时间映射；本机存量缺失样本多为旧记录，不能据此断言当前上游故障。历史数据未批量写回或强制消耗模型额度重评。

## 逐平台核对

| 来源 | 已核对的传递合同 / 限制 |
| --- | --- |
| Bilibili | 标题、作者、简介、时长、互动、发布时间/原始标签；视频没有 body_text 属正常字段分工 |
| 小红书 | DOM/原生搜索卡 → observed-urls → 候选池 → sparse；本次补 desc/tag 名，未返回的正文和日期仍未知 |
| 抖音 / TikTok | 时长、描述、互动、绝对时间/原始标签；TikTok 平台原生 ID 不放入语义 metadata |
| YouTube | 描述片段、时长、播放/点赞/评论、日期字段与 publishedTimeText；不存在的完整视频转录不伪称已有 |
| X | 完整 body_text、标签、原生及通用互动、日期；已补原生字段 |
| GitHub | 仓库 description/topics/star 与语义 source_metadata；已补状态和许可，pushed_at 不替代 created_at |
| 知乎 | summary/body_text、类型、作者、赞同/收藏/评论、created_time 映射；摘要不等于完整文章 |
| Reddit | 帖子正文/摘要、subreddit 标签、vote/comment、时间；旧库缺字段不可直接恢复 |
| Linux.do / V2EX | 主题正文/标签/讨论数/时间；V2EX 原生回复缺口已修 |
| Bangumi | 条目简介、标签、评分/评分人数/排名/发行日期；评分未被丢弃 |
| 微博 | 正文、互动、话题、发布时间、视频时长；原生计数补齐 |
| Instagram | media 正文/互动/时间；user 类型只有简介，不补造帖子发布时间或互动 |
| 开放 Web | 现有 LLM extractor 只有标题、作者、摘要和 URL；没有可信日期/互动/全文合同，这是上游能力限制，不是本次 sparse 丢字段 |

## 验证与兼容

- 首轮元信息五项回归在修改前失败，修复后专项380项通过；后续小红书补充另有扩展解析和 HTTP→SQLite→评估回归，修改前失败、修改后通过。
- 最终元信息、prompt、wire、Discovery 和 Jev 专项合计428项通过，包含长正文输入预算拆分；没有用这些专项数量替代完整CI。
- canonical 返回值深拷贝嵌套许可证；拒绝未规范化字段、未知键/类型；raw source 行保持原文。
- 用真实 `SponsoredProvider._check_context` 和已批准8B配置回放8条各8000字的长正文，复现输入超限（直接异常及 service 包装均在修复前失败）；现沿用最多3层、6个额外请求的拆分预算，所有条目和正文原样保留。此项使用本地上下文检查和合成评分响应，不是上游模型质量测试。单条超限、无拆分预算或未尝试完的子批保留错误；认证、429和网络失败不会因此放大重试。
- 缓存命名空间 `content-eval-v7`；系统 prompt 为固定 golden。row-wire-v1 保留冻结列，遇到新字段明确拒绝，不静默截断。
- 远程未设置密码时私有 HTTP401/WS4401，可信本机可用。迁移四端点仍由自身更严格的真实 loopback + 同源 + X-OBC-Auth 自检，远程403；已验证关闭普通 loopback 信任后本机恢复仍可用。
- 真实 uvicorn HTTP + Chromium 的桌面/手机设置指引、重新检查和密码登录均通过2项，已加入 CI 浏览器任务。浏览器连接临时 loopback 服务，测试中关闭本机信任以模拟远程访问；并非真实跨设备网络验收。使用临时 DB、未调用模型或操作站点账号。
- 插件认证专项14项通过；最终插件全量1884项通过，TypeScript 类型检查通过。包括小红书正文/标签解析、缓冲区读写隔离；后端小红书与评估专项39项通过，迁移/本机边界专项22项通过。
- Docker/脚本安装的认证步骤与 Cookie 等待指引同步；bootstrap 既有83项通过。此项验证脚本与文档合同，不声称已在本机重建并启动新容器镜像。
- Ruff、MyPy（386源文件）、格式与国际化校验通过；配置示例已通过 TOML 解析。安全与元信息源码提交 `a5ace697` 的[完整 CI](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42115046519)已完成：后端12466通过/162跳过、浏览器72通过，Windows HKCU与Firefox构建通过。后续固定SDK目录的原生安装和双架构Docker验收见 [PR #338 checks](https://github.com/whiteguo233/OpenBiliClaw/pull/338/checks)，以对应提交的完成结果为准。
- 国际化目录、sparse 冷/热缓存和 Tailnet 配置边界合计47项通过；英文目录移除旧匿名访问提示，缓存测试固定 v7，Tailnet 远程请求验证新401错误码及凭据未落盘。已中断或旧提交的运行不作为最终完整通过证据。

界面留档使用临时配置和合成数据：[桌面设置指引](assets/security-2026-11-06/desktop.png)、[手机设置指引](assets/security-2026-11-06/mobile.png)。CLI 配置读取在独立临时项目运行 `openbiliclaw config-show` 成功；未读取或展示真实凭据。

## SDK 发行与真实验证

- 最终Runtime `c4878e84`、Core合同快照 `a5ace697`；五平台原生构建全部通过，Mac两种架构均Developer ID签名并获Apple Accepted，Windows采用Ed25519 manifest校验。五个归档及目录文件上传后，GitHub返回摘要/大小均与本地一致。独立可信根未改变，见[发行摘要](assets/security-2026-11-06/runtime-release.json)。
- 模型/任务合同、凭据批次、目的地、额度与到期时间逐字段对比上一发行一致。本次没有轮换或撤销生产Key。
- 首轮签名候选在Mac旧账本升级时等待Keychain授权而超时，未公开。修复采用无UI原生查询和受原ACL约束的2秒只读兼容，不修改钥匙串权限、搜索列表或默认钥匙串，不将秘密放入argv。最终签名版本已从旧正式版生成的隔离账本成功启动，账本字节完全不变；测试条目精确清理。这是本机Mac Intel实测，不代表逐台用户钥匙串验收。
- 从公开URL下载新版组件的[本机安装验收](assets/security-2026-11-06/runtime-install-smoke.json)通过：验签、原生策略检查、激活、重复安装复用、桌面打包暂存、拒绝篡改包并保持原活动组件。打包/安装相关单测155通过、2跳过。
- 以实际发布组件、独立临时目录和合成文字并发调用两模型流式与BGE-M3，关闭模型互备，3/3成功。8B首段0.426秒/总0.593秒，4B首段0.453秒/总0.633秒，BGE向量1024维、0.314秒；结束关闭子进程并删除测试钥匙串条目。详见[真实请求记录](assets/security-2026-11-06/runtime-live-probe.json)。这是一次短请求TLS/并发回归，不是持续性能、全部业务质量或免费服务稳定性的承诺。
- 五平台实际下载/安装和Linux双架构Docker构建由本PR目录变更触发专门CI；对应提交的完成状态见[PR checks](https://github.com/whiteguo233/OpenBiliClaw/pull/338/checks)。主应用latest未改变，不把SDK发布等同于整包更新。

## 尚需生产条件的部分

Rust 二进制仍不是共享 Key 的不可提取容器；本地额度只能提供同一安装的公平性。移到服务端需要明确部署位置、域名/TLS、设备凭证签发/撤销与集中持久化额度，当前未提供生产目标。没有部署中继、撤销生产 Key 或改写生产请求出口。供应商账户硬限额仍须在真实控制台验证。
