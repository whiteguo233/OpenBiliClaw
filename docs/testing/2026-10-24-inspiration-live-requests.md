# Inspiration grounding 真实请求验收（2026-10-24）

代码基线：`main` @ `5d2d83a7`（#327 Keenable grounding + #328 词表收敛之后），外加本分支的
`bing_rss` 重定向修复。目的：不用 mock，直连真实端点与真实 LLM，验证 keyword inspiration 链路
（Keenable / Bing RSS / V2EX 外部召回 / fallback / 冷却 / 报告归属）。

运行环境：macOS，`[network] mode=system`，无显式代理；本机已装 `mcporter`。LLM 走配置里的
`openai_compatible`（`token.sensenova.cn`）。除特别说明外，数据目录用 `/tmp` 下的副本，
避免写主工作区的 `data/openbiliclaw.db`。

## 1. Keenable 匿名端点（真实 HTTP）

```
POST https://api.keenable.ai/v1/search/public
X-Keenable-Title: OpenBiliClaw
{"query": "机械键盘 客制化 入门", "max_results": 5}
→ 200, 5 条 results[].title/url/snippet，3.68 s（首次），后续 0.9–3 s
```

`site:v2ex.com/t 机械键盘` 同样 200，5 条全部命中 `v2ex.com/t/<id>`（含 `cn.` / `www.` host）。

## 2. Keyed 端点

本机配置未提供真实 `keenable_api_key`（字段不存在），因此 keyed happy path 未验证。用刻意错误的
key 打真实端点：

```
POST https://api.keenable.ai/v1/search  (X-API-Key: invalid-e2e-key)
→ 401 Unauthorized，0.90 s，httpx.HTTPStatusError 原样上抛（未被吞掉）
```

与 PR #327 记录的「无 key → 401」一致；取得真实 key 后应补一次 keyed 成功请求。

## 3. V2EX 正式 search 的外部召回路径

`build_v2ex_external_search_provider(config)`（#328 改造后的共享白名单 + key 打包）在
`inspiration_search_backends = ["keenable"]` 下：

```
search("site:v2ex.com/t 机械键盘", limit=5)
→ 3.03 s，5 条，URL 全部是 v2ex.com/t/…
```

说明白名单过滤、key 传递与 provider 装配都对；`local_cache` / `platform_sources` 仍被排除。

## 4. CLI 端到端（真实 LLM + 真实 grounding）

隔离根目录：复制 `config.toml`，`general.data_dir` 指向临时目录，`openbiliclaw.db` 与 `memory/`
用 `sqlite3 .backup` / `cp -R` 拷副本；`inspiration_search_backends = ["keenable", "bing_rss"]`。

```
OPENBILICLAW_PROJECT_ROOT=/tmp/oc-e2e/root python -m openbiliclaw.cli \
  keyword-inspiration-dry-run --platform bilibili --limit 3 --interest-limit 4
```

结果（报告 `inserted=false`，未写关键词池）：

- `grounding_records` 68 条，`grounding_source` 全部为 `keenable`；
- `grounding_ledger.searches = 14`，`provider_successes = {"KeenableInspirationProvider": 14}`，
  `provider_failures` / `timeouts` 均为空；
- 生成关键词如「大模型Agent工具使用综述 解析」「Qwen 函数调用机制 解析」，`llm_call_failed=false`；
- 墙钟 424 s，慢在 LLM（日志里 `openai_compatible` 触发 60 s 限流冷却后走备用实例、
  以及 `finish_reason=length` 重试），不是 grounding。

单后端链（`["keenable"]`）跑同一命令时，修复前报告里的 `grounding_source` 全部是 `none`：
`last_search_provider` 只有 fallback 链才会写，单后端直接返回 provider 本身。本分支改为回退
`backend_alias`；同一隔离配置复跑（`--limit 1 --interest-limit 1`）后 4 条记录全部记为
`grounding_source = keenable`。`provider_successes` 仍为空：该计数器只在 fallback 链汇总，
单后端不经过它——这是计数口径，不影响来源归属。

## 5. 失败传播与冷却（真实失败）

链 `["keenable", "bing_rss"]` + 错误 key：

- Keenable 401 → 记 `provider_failures`，进入 60 s 冷却；
- Bing 302 → 记 `provider_failures`；两条都失败后抛最后一个原始错误（不是空结果）；
- 冷却未过期的第二次调用 0.00 s 返回 `[]`，不再发请求。

## 6. 发现的问题（本分支修复）

1. **`bing_rss` 在国内网络下 100% 失败**：`https://www.bing.com/search?…&format=rss` 会 302 到
   `https://cn.bing.com/search`，而客户端用 httpx 默认的 `follow_redirects=False`，`raise_for_status()`
   直接抛 `HTTPStatusError`。`bing_rss` 在默认链里，等于免 key 的兜底一直是死的（只是被 exa / you
   接住才没暴露）。修复：`follow_redirects=True`；真实请求复验 5 条 / 0.69 s。
2. **单后端链的 `grounding_source` 记为 `none`**：报告无法说明是哪家 grounding 服务，
   `provider_successes` 也为空。修复：`_grounding_source_label()` 在无 `last_search_provider`
   时回退 `backend_alias`。

## 未覆盖

- keyed Keenable 成功路径（无真实 key）。
- 匿名端点 10 次/秒、1000 次/小时的额度压力。
- 插件弹窗 / 桌面 Web / 移动 Web 三端 UI：本次只验证共享后端与 CLI 预览链路。
- 生产 daemon 重启后的完整 discovery 周期（本机 daemon 仍在运行旧进程，未重启）。
