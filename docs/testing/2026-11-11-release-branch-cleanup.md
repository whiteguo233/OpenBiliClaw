# 独立插件发布分支合入与清理（2026-11-11）

## 范围与来源

在独立 worktree 中，将 `release/extension-v0.3.236` 的两个提交合入当前主线；
使用 merge 保留原提交历史，便于删除分支后继续追溯提审记录。

| 项目 | 提交或状态 |
| --- | --- |
| 合入基线 main | `ed6730aa4e644b4c94c0bfb7467256a38dd2f7c3` |
| 独立插件发布提交 | `2635eb7dfb545351ca9b3f228b6f05807c136f79` |
| 发布分支最终提交 | `ff69a77c96d68bea788d23f35a1799edf53df68d`，补充提审记录 |
| 已有插件标签 | `extension-v0.3.236`，仍指向原发布提交 `2635eb7d` |
| 当前产品版本 | 后端与插件均保留 `0.3.237` |

GitHub API 只读复核确认，[0.3.236 插件发行页](https://github.com/whiteguo233/OpenBiliClaw/releases/tag/extension-v0.3.236)
仍公开提供 Chrome ZIP、Firefox ZIP、Safari DMG。Chrome 与 Firefox 的历史提审工作流均为
`completed / success`：[Chrome 回执](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42521629175)、
[Firefox 回执](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42521629101)。
本轮未重新提交商店，也未将历史回执解释为当前商店审核状态。

## 合并取舍

- 保留发布分支的工作流保护：组件发行的创建与更新都传 `--latest=false`，
  Latest 继续由 `openbiliclaw-v*` 聚合发行管理。
- 仅当 `RELEASE_TAG` 与 `pyproject.toml` 的后端版本一致时运行聚合同步；
  独立插件发版跳过该步骤。
- README 中的当前下载入口、后端和扩展版本文件保留主线内容，避免退回 0.3.236。
- 将 0.3.236 变更与提审记录补入 changelog 和插件模块文档，明确其历史日期；
  0.3.237 已继承初始化功能，文档不再标为待发布。

这些工作流改动适用于合入后的工作流引用；已有发行标签、下载资产和安装包保留原样。

## 验证

- `pytest -q tests/test_aggregate_release_workflow.py tests/test_release_consistency.py tests/test_release_script.py tests/test_docs_index.py tests/test_install_contract_docs.py`：54 passed。
- 新增四个实际 Bash 执行场景：创建组件发行、更新已有组件发行、版本一致时聚合同步、版本不同时跳过同步。
  测试使用临时目录与本地命令替身，不访问 GitHub 或创建真实发行。
- `ruff check src/ tests/`：通过。
- `ruff format --check src/ tests/`：1,048 个文件通过。
- `mypy src/`：414 个源文件通过。
- 工作流 YAML 解析与两个变动步骤的 `bash -n`：通过。
- README、后端与扩展版本文件与合入前主线逐字节一致。

## 清理门槛

先将合并结果写入远端 main，再确认 `ff69a77c` 是远端 main 的祖先，
最后以预期提交校验删除 `release/extension-v0.3.236`。
两个 Mac 临时发布分支已在前轮清理，它们的提交仍由 `feat/mac-app-store` 保存；
商店候选 PR #337 及其功能分支继续保留。
