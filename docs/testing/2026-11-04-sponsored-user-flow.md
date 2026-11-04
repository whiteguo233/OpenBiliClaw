# 免费模型用户流程验收（2026-11-04）

## 结论与范围

本分支的初始化、插件、PC、手机设置已接通8B/4B选择，文本向量使用BGE-M3，真实Rust/API流式调用通过。五平台SDK组件已[发行](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/sponsored-runtime-2026-11-04.1)并接入固定目录，主程序变更仍在PR336，尚未向现有用户发布新版应用。需求与边界见[实现决策](../plans/2026-11-04-sponsored-user-flow.md)。

本轮真实上游测试由 `SponsoredProvider → 签名IPC2 → release Rust HttpUpstream → SiliconFlow` 完成，关闭免费互备，确保返回的就是选择的模型。只发送合成文本；用户授权的Key通过stdin进入临时构建，未写入仓库/配置/报告。测试策略最多30请求、150,000tokens、20RPM、有效3小时；临时二进制、封装材料及本测试账本Keychain条目已删除。不是公开发行包。

## 真实API结果

| 用例 | Qwen3-8B | Qwen3.5-4B |
| --- | --- | --- |
| 文本首段/总耗时 | 0.614s / 0.873s | 0.448s / 0.685s |
| 项目画像Prompt | JSON成功，12.732s | JSON成功，20.585s |
| 两候选评估（传统production模板） | Rust高、博彩低，11.298s | Rust高、博彩低，13.885s |
| 默认sparse-json候选评估与Core校验 | 2/2编号和评分通过，7.104s | 2/2编号和评分通过，11.967s |
| 30条推荐文案 | 30/30编号，59.917s，首段1.006s | 30/30编号，44.475s，首段0.409s |
| 模拟工具调用 | 正确选择search，2.052s | 正确选择search，2.781s |

3次BGE-M3均返回1024维有限非零向量，耗时0.221–0.288s；同主题余弦0.753，高于无关天气0.365。聊天模型与所选模型一致，增量拼接和terminal正文一致。可机器核对的记录见[脱敏摘要](assets/2026-11-04-sponsored-live-summary.json)。单轮小样本时延不是稳定SLA；本轮没有跑满128K/256K真实上游窗口。

首次测试两模型都仅返回1条文案。定位为SDK强制 `response_format=json_object` 与项目根数组Prompt冲突。修正签名契约与Rust请求构造后重跑得到上表30/30；因此这项旧失败属于接入格式问题。

质量仍有不足：两模型都从很少的Rust标题推断大量人格/人生阶段；8B给INTJ置信度0.85，4B给0.75。4B补造“近期讨论”作为时间证据，8B也无依据地赋予近期属性。30条文案虽完整，仍出现未被摘要支撑的内容评价。现有Core字段、日期证据和画像校验继续执行；原始模型输出不能作为事实全过的证据。本轮API13/13成功不等于业务准确率100%。

补充2次默认sparse-json评估也通过了真实Rust/API流式调用。使用项目生产解析器、严格本地编号绑定和 `_apply_evaluator_result`：8B评分0.95/0.15，4B评分0.85/0.0，均被Core接收；两模型补出的“近期讨论”均不在输入里，Core清空该证据并把该候选的时间分类降为unknown。补测初版脚本遗漏了生产解析器，对4B的 `results` 包装误报异常；修正测试后重跑通过，未修改生产解析规则。

## 本地自动化

- 四端浏览器：真实uvicorn配置API、真实保存/应用与重启；只有模型IPC peer替换为合成测试进程。初始化不要求Key；PC选4B保存、探测实际模型并重启恢复；插件选4B、手机选8B保存/探测通过。2项通过。
- 免费路由/安装/API/打包与CLI重点回归：144项通过。覆盖免费互备、禁止默认付费回退、流中断不重放、上下文不裁剪、严格模型枚举、BGE隔离、验签前禁止执行、失败升级保留原指针，以及Mac签名保留SDK字节。
- 扩展构建与资源检查通过，扩展1857项测试通过；MyPy检查384个源文件通过。
- Rust默认测试67项通过、1项忽略，development特性66项通过、1项忽略，fmt/Clippy通过；发行辅助6项通过。离线发行使用假Key完成本机release构建、独立公钥验签、目录导出、原生自检、原子激活与桌面资源准备。Python wheel已确认包含发行目录和共享设置资源。
- 后续浏览器/资源/安装回归43项通过；策略生成4项通过，安装文档及三语言一致性52项通过。手机全局appearance样式曾隐藏复选框，已修复并重新截图确认，扩展重新构建通过。
- 本机全量pytest完成：12,419通过、70跳过、9失败（58分09秒）。其中策略/安装文档断言已修正并通过定点回归；Windows子进程无窗口及组件安装18项通过；旧配置回填兼容修正后初始化与免费模型浏览器66项通过。剩余两个首页失败在干净main `f4af7744995fe053df6cb771738a88d58c419b4a` 快照独立复现：库存期待30但实际27、配置重试后平台Tab未出现，与此前v0.3.233本机验收记录一致。没有把这两个既有失败计为通过。

- GitHub Core CI（提交`b8f4079d`）全部通过：Python 12,268通过、156跳过；浏览器64通过；Windows HKCU启动与Firefox扩展构建通过。[运行记录](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/41826963178)。这与本机已知两个首页失败分别记录。

## 界面记录

以下截图使用独立合成配置/IPC peer，只验证页面呈现；真实上游结果见上表。[初始化](assets/2026-11-04-sponsored-setup.png)、[PC设置](assets/2026-11-04-sponsored-desktop.png)、[手机设置](assets/2026-11-04-sponsored-mobile.png)。

## 生产SDK发行

私有发行运行41830285859的五平台构建、Rust测试和原生策略自检均通过。Mac ARM与Intel均完成Developer ID签名和Apple Accepted公证，最终字节重新签manifest。Windows与Linux使用独立Ed25519 manifest；Windows没有Authenticode证书，不声称具备系统签名。全部归档通过独立公钥、固定Core/Runtime SHA、任务模型和有效期核对；GitHub上传后的SHA256与本地一致。[审计摘要](assets/2026-11-04-sponsored-release-audit.json)。

已发布的SDK不会改变应用latest release；主程序仍由PR336交付。当前Core兼容版本为0.3.233，策略有效90天，轮换/续期需新组件和目录。生产单安装预算是本地公平使用上限，不改变供应商实际账户额度。

原生五平台下载、验签、自检、重复安装、桌面stage与损坏更新保护由[Sponsored runtime installation CI](https://github.com/whiteguo233/OpenBiliClaw/actions/workflows/sponsored-runtime-smoke.yml)执行；同一工作流在Linux x86_64/arm64实际构建Dockerfile并在容器运行SDK自检，均不发模型请求。桌面整包另使用[Build Desktop Installers](https://github.com/whiteguo233/OpenBiliClaw/actions/workflows/build-installers.yml)的冻结应用/安装验证。

图片理解按用户确认是可选增量能力，本轮不以图片向量作为免费方案通过条件。


## 已发布组件的本机完整路径

Mac Intel从真实公共发行URL下载成功，通过独立验签、原生自检、原子激活、重复安装复用、桌面stage和损坏更新保留原组件。[安装摘要](assets/2026-11-04-sponsored-published-install.json)。补测发现独立bootstrap/打包进程未初始化全局网络状态时会忽略用户network策略，GitHub连接曾两次超时；改为按保存的system/direct/custom配置创建下载client后重新通过，相关网络隔离与安装40项回归通过，打包/安装重点96项通过。

随后使用隔离的真实uvicorn后端与生产SDK，配置不填Key、不注入mock IPC：PUT配置选4B→应用→调用流式+BGE成功（9.267秒）；再选8B→应用→调用成功（18.418秒），均返回所选模型且禁用免费互备和付费回退。重新启动后端仍保留8B且组件可用，临时数据与本测试账本Keychain项已清理。[配置与真实调用摘要](assets/2026-11-04-sponsored-published-settings.json)。这些是本次连通性耗时，不是模型性能排名或稳定SLA。

## 桌面整包验收修复

首次预览构建的Windows冻结应用可启动，但真实HKCU生命周期测试未找到应注册的登录项。根因是桌面入口自动设置的 `OPENBILICLAW_SPONSORED_BUNDLE` 被现有自启动守卫当作登录后会丢失的外部配置。守卫现在仅豁免冻结程序可重新发现的、存在且与随包目录一致的SDK路径；源码或外部覆盖仍受保护。新增两种桌面布局与三种不可恢复路径回归，本机自启动与桌面入口回归85项通过、7项平台条件跳过，Ruff和384文件MyPy通过；真实整包生命周期继续作为验收门槛。

全量CI运行41833497748为12,270通过、156跳过、1失败：原有库存配额测试把五条内容设为同分，却断言固定两条ID被压制。模拟写入跨秒可稳定复现，生产排序本来就先比较评分时间再比较ID；相关生产代码和测试在本分支此前均未改动。将测试中的B站评分设为0.95至0.91（仍全部高于抖音0.66），保留全部配额与具体ID断言，使这项配额测试不再依赖写入恰好同秒；未改变生产排序。同一模拟跨秒场景修正后通过，storage整模块183项通过。
