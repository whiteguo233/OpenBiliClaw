# Preference Prompt Budget Splitting Design

## Goal

修复偏好分析在初始化或大批量学习时把超长事件批次直接发给 LLM 的问题。目标是让 `PreferenceAnalyzer` 在发送请求前按 prompt 体积继续拆 chunk，并在单条事件过长时做保守瘦身，避免再次出现本地模型报：

```text
HTTP 400: The number of tokens to keep from the initial prompt is greater than the context length
n_keep: 135132 >= n_ctx: 36096
```

## Problem Statement

当前 `PreferenceAnalyzer.analyze_events(..., event_chunk_size=200)` 已经支持按事件条数分片，但这个策略不能保证 prompt 不超出模型上下文：

1. `build_preference_analysis_prompt()` 会把完整 `events` JSON 放入 `<event_batch>`。
2. 某些事件的 `context`、`metadata.raw_context`、字幕、评论、描述或第三方平台原始 payload 可能很大。
3. 只按 200 条切分时，一个 chunk 仍可能生成 10 万级 token 的 prompt。
4. `_run_chunk_resilient()` 当前只会对坏 JSON / 模型拒答做递归拆分；`LLMProviderError` / `LLMServiceError` 会被包成带 `__cause__` 的 `PreferenceAnalysisError` 并直接 abort。

所以截图里的 context overflow 是 provider 层错误，现有递归分片逻辑不会介入。

## Root Cause

偏好分析的分片单位是“事件数量”，但 LLM 的硬限制是“prompt token 数”。二者没有稳定关系。一个带长正文或原始 payload 的单条事件，可能比几百条普通标题事件还长。

代码层面的根因集中在 `src/openbiliclaw/soul/preference_analyzer.py`：

- `_analyze_events_chunked()` 只根据 `event_chunk_size` 建初始 chunks。
- `_run_chunk_once()` 构建 prompt 后立即调用 `complete_structured_task()`，没有本地预算检查。
- `_run_chunk_resilient()` 遇到 provider/service error 会直接抛出，不会识别 context overflow 并拆分重试。

## Chosen Approach

采用推荐方案：不新增 tokenizer 依赖，先用保守的字符预算做本地保护，并增加 provider context-overflow 错误的兜底拆分。

行为合同：

1. 保留现有 `event_chunk_size` 作为第一层粗分片。
2. 即使调用方没有传 `event_chunk_size`，`analyze_events()` 也会先预估整批 prompt；整批超预算时转入同一套 chunked/resilient 路径。
3. 每次真正调用 LLM 前，先计算 `len(system_instruction) + len(user_input)`。
4. 默认 `PreferenceAnalyzer.max_prompt_chars = 24_000`。这个值比截图里的 36k context 更保守，给结构化输出预算和中英文 token 差异留余量。
5. 如果 chunk prompt 超过预算且 chunk 有多条事件，递归二分 chunk，不调用 LLM。
6. 如果单条事件 prompt 仍然超过预算，生成 compact event 后重试：
   - 保留行为判断必需字段。
   - 截断长文本字段。
   - 丢弃大体积原始 payload。
7. 如果 compact 后仍然超过预算，跳过这条事件并记录 warning，不让整轮初始化失败。
8. 如果 provider 抛出明确的 context overflow 错误，按同样逻辑拆分或 compact 后重试。
9. 非 context overflow 的 provider/service 错误仍然 abort，避免把网络故障、认证失败、限流或模型不可用伪装成成功。

## Compact Event Contract

单条事件过大时，compact 版本只保留偏好提取需要的信号：

- 顶层字段：
  - `event_type`
  - `type`
  - `title`
  - `context`
  - `url`
  - `created_at`
  - `inferred_satisfaction`
  - `satisfaction_reason`
- `metadata` 白名单字段：
  - `source_platform`
  - `up_name`
  - `author`
  - `bvid`
  - `aid`
  - `content_id`
  - `folder`
  - `duration`
  - `watch_seconds`
  - `video_duration_seconds`
  - `feedback_type`
  - `reaction`

文本截断建议：

- `title`: 180 字符。
- `context`: 600 字符。
- `url`: 300 字符。
- metadata 字符串值：300 字符。

明确丢弃：

- `raw_context`
- `comments`
- `comment_list`
- `transcript`
- `subtitle`
- `description`
- `raw`
- `payload`
- 任意大型嵌套 dict/list，除非字段在白名单内且体积很小。

这样做会牺牲少量细节，但保留偏好学习最关键的“用户做了什么、对什么内容、来自哪个平台、作者是谁、是否是负反馈”。

## Data Flow

1. `SoulEngine.analyze_events(events, event_chunk_size=200)` 传入一批行为事件。
2. `PreferenceAnalyzer.analyze_events()` 先执行 satisfaction filter。
3. `analyze_events()` 构造一次预览 prompt：如果整批 prompt 不超预算且没有触发按条数分片，则走现有 single path。
4. 如果调用方传了 `event_chunk_size` 且事件数超过它，或整批预览 prompt 已经超预算，则进入 `_analyze_events_chunked()`。
5. 初始分片优先按显式 `event_chunk_size` 建立；没有传 `event_chunk_size` 但整批超预算时，按 `len(events) * max_prompt_chars // prompt_chars` 估算一个初始 chunk size，减少无谓的第一层整批递归。
6. `_run_chunk_resilient(chunk)` 在调用 LLM 前先执行 prompt budget check。
7. 超预算时：
   - 多条事件：二分为 left/right 并递归处理。
   - 单条事件：构造 compact event 并重试。
   - compact 仍超预算：跳过该事件。
8. prompt 合格后调用 `complete_structured_task()`。
9. LLM 返回 JSON 后走现有 `_parse_response()`、`_normalize_preference()` 和 `merge_preferences()`。
10. `source_platform_mix` 仍基于原始完整 events 计算，不受 compact/skipped prompt 表示影响。

## Error Handling

- 本地 prompt 预算超限：不调用 provider，直接拆分或 compact。
- Provider context overflow：识别错误文案后拆分或 compact 重试。
- 坏 JSON / 模型拒答：沿用现有递归拆分策略，最后只跳过仍失败的单条事件。
- 网络错误、认证错误、限流、模型不存在、服务不可用：继续 abort。
- 单条 compact 后仍超预算：记录 warning 并跳过，避免一个异常事件拖垮整批初始化。

Context overflow 匹配应保持窄口径，例如错误文本包含以下任意组合：

- `context length`
- `maximum context`
- `n_ctx`
- `n_keep`
- `tokens to keep`
- `prompt is too long`
- `input is too long`

## Compatibility

这个改动不新增配置字段、不新增依赖、不改变 CLI 参数，也不改变 LLM provider 接口。`PreferenceAnalyzer` 增加 dataclass 字段后，现有调用方仍可不传值。`SoulEngine.process_feedback_batch_if_needed()` 会显式传 `event_chunk_size=200`，作为 feedback 批处理入口的直接防护；预算预检仍是兜底。

行为上唯一变化是：超长初始化会拆成更多 LLM 请求，运行时间和请求次数可能增加，但不会再因为一个巨大 prompt 直接失败。

## Out of Scope

- 引入 `tiktoken`、SentencePiece 或 provider-specific token counter。
- 自动读取每个 provider 的真实 `n_ctx`。
- 新增用户可配置的 `config.toml` 字段。
- 改写 `build_preference_analysis_prompt()` 的完整 schema。
- 在其他 analyzer 上统一 prompt budget。这个 patch 只处理截图中的 `PreferenceAnalyzer`。

## Testing

新增 `tests/test_preference_analyzer.py` 回归测试：

- 超过 `max_prompt_chars` 的多事件 chunk 会在本地继续拆分，且每次 fake LLM 调用都不超过预算。
- 单条超长事件会 compact 后再发送，保留 `title/context/source_platform/up_name/bvid/feedback_type` 等关键字段，丢弃 `raw_context` 等大字段。
- compact 后仍超预算的单条事件会被跳过，不影响同批其他事件。
- provider 抛出 context overflow 错误时会触发拆分重试。
- 非 context overflow 的 `LLMProviderError` 仍然抛出 `PreferenceAnalysisError`。
- 现有坏 JSON 递归拆分测试继续通过。

建议验证命令：

```bash
.venv/bin/pytest tests/test_preference_analyzer.py -q
.venv/bin/ruff check src/openbiliclaw/soul/preference_analyzer.py tests/test_preference_analyzer.py
.venv/bin/mypy src/openbiliclaw/soul/preference_analyzer.py
```

若本地虚拟环境不可用，用项目当前约定的 `uv run --extra dev python -m pytest ...` 替代。

## Documentation

实现时需要同步更新：

- `docs/modules/soul.md`: 在 PreferenceAnalyzer 小节补充 prompt 预算拆分、单条事件 compact、context overflow 兜底。
- `docs/changelog.md`: 当前版本块增加一条 `fix(soul)` 短描述。

不需要更新架构图、CLI 文档或配置文档，因为没有新增模块边界、命令、配置字段或跨模块数据流。
