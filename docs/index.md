# 📖 OpenBiliClaw 文档导航

> 本页面是项目文档的一站式入口。

## 项目概览

- [项目规格说明书 (SPEC)](spec.md) — 完整的项目设计与规划
- [v0.1 开发任务清单](v0.1-todolist.md) — 当前版本的开发主线
- [架构设计](architecture.md) — 系统架构与模块关系
- [记忆系统设计](memory-design.md) — 多层网状记忆架构详解
- [变更日志](changelog.md) — 各里程碑交付记录

## 模块文档

| 模块 | 文档 | 对应代码 | 状态 |
|------|------|----------|------|
| LLM 多模型支持 | [modules/llm.md](modules/llm.md) | `src/openbiliclaw/llm/` | ✅ M2 完成 |
| B 站接入层 | [modules/bilibili.md](modules/bilibili.md) | `src/openbiliclaw/bilibili/` | ✅ M3 完成 |
| 记忆系统 | [modules/memory.md](modules/memory.md) | `src/openbiliclaw/memory/` | 🔄 M4 进行中 |
| 灵魂引擎 | [modules/soul.md](modules/soul.md) | `src/openbiliclaw/soul/` | 🔄 M4 进行中 |
| 浏览器插件 | [modules/extension.md](modules/extension.md) | `extension/` | 🔄 M8 进行中 |
| CLI 命令参考 | [modules/cli.md](modules/cli.md) | `src/openbiliclaw/cli.py` | ✅ 持续更新 |
| 配置参考 | [modules/config.md](modules/config.md) | `config.example.toml` | ✅ 持续更新 |

## 开发指南

- [贡献指南](contributing.md) — 环境搭建、代码规范、文档更新要求
- [AGENTS.md](../AGENTS.md) — AI 代理开发规则（含文档更新强制要求）
