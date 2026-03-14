# 数据库修复与防损坏设计

## 背景

当前本地后端使用单文件 SQLite 数据库 `data/openbiliclaw.db` 保存：

- `events`
- `content_cache`
- `recommendations`
- `schema_version`

本次排障已经确认两件事：

1. 当前线上使用的 `openbiliclaw.db` 已出现页级损坏，`PRAGMA integrity_check` 明确报 `database disk image is malformed`
2. 损坏前日志里先连续出现了大量 `database is locked`，说明数据库访问模型本身已经存在明显脆弱点

这直接导致：

- `openbiliclaw start` 可能在初始化阶段就启动失败
- discovery 虽然找到了内容，但无法写入 `content_cache`
- popup 点“换一批”时会因为后端异常走失败提示，而不是正常的“池子里没新内容”

## 目标

- 提供显式、可控的数据库修复入口，尽量保住已有数据
- 将修复流程设计成“先备份、后恢复、原子切换”，避免二次损坏
- 降低 SQLite 在当前运行模型下再次损坏的概率
- 增加定期冷备份，至少让未来出现问题时有最近回滚点

## 非目标

- 不承诺在任何断电、强杀、文件系统异常下 SQLite 绝对不损坏
- 不把 SQLite 替换成别的数据库
- 不在本次把所有运行状态都迁出 SQLite
- 不自动在 `start` 里隐式执行高风险修复

## 方案概述

### 1. 新增显式 CLI：`openbiliclaw db-repair`

`db-repair` 是唯一正式的数据库修复入口，执行原则：

- 先检查
- 再备份
- 再恢复
- 成功后原子切换
- 失败时保留原始文件

该命令必须在数据库未被运行中服务占用时执行；如果检测到当前 `openbiliclaw start` 或其他本地进程仍持有该库，会拒绝继续。

### 2. 恢复流程采用“最大保留数据”的保守策略

修复顺序：

1. 检查 `openbiliclaw.db` 与 `openbiliclaw.db-wal`
2. 运行 `PRAGMA integrity_check`
3. 若正常则直接退出
4. 若异常则先备份原始文件到 `data/backups/`
5. 尝试把旧库里还能读出的 schema / data 导出到新库
6. 对新库再做完整性检查
7. 成功后用新库替换正式库，旧库继续保留

关键原则：

- 恢复期间不覆盖原库
- 不沿用旧 WAL
- 任何失败都只中止并报告，不做“半修复覆盖”

### 3. 防下次再坏：收口数据库访问模型

排障显示问题不是单一 SQL 失败，而是访问模型脆弱：

- 同一运行时里存在多份 `Database(...)` 连接
- 不是所有写路径都统一经过 `_execute_write()`
- 运行时刷新与事件写入可能重叠，先产生大量锁冲突，再进入更差状态

因此本次根因治理包括：

#### 3.1 共享单数据库实例

在 API 运行时、CLI 运行时上下文内优先共享同一个 `Database` 实例，不再在同一进程里随意重新 `Database(...).initialize()` 多次。

这一步不要求全项目一次性重构成完整 DI 容器，但至少要把：

- `MemoryManager`
- API app runtime
- CLI runtime builders

这些高频路径的数据库实例关系理顺。

#### 3.2 所有写操作统一走一个写入口

现在 `_execute_write()` 已经有：

- `database is locked` 重试
- commit 封装

但并不是所有写操作都经过它。后续要统一：

- 插入
- 更新
- 标记 presented / feedback / notification_sent
- runtime 状态更新相关写操作

都走同一套写接口。

#### 3.3 启动时做健康检查，但不自动修复

`start` 或底层初始化时可以做轻量健康检查：

- 正常：继续
- 明确损坏：直接报错，并提示执行 `openbiliclaw db-repair`

不在启动路径里偷偷修库，避免：

- 启动动作带隐式高风险副作用
- 正在运行时误触发修复
- 修到一半失败导致情况更复杂

### 4. 增加定期冷备份

备份分两层：

#### 4.1 高风险操作前强制备份

这些操作执行前必须备份：

- `db-repair`
- 未来 schema migration

#### 4.2 正常运行时按周期自动备份

在本地 API 启动后，若满足：

- 距离上次备份已超过阈值（默认 24h）
- 当前数据库完整性检查通过

则自动生成一份冷备到 `data/backups/`

建议默认保留策略：

- 最近 7 份日备份
- 最近 4 份周备份

超出后自动清理，避免无上限堆积。

## 数据与模块设计

### 新增模块建议

建议新增独立的运行时数据库维护模块，例如：

- `src/openbiliclaw/storage/maintenance.py`

职责：

- 完整性检查
- 备份创建
- 备份轮转
- 修复恢复
- 进程占用检测

避免把高风险维护逻辑全部塞进 `database.py`

### CLI 接口

建议新增：

```bash
openbiliclaw db-repair
```

行为分三类：

- 无需修复：输出“数据库正常”
- 修复成功：输出备份位置、恢复位置、是否已切换
- 修复失败：输出失败阶段、原库保留位置、下一步建议

如果检测到库正在被占用：

- 直接报错
- 提示先停掉 `openbiliclaw start`

## 风险与取舍

### 风险 1：损坏库部分可读、部分不可读，恢复结果不完整

处理：

- 恢复命令明确统计每张表恢复出的行数
- 恢复成功但有损失时要给出可见提示
- 原始坏库始终保留

### 风险 2：自动备份本身影响启动速度

处理：

- 只在超过时间阈值时执行
- 先做轻量检查，再决定是否备份
- 不在每次写库时备份

### 风险 3：只修“锁重试”而不改访问模型，问题还会回来

处理：

- 本次明确把共享实例和统一写路径纳入范围
- 不接受只加更多 retry 的表面修补

## 测试策略

- `storage`：
  - 完整性检查正常路径
  - 锁冲突重试路径
  - 统一写路径覆盖
  - 备份轮转
- `CLI`：
  - `db-repair` 正常库直接退出
  - 占用中拒绝修复
  - 损坏库恢复成功
  - 恢复失败不覆盖原库
- 若恢复逻辑拆模块：
  - 独立单元测试恢复计划与文件切换逻辑

## 影响文件

- `src/openbiliclaw/storage/database.py`
- `src/openbiliclaw/storage/maintenance.py`（新增）
- `src/openbiliclaw/memory/manager.py`
- `src/openbiliclaw/api/app.py`
- `src/openbiliclaw/cli.py`
- `tests/test_storage.py`
- `tests/test_cli.py`
- `docs/modules/cli.md`
- `docs/modules/config.md`（如新增备份配置）
- `docs/architecture.md`（如需要补数据库共享机制）
- `docs/changelog.md`
