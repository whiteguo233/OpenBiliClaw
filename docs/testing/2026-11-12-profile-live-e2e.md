# 两免费模型真实端到端验收（2026-11-12）

修复分支：`fix/initial-profile-completeness`。生产画像修复为 `a47c30cc`，本轮完整验收脚本为 `6bd740d5`。2026-11-12 合入 main，尚未发布。

## 验收路径

使用独立 GitHub Actions Linux / macOS 原生运行器，安装生产后端和正式验签 SDK；只配置当前被测免费实例，不配置自有模型。输入来自 `whiteguo233` 的真实公开 GitHub Star，配置采样上限 40 条。未注入事件，未替换模型/来源/HTTP 响应；没有上传本机 B站 Cookie 或私有历史。

实际流程：Chrome 桌面页面点击初始化 → `POST /api/init` → 官方公开账号 API 采集 → 偏好分析 → 画像构建、保存 → 内容发现、评估、推荐表达 → `/api/profile-summary` → 桌面/手机页面 → CLI → 停止并重新启动后端 → 同一画像读回。

macOS 使用运行器自己的临时加密钥匙串；本机 macOS 26 的登录钥匙串访问失败没有通过修改访问控制绕过。生产 SDK 的验签与保护策略保持启用。

## 结果

最终运行：[43056781359](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/43056781359)，四个任务全部 `success`；[运行与制品清单](assets/profile-live-20261112/run-manifest.json)。

| 环境 | 模型 | 已入库公开事件 | 画像 / 页面 / CLI / 重启 | 初步特质 / 需求 / 价值 / 驱动力 | 觉察 / 洞察 | 记录的模型调用 | 总耗时 |
| --- | --- | ---: | --- | --- | --- | ---: | ---: |
| Ubuntu 22.04 | Qwen3-8B | 40 | 通过 | 3 / 3 / 3 / 3 | 6 / 4 | 9 | 314.19 秒 |
| Ubuntu 22.04 | Qwen3.5-4B | 40 | 通过 | 3 / 3 / 3 / 3 | 5 / 5 | 11 | 362.15 秒 |
| macOS 15 Intel | Qwen3-8B | 40 | 通过 | 3 / 3 / 3 / 3 | 6 / 4 | 9 | 418.72 秒 |
| macOS 15 Intel | Qwen3.5-4B | 40 | 通过 | 3 / 3 / 3 / 3 | 4 / 3 | 8 | 329.39 秒 |

调用数来自持久化 `llm_usage`，不是所有失败尝试/Embedding 请求的计费总数。四组均只记录各自被测免费模型；含 `soul.preference.chunk`、`soul.profile_build`、`discovery.evaluate_batch`、`recommendation.write_expression`，Linux 4B 另有正常 `soul.speculate` 调用。每个组合仅运行一次成功样本，耗时不代表延迟 SLA。

各组同时核对核心特质、需求、价值、驱动力、认知风格、当前内容关注与兴趣非空。四个推测区块保留「待确认」，未知 MBTI 显示说明。已有觉察与洞察在摘要中可见，画像文件与 API 一致，重启后同一组字段完全一致。

### 来源警告的边界

40 条是显式测试样本上限，不代表公开收藏全量。后端仍返回 `github_partial`、阶段 1 `warning`、`partial_success=true`，未篡改为全量成功。只有实际入库恰好 40 条、唯一来源警告为 `github_partial`、阶段 2/3/4 全部 `ok` 时，验收接受这一预期采样状态。短采集、其它 warning、模型阶段失败、发现超时或推荐未就绪仍使验收失败。

本轮覆盖公开 GitHub 来源的完整初始化链路；不等同于私有 B站登录态采集、扩展商店安装或所有平台的账号验收。

## 证据

归档 CLI 文本仅清理行尾空白，正文不变；原始字节可从上方 CI 制品下载。JSON 和截图按原始制品保留。

- [macOS 8B 报告](assets/profile-live-20261112/macos-8b/report.json)、[画像摘要](assets/profile-live-20261112/macos-8b/profile-summary.json)、[桌面截图](assets/profile-live-20261112/macos-8b/desktop.png)、[手机截图](assets/profile-live-20261112/macos-8b/mobile.png)、[CLI](assets/profile-live-20261112/macos-8b/cli.txt)。
- [macOS 4B 报告](assets/profile-live-20261112/macos-4b/report.json)、[画像摘要](assets/profile-live-20261112/macos-4b/profile-summary.json)、[桌面截图](assets/profile-live-20261112/macos-4b/desktop.png)、[手机截图](assets/profile-live-20261112/macos-4b/mobile.png)、[CLI](assets/profile-live-20261112/macos-4b/cli.txt)。

- [Linux 8B 报告](assets/profile-live-20261112/ubuntu-8b/report.json)、[完整画像摘要](assets/profile-live-20261112/ubuntu-8b/profile-summary.json)、[CLI](assets/profile-live-20261112/ubuntu-8b/cli.txt)。
- [Linux 4B 报告](assets/profile-live-20261112/ubuntu-4b/report.json)、[完整画像摘要](assets/profile-live-20261112/ubuntu-4b/profile-summary.json)、[CLI](assets/profile-live-20261112/ubuntu-4b/cli.txt)。

Linux 的截图文件同目录保留，但运行器没有中文字体，截图汉字显示为方框；Unicode DOM 内容断言与 API 正常。视觉核对使用 macOS 截图，不能把这组 Linux 截图描述为中文字体显示已验收通过。

## 首轮问题与恢复

首轮运行：[43054536636](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/43054536636)。

- 两个 macOS 模型实际完成阶段 2/3/4；最初脚本要求 `partial_success=false`，将配置的 40 条采样上限警告误判失败，尚未执行后续字段/重启断言。保留 [8B 首轮报告](assets/profile-live-20261112/first-macos-8b-report.json) 和 [4B 首轮报告](assets/profile-live-20261112/first-macos-4b-report.json)。
- 两个 Linux 任务因 Azure APT 镜像 DNS 解析失败，停在浏览器依赖安装，还未调用模型。改用运行器预装的真实 Chrome 后重跑。
- 脚本修正增加了有界样本判断的 5 项回归，并在失败时保存实际用量。画像、初始化、本地化与该判断共 95 项本地回归通过；Ruff、MyPy（414 个源文件）和本地化目录一致性检查通过。
- 本次也补齐新 MBTI 说明及待聊文案的简/繁/英文目录，清理废弃键并规范排序。

## 复跑

```sh
PYTHONPATH=src OBC_LIVE_BROWSER_CHANNEL=chrome python scripts/profile_initialization_live.py \
  --root /tmp/obc-live-new --model Qwen/Qwen3-8B
```

根目录必须全新；另一模型使用 `Qwen/Qwen3.5-4B`。没有预装 Chrome 时，先按 Playwright 常规流程安装 Chromium，并省略浏览器 channel 环境变量。仅上传 `evidence/` 白名单文件；配置、数据库、SDK 和服务器原始日志均不在公开归档内。

## 合入收尾（2026-11-12）

以 `738d51d4` 为 main 基线、`e91560e8` 为修复分支末端，在独立合并 worktree 形成候选；自动合并无冲突，保留 main 的弹窗设置布局修复。合入时将真实请求工作流改为手动触发，删除已经结束的修复分支 push 入口。同步插件两条旧测试组的三个空态文案预期，恢复当前后台补货语义的完整 Node 回归。

合入候选检查：后端相关回归 **770 passed**（72.16 秒），插件完整 Node 回归 **1907 passed / 0 failed**；Ruff、MyPy（414 个源文件）、3273 条 UI 目录一致性、版本一致性（后端/插件 0.3.237）、插件 typecheck/build 与 22 个 manifest/WAR 入口资产校验均通过。两个既有插件文案失败在本轮同步预期后已消除。

已有两模型 × 两系统四组真实验收所覆盖的画像与模型调用代码与本轮候选一致；本轮仅整合主分支弹窗布局、收尾文档、工作流入口和旧测试预期，没有重跑相同的上游请求。

清理范围仅限本任务的修复/合并分支与 worktree；主工作区已有的 `outputs/`、`promo/` 及其它任务的 worktree 保留。版本保持 0.3.237，未创建新发行，也未重启正式服务。
