# Extension Settings Tabs And YouTube Config Design

## Goal

整理浏览器插件设置页，把现在单一路径纵向堆叠的配置拆成可扫描的 tab，并补齐 YouTube 与其他内容源在配置页、后端 config/API、文档里的可配置项。

## Current Gaps

- `extension/popup/popup.html` 的设置页把 LLM、embedding、平台源、调度、日志全部堆在一个滚动列表里，字段很多时很难定位。
- 设置页只有一级 popup tab，没有设置页内部 tab；`popup.js` 也只处理主视图 tab。
- YouTube 后端配置目前只有 `[sources.youtube].enabled`。运行时已有 `yt_search` / `yt_trending` / `yt_channel` 三类 discovery 策略，但用户无法像小红书、抖音一样在配置里看见或调节这三类路径。
- `/api/config` 的 `YoutubeSourceConfigOut` 和 `PUT /api/config` 只 round-trip `enabled`，插件只能保存一个 YouTube 开关。
- `docs/modules/config.md`、`docs/modules/extension.md` 和 `config.example.toml` 的 YouTube 描述仍停留在“只有 pool-share 归属”的旧状态。

## Chosen Approach

在设置 overlay 内新增一组轻量的 settings tabs：`模型`、`平台源`、`调度`、`通用`、`日志`。每个 tab 只显示相关 `.settings-section`，保存按钮和错误/banner 留在 overlay 底部，不随 tab 切换消失。默认打开 `模型` tab；切换只影响前端可见性，不改变表单值，也不影响保存时 `collectForm()` 收集整张表单。

YouTube 配置按已有策略名对齐，而不是新增一个不存在的插件 producer：

- `enabled`: 是否让 YouTube 参与 discovery 与 pool share。
- `daily_search_budget`: 每次 runtime YouTube 搜索策略最多生成的 query 数，对应 `YoutubeSearchStrategy.queries_per_run`。
- `daily_trending_budget`: 每次 trending 策略拉取的候选上限，对应 `YoutubeTrendingStrategy.fetch_limit`。
- `daily_channel_budget`: 每次订阅频道策略最多读取的频道数，对应 `YoutubeChannelStrategy.max_channels`。
- `request_interval_seconds`: 预留给 YouTube 请求节流，当前先作为配置/API/UI/文档可见项保存，策略层暂不引入人工 sleep，避免无意义拖慢单次 discovery。

这些字段命名使用 `daily_*_budget`，和小红书 / 抖音设置页文案保持一致；实现上它们约束“单次 runtime discovery 的策略规模”，不是独立后台任务队列预算。文档会明确这个语义，避免用户误以为 YouTube 有一个和抖音一样的 plugin task producer。

## Data Flow

1. `config.example.toml` 和 `Config.YoutubeSourceConfig` 提供默认值。
2. `load_config()` 读取 `[sources.youtube]` 新字段，缺失时保持兼容默认。
3. `_render_config_toml()` 写出新字段，确保 settings page 保存后不会丢配置。
4. `GET /api/config` 通过 `YoutubeSourceConfigOut` 暴露新字段。
5. `PUT /api/config` 接收新字段并写回 `cfg.sources.youtube`。
6. `RuntimeContext.rebuild_from_config()` 构造 YouTube 三个 discovery strategy 时读取配置并传给对应 constructor。
7. `popup.html` 在“平台源” tab 中展示 B 站、通用网页、小红书、抖音、YouTube 和候选池占比；`popup.js` populate/collect YouTube 新字段。

## Error Handling

- 新数字字段按现有配置风格使用 `int(...)` 读取和保存；API 写入也复用当前 `PUT /api/config` 的异常路径，让结构化 config validation 和 degraded UI 处理错误。
- UI 不在前端重复做复杂校验，只设置 `type="number" min="0" step="1"` 或合理最小值，实际保存仍以后端为准。
- settings tab 切换不卸载 DOM，避免隐藏 tab 的值丢失。
- 若旧配置没有 YouTube 新字段，默认值保持可用，不需要用户手动迁移。

## Testing

- `tests/test_config.py`: YouTube 默认值、从 TOML 读取、保存 round-trip。
- `tests/test_api_app.py`: `GET /api/config` 暴露字段，`PUT /api/config` 更新字段。
- `tests/test_refresh_runtime.py` 或 runtime context 测试：构造 YouTube strategy 时把 config 值传给 search/trending/channel strategy。
- `extension/tests/popup-settings.test.ts`: 设置页有 settings tabs、每个 section 标注 tab、YouTube 新字段存在并被 `popup.js` wire。
- `extension/tests/popup-layout.test.ts`: settings tab CSS 使用稳定布局，隐藏 inactive panel。

## Documentation

- `docs/modules/config.md`: 更新 `[sources.youtube]` 表格和语义说明。
- `docs/modules/extension.md`: 更新设置页源策略控制说明，提到 tabbed settings 和 YouTube 三策略配置。
- `docs/changelog.md`: 当前版本块追加一条插件设置页 / YouTube 配置对齐变更。
- `config.example.toml`: 写出新 YouTube 字段与注释。
