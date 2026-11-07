# 免费 Provider 合并验收（2026-11-07—08）

## 范围

将 `feat/unified-sponsored-providers` 的功能与修复（截至 `8d8a5839`）和
`origin/main` 的 `e080fbe3` 集成。main 的不喜欢主题短语匹配修复完整保留，
自动合并没有冲突。本轮不新增 Embedding 路由功能，不修改模型、配额或共享凭据。

此前真实硅基流动请求、初始化、链切换、笔记读写与审批验证见
[固定实例真实验收](2026-11-07-fixed-providers-live.md)和
[Qwen 工具协议验收](2026-11-07-qwen-tool-protocol.md)。模型原始失败与延迟记录保留；
本次合入检查不把本地回归测试当作新的真实模型成功率样本。

## 首轮合并检查（e080fbe3 基线）

| 检查 | 结果 |
| --- | --- |
| `PYTHONPATH=src pytest -q` | 首轮 12,834 通过、71 跳过、4 失败；失败均为下述过期测试夹具，完整运行 48 分 54 秒 |
| 两个失败文件及固定实例 / 工具协议回归 | 修正夹具后 137 通过 |
| `ruff check src/ tests/` | 通过 |
| `mypy src/` | 通过，393 个源文件 |
| `npm test`（extension） | 1,884 通过，0 失败 |
| `npm run typecheck`（extension） | 通过 |
| `npm run build` / `npm run verify:assets`（extension） | 通过，22 个 manifest 脚本与资源校验通过 |
| `scripts/build_i18n_catalogs.py --check` | 通过，2,977 条 UI 文案 |
| `scripts/release.py --check` | 后端与插件均为 0.3.234 |
| 隔离配置执行 `openbiliclaw config-show` | 退出码 0，未读取用户正式配置 |
| `git diff --check main` | 通过 |

四个失败用例分布在 `test_sponsored_all_tasks.py` 和 `test_sponsored_user_flow.py`。
单独复现确认两处仍断言旧备用 ID `siliconflow-free-backup`，另两处仍让模拟 SDK
返回旧裸 JSON 工具调用。更新为固定 `siliconflow-free-qwen35-4b` 和当前
`<tool_call>` 标记格式，保留用户付费渠道隔离、工具参数与结果续接、流式正文无协议
泄漏的原断言；未改变运行时代码，也未恢复把普通 JSON 当作工具动作的旧行为。
复测两个完整文件，以及 `test_sponsored_tool_protocol.py`、
`test_unified_siliconflow_providers.py`，137 项全部通过。
首次失败和单独复现日志保留，不将首轮全量报告改写为零失败。

配置页手动浏览器步骤：在独立临时后端的 `/setup/` 选择免费 8B 和云端 BGE-M3，
保存后进入 `/web/?settings=models`，确认 8B 已在默认链第一项；将固定 4B 实例
加入链尾，保存后重载页面，确认两个实例与顺序持久化。检查 Embedding Provider
显示硅基流动 BGE-M3、模型 `BAAI/bge-m3`、1024 维，无需输入凭据。
本轮截图覆盖 PC 配置页；插件、移动端和此前真实请求范围见上述历史验收。
截图、完整本地检查日志和调试附件在删除 worktree 前独立归档，未提交凭据或私有 SDK。

## 同步等待期间的新主线（2026-11-08）

首轮全量检查期间，main 推进到 `16dda04c`，合入 Jev 原生网关、上下文预算、
候选停放和设置页探测。保留首轮结果后继续集成该主线，没有覆盖其提交。
6 个冲突文件涉及变更日志、文档导航、三语言目录和手机设置页；文档保留双方记录，
目录按合并后的实际 UI 重新生成。手机页保留固定免费 LLM 实例和独立 Embedding
选择，同时向 Jev 表单传入 `probeJevConfig`，不恢复已移除的独立免费方案表单。

该轮验证针对新增主线涉及的 decision / Jev / discovery / 画像 / 配置 / API，
以及免费 Provider、工具协议、实例路由、热重载和真实浏览器配置入口。HTTP / IPC
模拟端点只验证集成，不算新增真实模型验收，也不消除主线记录中的 Jev
`invalid_response` 真实请求边界。

- 后端集成回归：40 个文件 / 指定节点，共 1,271 项；首轮 1,270 通过、1 失败，
  运行 6 分 48 秒。[节点清单](2026-11-07-unified-providers-merge-tests.txt)可用于复验。
  唯一失败为主线新增 `candidate_eval_invalid_evidence_blocked` 字段未同步到
  `test_runtime_status_endpoint_returns_runtime_summary` 的完整响应断言。
  补齐默认值 `0`，保留严格字典相等校验后，`test_api_app.py -k runtime_status`
  的 5 项回归全部通过（623 项未选中）；运行时代码未作额外修改。
  两轮的原始失败日志均保留，未将分次复测描述为一次全仓零失败运行。
- Ruff、MyPy（394 个源文件）通过。
- 国际化目录 2,991 条检查通过，无缺失英文。
- Chrome 插件构建与 22 项资源校验通过；插件测试再次 1,884 通过。

最终没有未处理的测试失败。本轮未重跑整仓 48 分钟套件：首轮完整覆盖后，
新增主线、冲突接线与测试修正通过上述集成范围和原失败场景复测。

## 保留的边界

- LLM 使用默认 / 模块实例调用链；两个免费实例只读，可通过调整链顺序选择。
- Embedding 仍是独立默认端点和一个构建期备选：服务构建成功后，请求失败不会
  自动遍历多个 Embedding 实例。此次明确保留现状，尚未实现 LLM 式运行时调用链。
- 当前正式安装包构建默认集成并验证对应平台 Rust SDK、签名策略与校验文件；
  固定 SDK 仍为 `sponsored-runtime-2026-11-06.2`。Key 密文随签名策略打包，
  解密材料编入 Rust；客户端持有这些材料，不能保证凭据不可提取，生产中继未配置。
- 此次是源码合入，未创建新版本标签、发布新安装包或替换已发布资产；
  不能把合入后的配置页面当作当前下载包已经更新的证据。
