# 动态 Discovery 来源续建验收（2026-10-22）

本轮目标为 discovery-only：通过聊一聊动态添加网站与 RSS/Atom/JSON Feed 来源，
在同一 Discovery 管线定时采集、评估入池，并在配置页管理独立份额、频率与启停。
继续使用 `feat/dynamic-discovery-sources` 独立 worktree；起点为 `e88965bb`，
整合当前 main `8b99ed46`（v0.3.231）。主工作区的 `outputs/`、`promo/` 未跟踪文件不属于本任务。

## 主线整合

合并保留 TikTok/Instagram、国际化、蓝鱼及运行时恢复改动，同时保留 Web/custom
注册、独立份额与三端展示。API canonical 来源顺序为 main 原序加末尾 Web；UI 展示顺序
可以不同，初始化 roster 继续排除 Web。HTTP 依赖取双方约束交集 `httpx[socks]>=0.28,<1`，
保留默认 Playwright driver 和 main macOS 签名/版本构建规则；`uv lock --offline` 成功。

所有 Python 检查以当前 worktree `src` 导入。需要配置的回归使用临时 cwd/config/data，
不加载生产账号或画像。合并阶段未启动生产服务、浏览器账号任务或发布。

| 验证 | 结果与边界 |
| --- | --- |
| MyPy / Ruff | 全局 349 个源码文件类型检查通过，Ruff 通过 |
| 文档与打包 | 99 passed；`/tmp/dynamic-source-merge-20261022-docs-packaging.log` |
| 前端 | 扩展全套 1833 passed，Web JS 142 passed，typecheck、Chrome/Firefox 构建与资产 preflight 通过；`/tmp/dynamic-ui-merge-*` |
| 配置、认证、CLI、OpenClaw、custom | 原始 1022 passed / 40 skipped / 1 failed；失败为精确 dispatcher 集合漏 main 新平台，更新联合契约后整文件 12 passed；`/tmp/dynamic-merge-backend-tests.log`、`custom-contract.log` |
| API/平台兼容 | 原始 269 passed / 4 failed；旧顺序和 CLI 文案断言更新联合契约后两整文件 67 passed；`/tmp/dynamic-merge-backend-compat.log`、`adjusted-contracts.log` |
| 运行时与配额 | 原始 374 passed / 1 failed，核心 refresh 另 205 passed；API roster 误用 UI 顺序，恢复 main 原序加 Web 后相关两整文件 21 passed；`/tmp/dynamic-source-merge-runtime-result.json` |
| Python 前端契约 | 原始 75 passed / 1 failed，同为 API roster 问题，上述 21 项复跑覆盖；原失败日志保留 |

各组有重叠，不能把通过数相加当作全量测试数；上述均为合并定向验证，新增功能及最终全量结果另记。

## 待补齐的实际产品缺口

- 聊天中旧 `create_source` 路径与动态来源流程竞争，RSS 可能落入旧聚合 Web 份额。
- 暂停自动调度时，普通手动刷新不执行自定义来源；搜索流程还需要关键词准备与有界评估。
- 配置页操作 A 来源后重绘会丢失 B 来源的未保存编辑；人工接管轮询与按钮操作存在竞争。
- RSS/Atom/JSON Feed 解析已存在，但需验证直接订阅、网站发现订阅、无标题条目与跨域文章等实际边界。

此前验收不能代替本轮修改后的验证，尤其是真实聊天、实际定时采集、模型评估与安装产物。
历史记录见[动态来源验收](../platform-source-acceptance.dynamic-web.md)。
