# inspiration grounding fallback 修复验收（2026-10-24）

> **2026-10-24 后续**：Keenable grounding 已收敛为**纯匿名免费模式** —— `[discovery].keenable_api_key` 与 `/v1/search`（`X-API-Key`）分支已移除，`KeenableInspirationProvider` 只请求 `/v1/search/public`。本文中 keyed 端点与 key 回退相关的验证仅作历史记录。


范围：`src/openbiliclaw/discovery/inspiration_provider.py` 的三个修复——`bing_rss` 302 区域跳转、Keenable key 被拒回退匿名端点、单后端配置也装配 fallback。

## 真实请求验证

环境：隔离 project root（复制真实 `config.toml` + `config.toml` 的 `[discovery]` 后端列表），工作树代码，真实公网请求（2026-10-24 22:5x，北京时间）。

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `build_inspiration_search_provider(["bing_rss"])` → `search("机械键盘 推荐")` | `HTTPStatusError: 302 Found` for `www.bing.com/search?...`（Bing 把 302 指向 `cn.bing.com`，httpx 默认不跟随） | 3 条结果 / 0.46 s，`last_search_provider=bing_rss`（知乎、ZOL 等真实 URL） |
| `build_inspiration_search_provider(["keenable"])` → `search(...)` | 返回裸 `KeenableInspirationProvider`，`last_search_provider` 不存在 → pipeline 记 `grounding_source="none"` | 返回 `FallbackInspirationSearchProvider`，3 条结果 / 3.57 s，`last_search_provider=keenable` |
| keenable + `keenable_api_key="invalid-e2e-key"` | keyed 端点 401 → 后端整体失败 → 只能走 fallback 链 | 日志 `keenable rejected the configured API key (HTTP 401); retrying the anonymous endpoint`，3 条结果 / 2.32 s |

补充：单后端失败时第二次 probe 落在 60 s cooldown 内，不再重复打端点（单测 `test_single_backend_failure_cools_down_before_retrying`）。

## 自动化验证

```bash
pytest -q tests/test_keenable_inspiration_provider.py tests/test_parallel_inspiration_provider.py \
  tests/test_discovery_inspiration_provider.py tests/test_v2ex_producer.py tests/test_config.py \
  tests/test_agent_web_search_tools.py tests/test_docs_index.py   # 513 passed
pytest -q tests/test_cli.py -k "inspiration or discover"           # 47 passed
ruff check src/ tests/                                             # 通过
mypy src/                                                          # 362 files, no issues
```

新增单测：`test_bing_rss_follows_regional_redirect`、`test_rejected_api_key_degrades_to_anonymous_endpoint`、`test_keyed_server_error_is_not_retried_anonymously`、`test_single_backend_failure_cools_down_before_retrying`。

## 未覆盖

- Keenable 有效 key 的成功路径（无可用 key）；只验了 401 → 匿名回退。
- `bing_rss` 在非 CN 网络（无 302）的行为未实机验证，逻辑上跟随跳转不影响直连路径。

## 端到端复验：单后端 `keenable` 的关键词灵感链路

隔离 project root（复制真实 `config.toml` + soul 画像 DB），`inspiration_search_backends = ["keenable"]`，真实 LLM + 真实 Keenable 请求：

| | 修复前（main） | 修复后（本分支） |
|---|---|---|
| `keyword-inspiration-preview --platform v2ex --limit 2` 的 `grounding_source` | 70 处 `"none"` | 69 处 `"keenable"`，0 处 `"none"` |
| `grounding_ledger.provider_successes` | `{}`（裸 provider 不产 ledger） | `{"KeenableInspirationProvider": 14}` |
| 产出关键词样例 | `agent-browser OpenAI 配置 踩坑`、`Reflexion 实测` | 同左（关键词质量不变，只是归因可读） |

同一次运行里 `searches=14`、`timeouts=0`，与修复前的搜索次数一致，说明单后端包装没有改变探测量。
