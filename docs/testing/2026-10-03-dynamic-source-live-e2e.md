# 动态网站来源：完整服务与真实界面复验

本轮在 `feat/dynamic-discovery-sources` / `0c983b19` 基础上继续验证；后续修复包括运行
计数文案、惊喜卡来源归属、浏览器诊断和 CI 覆盖。各项最终状态以[验收账本](../platform-source-acceptance.dynamic-web.md)为准。

## 服务与数据边界

- 服务为 `http://127.0.0.1:18485`，使用真实 `create_app()`、`RuntimeContext`、lifespan、
  Agent 工具、SSE、调度器、评估队列和数据库；没有 mock routes 或 FakeLLM。
- 独立根 `/tmp/dynamic-source-live-product-qsdnjsn7`，只创建合成 Django/Python 兴趣画像；
  不复制真实用户历史，不写生产配置、数据库或记忆。
- 沿用当前 provider/model/route、Ollama embedding、90 秒评估聚合和既有评分门槛。
  真实调用含 `deepseek-v4-flash` 与配置中的 `sensenova-6.8-flash-lite` 回退；
  HTTP 429、空 JSON 及输出长度重试按实际记录保留。
- 关闭内置平台采集与账号同步；自定义来源保持正式调度。设置页曾只读查询到 rdt-cli
  全局凭据状态，未输出或使用凭据访问 Reddit；后续隔离启动把该状态文件路径指向空目录。
- 浏览器均为独立测试 profile。插件以 unpacked 方式加载当前分支构建产物；先离线启动，
  在扩展独立 storage 设置 18485 后才联网，没有使用用户日常浏览器的登录资料。

## 实际操作

桌面真实页面发送：

> 我想把 Django 官方博客加成一个独立的 Discover 来源，名称叫 Django 官方博客。
> 以后定期寻找 Django 发布、安全公告和 Python Web 开发相关的新内容，和其他来源一样按份额入池；
> 份额设为 2，每 5 分钟检查一次。请帮我接入，缺什么信息就问我。

模型选择官网 `https://www.djangoproject.com/weblog/`，实际调用准备工具并展示两条试跑样例。
没有为测试强制模型追问已能确定的网址。此轮采用 HTTP recipe，不冒充浏览器执行证据。
回复明确待确认、尚未启用，并说明试跑不代表入池；审批摘要显示正确网站、目标、2 份和 5 分钟。
在真实“待审批”界面点击“批准并执行”后才创建来源。

来源 ID 为 `custom_bb846cab60fd4a5e81fbf142e785cd0f`，workflow
`cf96dcf8-a23d-4bb5-80cc-c2c9268cf9cb`、version 1。桌面设置实际将份额 2 改为 3 保存，
再恢复为 2；移动设置读取为 2 份 / 5 分钟。未改变全局配置或评分门槛。

## 自然到期运行

以下为 UTC 时间；未使用 force、修改时钟、回拨 next-run 或直接调用 producer：

| 运行 | 开始 | 完成 | 结果 | 发现 / 待评估入队 |
| --- | --- | --- | --- | --- |
| 1 | 06:12:56.873 | 06:13:00.185 | ok | 10 / 10 |
| 2 | 06:18:00.212 | 06:18:02.983 | not_modified | 0 / 0 |
| 3 | 06:23:03 | 06:23:05 | not_modified | 0 / 0 |
| 4（重启后） | 06:28:16.741 | 06:28:20.602 | not_modified | 0 / 0 |

四轮后 seen 与候选均为 10，没有重复入队。后台有效份额只有该自定义来源，target 300；
旧版 Web 虽在配置中启用，但没有 enabled recipe，因此不占有效后台 quota。
设置页的配置比例预览与后台按可用来源计算的实际目标不是同一统计口径。

真实评估用时 673.75 秒，最终使用原配置中的 sensenova-6.8-flash-lite 回退成功：
10 条 evaluated，2 条 cached、8 条低分拒绝；评分门槛未改变。入池项分别为 Django 6.1.1
bugfix（0.88）和 technical governance（0.62），后续分别进入惊喜与普通推荐展示。

06:26:52 正常停止独立服务，06:27:30 同一数据目录重启健康；来源、version、run、next-run、
cursor SHA、seen 和两条缓存均一致，第四轮自然到期继续运行。06:30:43 在真实移动页点击
“停用”，服务继续运行至 06:34:50，已超过原下一到期时间 90.24 秒（超过一个 60 秒 tick）；
run 仍为 4、seen 仍为 10、缓存仍为 2，没有后续抓取。没有以停掉进程冒充来源停用成功。

## 三端及文案修复

桌面、390px 移动 Web 和 430px unpacked 插件均连接真实 18485 服务，显示相同来源和调度状态。
移动为浏览器视口模拟，不是实体手机或 iOS Safari。插件验证的是当前分支 unpacked 安装态，
不表示商店发布或桌面安装器已验证。

实测发现运行记录把 `enqueued_count` 写成“入池”，但此时真实候选仍在评估中。动态来源、
桌面旧 recipe 和插件旧 recipe 统一改为“入队待评估”，并给 `not_modified` 加中文说明。
此修复没有改变抓取、准入或评分结果。共享组件测试明确断言不能把入队数称作入池数。

同轮惊喜卡暴露另一个真实缺陷：普通推荐已有网站名，惊喜单条 DTO、批量及手动/后台推送
遗漏 `source_id/source_name`。补齐全部出口并增加从真实候选入库至 API/runtime 投影的回归，
避免只在前端补一个固定标签。

截图与操作快照位于本地忽略目录 `output/playwright/dynamic-sources-live/`：
`chat-approval.png`、`source-live.png`、`mobile-source.png`、`extension-source-history.png`。
其中插件首张历史截图保留了修复前 `not_modified` 原文；计数已经使用“入队待评估”。

## 复核资料

隔离服务根中的 `observations.jsonl`、`latest-evidence.json`、`third-cycle.json`、
`effective-quota.json` 是只读采样生成的证据；不记录 provider 密钥、Cookie、密码或完整模型提示。
测试结束后的最终结算、重启、停用与环境结果同步至验收账本。

身份修复后，真实刷新桌面、移动和 unpacked 插件，普通与惊喜卡均显示“Django 官方博客”。
三张最终截图为 `desktop-recommendation-final.png`、`mobile-recommendation-final.png`、
`extension-recommendation-final.png`，已经实际查看；`evidence-manifest.json` 记录截图和
请求清单的 SHA-256。重启期间出现过连接断开日志，恢复后页面请求正常，插件刷新后无控制台错误。

06:41:48Z 完成清理：两测试浏览器、18485 服务和观察器均停止，端口关闭；复制的模型密钥
配置及三份原始服务日志已删除，保留合成数据库与脱敏证据。详情在隔离根的
`acceptance-summary.json` 和 `cleanup-record.json`。正式服务、生产配置和数据未修改。

最终冻结代码的生命周期、真实浏览器执行器及惊喜来源身份回归为 48 passed；扩展全套
1539 passed，Ruff、格式检查和 MyPy 通过，wheel 已重建并核对七个本轮产品文件逐字节一致。
全量 Python 的原始失败及独立重跑原因均保留在验收账本，不用定向回归冒充单次全量全绿。
