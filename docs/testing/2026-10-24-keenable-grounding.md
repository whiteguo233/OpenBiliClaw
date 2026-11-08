# Keenable grounding 验收（2026-10-24）

> **2026-10-24 后续**：Keenable grounding 已收敛为**纯匿名免费模式** —— `[discovery].keenable_api_key` 与 `/v1/search`（`X-API-Key`）分支已移除，`KeenableInspirationProvider` 只请求 `/v1/search/public`。本文中 keyed 端点与 key 回退相关的验证仅作历史记录。


范围：可选 `keenable` query inspiration grounding 后端（`[discovery].inspiration_search_backends` 显式加入 `keenable` 才启用）。默认链不变，插件 / 桌面 Web / 移动 Web / CLI 均复用同一后端配置，不新增设置控件。

## 接口契约（真实匿名端点）

对 `https://api.keenable.ai/v1/search/public` 的真实请求，验证 PR 声明的契约与实现假设一致：

| 场景 | 结果 |
|---|---|
| `POST /v1/search/public`，带 `X-Keenable-Title: OpenBiliClaw`，`{"query":"python asyncio tutorial","max_results":3}` | 200，`results[]` 含 `title / url / description / snippet`，1.1 s |
| 同上，去掉 `X-Keenable-Title` | 400 `{"error":"Missing app identifier","message":"X-Keenable-Title header is required for token-less requests"}` |
| `POST /v1/search`（keyed），无 `X-API-Key` | 401 `{"error":"Missing authentication",...}` |
| `POST /v1/search/public`，`site:v2ex.com/t 机械键盘`，`max_results=5` | 200，5 条，全部为 `v2ex.com/t/<id>` 或 `www.v2ex.com/t/<id>` 链接，3.9 s |

结论：`parse_keenable_search_payload()` 的字段假设（`snippet` 优先、`description` 回退）与匿名端点的真实响应一致；`X-Keenable-Title` 确实是匿名调用的必需头；V2EX 正式 search 的 `site:v2ex.com/t` 召回可用，且两种 host 都能被 `_row_topic_id()` 归一为 Topic ID。

## 自动化验证

```bash
pytest -q tests/test_keenable_inspiration_provider.py tests/test_v2ex_producer.py \
  tests/test_parallel_inspiration_provider.py tests/test_discovery_inspiration_provider.py \
  tests/test_config.py tests/test_docs_index.py
ruff check src/ tests/
mypy src/
```

结果：470 passed；`ruff check` 通过；`mypy` 362 文件无问题。单测覆盖匿名 / keyed 端点选择、`X-Keenable-Title` 与 `X-API-Key` 头、`max_results` 上限、空 query 不发请求、摘要 400 字符截断、`snippet`→`description` 回退、429/400/503/HTML 四类失败进入既有 fallback、`keenable_api_key` 配置存取往返、默认链不含 `keenable`、V2EX 白名单透传。

## 未覆盖

- keyed 端点的真实调用（本地没有 Keenable API Key），只按官方文档与 401 行为验证了鉴权要求。
- 匿名额度「每 IP 每秒 10 次、每小时 1000 次」未压测，数字取自 Keenable 官方 Rate limits 文档。
- 限时余量：中文查询实测 2.3–3.9 s，与工厂注入的 6 s 请求超时、上层 8 s 绝对期限之间余量偏小；国内网络抖动时可能触发超时→60 s cooldown→下一后端。
