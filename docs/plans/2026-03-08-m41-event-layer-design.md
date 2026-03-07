# M4.1 事件层设计

**目标**

为记忆系统补齐事件层：`MemoryManager` 通过 SQLite 持久化用户行为事件，并支持按时间、类型、关键词查询和按类型统计。

## 范围

- 以 SQLite `events` 表作为事件层唯一真源
- `MemoryManager` 提供统一事件入口和查询/统计入口
- 事件类型先覆盖 `view`、`search`、`favorite`、`like`、`comment`、`click`、`feedback`
- 不在本阶段实现偏好分析、画像更新或 LLM 推理

## 架构

- `Database` 负责事件底座能力：
  - 写入事件
  - 条件查询事件
  - 按类型统计事件数量
- `MemoryManager` 负责编排：
  - `initialize()` 初始化数据库和文件层
  - `propagate_event()` 校验并写入 SQLite
  - `query_events()` / `get_event_stats()` 委托到底层数据库
- `event.json` 不再承担事件主存储职责，不要求和 SQLite 双写

## 数据模型

事件输入保持轻量 `dict[str, Any]`：

- `event_type`: 事件类型
- `url`: 可选
- `title`: 可选
- `context`: 页面/来源上下文
- `metadata`: 事件特有附加信息
- `created_at`: 可选，默认由数据库生成

## 查询规则

- 类型过滤：支持单个或多个事件类型
- 时间范围过滤：`start_time` / `end_time`
- 关键词过滤：匹配 `url`、`title`、`metadata`
- 结果顺序：按 `created_at DESC, id DESC`
- 统计输出：`dict[str, int]`

## 测试策略

- 先写 `Database.query_events()` / `count_events_by_type()` 失败测试
- 再写 `MemoryManager.propagate_event()` / `query_events()` / `get_event_stats()` 失败测试
- 只做单元测试，使用临时 SQLite 文件，不依赖外部服务

## 验收对应

- `memory_manager.propagate_event(event)` 后事件能写入数据库
- 能按时间和类型查询事件
- 能返回某时间段内的事件类型统计
