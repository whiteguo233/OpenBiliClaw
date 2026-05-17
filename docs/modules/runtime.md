# Runtime Module

## 概述

`src/openbiliclaw/runtime/` 负责后端 daemon 的长期运行能力：后台刷新、账号同步、运行时事件流、浏览器插件 presence gate、自动更新和任务生命周期管理。FastAPI 启动后会通过 `RuntimeContext` 持有这些 runtime 服务，配置热重载时重建可替换组件。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 后台刷新控制 | ✅ | `ContinuousRefreshController` 按 scheduler 配置补充候选池，并通过 source policy 计算各平台有效配比。 |
| 浏览器 presence gate | ✅ | `background_llm_work_allowed()` 结合 `scheduler.enabled` 与 `pause_on_extension_disconnect` 控制 daemon-owned 后台 LLM / embedding 工作。 |
| Runtime event stream | ✅ | `/api/runtime-stream` 向扩展推送状态、Cookie sync 请求、配置重载和 presence 事件。 |
| 自动更新 | ✅ | `AutoUpdateService` 周期性检查 backend git tag，发现新 backend 版本后执行 `git pull --ff-only` 与依赖同步。 |
| 账号同步 | ✅ | `AccountSyncService` 同步 B 站账号历史、收藏和关注等信号。 |

## 公开 API

```python
from openbiliclaw.runtime.updater import AutoUpdateService

service = AutoUpdateService(enabled=True, check_interval_hours=6)
result = await service.check_and_update_now()
```

`AutoUpdateService.check_and_update_now()` 返回字典结果：

- `{"checked": False, "reason": "disabled"}`：自动更新关闭。
- `{"checked": True, "updated": False, "reason": "no_backend_tag_yet"}`：GitHub tag 列表中没有可用 backend tag。
- `{"checked": True, "updated": False, "current_version": "...", "remote_version": "..."}`：已是最新 backend 版本。
- `{"checked": True, "updated": True, ...}`：已应用更新并尝试重启当前进程。

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.auto_update_enabled` | `false` | 是否启用后台自动更新检查。 |
| `scheduler.auto_update_check_interval_hours` | `6` | 自动更新检查间隔。 |
| `scheduler.enabled` | `true` | 后台 LLM / embedding 总开关。 |
| `scheduler.pause_on_extension_disconnect` | `false` | 浏览器插件断开后是否暂停后台 LLM / embedding 工作。 |
| `scheduler.extension_disconnect_grace_seconds` | `90` | 插件断开后的宽限秒数。 |

## 设计决策

### Auto-update release contract

后端自动更新只认 backend source tag：

- backend 源码更新发布为 git tag：`backend-vX.Y.Z`。
- legacy 安装仍兼容 `vX.Y.Z` 和裸 semver `X.Y.Z`。
- 浏览器扩展 release 使用 `extension-vX.Y.Z`，必须被后端自动更新忽略。
- GitHub `/releases/latest` 当前由扩展 artifact 占用，不能代表后端源码版本；`AutoUpdateService._fetch_latest_version()` 直接查询 `/tags`，分页过滤 backend tag 后选择最高版本。

这样可以避免后端 `0.3.64` 把 `extension-v0.3.24` 解析成 `(0,)` 并误报 "Already up-to-date"。
