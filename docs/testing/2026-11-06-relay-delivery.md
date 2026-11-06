# 2026-11-06 HTTPS 中继与主应用交付

范围：元信息 capability-increment、免费模型中继、安全验证及主应用打包。基线 Core `28c9d7e320eadc17e5e011524df328d89de6331a`，独立分支 `fix/relay-metadata-delivery`。原主工作区输出目录与无关 worktree 保留。

| 门 | 结果 | 证据 |
| --- | --- | --- |
| HTTPS 上游 | PASS | 固定 api.siliconflow.cn HTTPS、禁止重定向、内置公共 CA；发布 Rust 的假证书代理3次握手被拒绝、解密请求0字节、无 TLS key log |
| 中继真实请求 | PASS（本机有效测试 CA） | HTTPS socket → 中继 → 已发布真实 Rust → 硅基流动；两个指定模型 SSE 与 BGE1024维并发成功；无用户内容输入，隔离 Keychain 条目已删除 |
| 令牌/重放/撤销 | PASS | 只持 Token、内容篡改、跨设备、过期、撤销均拒绝；同一证明8路并发只允许1次；配额重启后仍保留 |
| 配置/打包模式 | PASS（专项） | URL只允许HTTPS origin；中继不调用本地共享Key；官方中继发行模式不生成Key SDK且拒绝遗留目录 |
| 本地历史元信息 | PASS | 一致性备份后恢复21字段（描述20、作者1）；没有来源证据的字段保持未知；没有触发模型请求或改评分 |
| YouTube 历史回填 | PASS | 通过 agent-reach 的 yt-dlp 公开只读路径查询104条，0错误/0验证挑战，补回187个空字段（日期100、描述79、作者8）；4条未返回日期继续未知，未下载视频或读取浏览器Cookie |
| 生产中继 | BLOCKED | 尚未收到服务器、域名或云项目位置；默认发行地址仍空，未修改线上去向或撤销现有Key |
| 全量回归 | PASS | [CI 42163008034](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42163008034)：12,485通过、162跳过；浏览器端到端72通过；插件1,884通过；Ruff、391模块MyPy及i18n通过 |
| 中继跨平台专项 | PASS | [CI 42164797972](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42164797972)：Ubuntu、Windows、macOS各19项，服务镜像/Caddy配置亦通过 |
| 主应用Mac候选包 | PASS | [构建42163055896](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42163055896)：Intel/ARM整包均完成签名、公证、staple和冻结程序自检，详见[公证记录](assets/2026-11-06-relay/mac-candidate-builds.json) |
| 主应用正式渠道 | 见发行记录 | [v0.3.234完整发行](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/openbiliclaw-v0.3.234)中的安装包、插件、容器链接及 `delivery-verification.json` 记录最终产物与校验；本表是提交前验收快照 |

真实请求短样本：8B首段0.672s/总0.712s，4B首段9.182s/总9.379s，BGE0.964s。不是容量或延迟保证。随后服务端池从客户端默认1+2独立为共享12worker，默认单设备3路；应以最终回归/部署压力测试补充容量结论。[机器可读实测](assets/2026-11-06-relay/live-security.json)。

最终SDK `.2` 和12worker配置再次通过同一隔离HTTPS→真实供应商测试：8B首段0.591s/总0.750s、4B首段0.484s/总0.577s、BGE1024维0.420s；假证书代理3次被拒绝、0字节请求解密、无TLS key log，测试钥匙串条目清理成功。[最终实测](assets/2026-11-06-relay/live-security-final.json)。两轮均为短合成样本，4B延迟波动不能据此当作稳定SLA。

专项19项通过；新增非流式3次上游尝试的请求数/Token预算预留，余量只够一次时在派发前拒绝。隔离临时配置的 `openbiliclaw config-show` 通过，未配置供应商秘密。五平台原生SDK、双架构Docker安装和独立Caddy部署校验已通过；主应用整包和全量回归另记最终状态。

中继CI另以Ubuntu、Windows、macOS矩阵运行设备身份并发创建、证明/撤销/配额、真实子进程传输及元信息修复19项，直接验证文件系统和IPC差异；构建/导入成功不代替这一项。

本机数据检查只保存字段计数，不把标题、URL、账号或数据库提交到仓库。备份位于本机私有输出目录。历史相对标签不能推算绝对发布时间；元信息修复完整不等于所有平台上游/登录态全部可用。

迁移尚缺的外部步骤及保护边界见 [部署说明](../sponsored-relay.md)。并发首次创建设备身份的测试发现原子link切换窗口曾误拒绝新身份，已在同一已打开inode上做最多100ms等待，永久多链接仍拒绝，并保持拒绝前不读取密钥。

供应商Key一旦存在旧二进制，无法靠追加客户端校验保证不可提取；彻底迁移须生产中继、不含Key的新包及供应商撤销旧Key三者完成。

中继断流补强：响应头发送前断开也会释放服务端容量，连续4次早断线后容量仍可用；配额预留保持不退款。部署新增独立CI构建容器并验证Caddy/Compose配置，尚未执行时不得算作生产部署。

五平台SDK `sponsored-runtime-2026-11-06.2` 已发布供Core0.3.234使用；私有release工作流42160343414全部通过，Mac双架构已签名公证，6个公开资产摘要/大小逐一匹配本机验签产物。SDK的Core契约快照为a135ff8c，后续中继传输清理不改变签名任务。

补充历史只读恢复：知乎8次尝试补10个空字段，第8次命令未成功后停止；小红书1条已有完整链接补3个正文/描述/标签字段，现有作者未覆盖，读取器不返回日期。总计恢复221个字段；不能将部分读取器缺字段称为源站一定没有字段。

代码回归和手工Mac候选包固定 `3d80ab5713aafe6432cdffaff579955598b1daad`；其后的本分支提交仅增加跨平台CI和验收文档，产品源码/测试/插件目录保持相同。三OS专项直接检验设备私钥并发写入和真实IPC，不从Mac结果推断Windows。正式发布会补齐主应用渠道，SDK仍不能代替整包验收。

实际下载的Intel归档再通过本机codesign深度严格校验、Gatekeeper评估与DMG票据校验，[SHA与验签记录](assets/2026-11-06-relay/intel-package-verified.json)。以包内冻结API进程、隔离的明确免费配置进行HTTP保存/热切模型，两模型均完成流式+BGE-M3组合探测，8B 1.431秒、4B 0.745秒，[请求记录](assets/2026-11-06-relay/frozen-live.json)。首轮测试脚本漏传父进程配置/端口，被主动终止并保留本机失败记录；这是测试入口修正，不修改产品源码。正式桌面初始化覆盖以CI入口自检及浏览器E2E为准，不将内部API进程声称为本机GUI安装验收。

请求记录中的首次Keychain清理未匹配：测试目录原路径与配置resolve后的路径不同。随后仅查询本应用服务的条目元信息，以8秒测试时间窗与本次临时安装包ACL路径双重匹配，唯一条目通过SecKeychainItemDelete精确删除，未读取秘密值；[清理补验](assets/2026-11-06-relay/frozen-keychain-cleanup.json)。测试进程和临时配置已退出/删除。
