# LLM 价格目录与峰谷调度设计（issue #353 后续）

> 状态：设计稿 · 2026-11-21
> 关联：issue #353（每日花费上限）、PR #191（高峰错峰，未合入）、`feat/daily-llm-budget-cny`
> 本文回答两个问题：**每个 provider / 每个模型的价格从哪里来**，以及**峰谷价怎么和每日预算一起用**。

## 1. 结论

1. 价格不需要“猜”，也不该继续手写一张会过期的静态表。现成有三类可组合的数据源：
   - **models.dev**：324 个 provider、10,018 个模型、9,569 个带 `cost`（USD / 百万 token，含 cache read/write）。覆盖面最广，适合做“模型级价格分布”。
   - **OpenRouter `/api/v1/models`**：476 个模型，其中 **88 个带 `pricing.overrides`**，包含 **UTC 峰谷窗口** 和长上下文档位。这是目前唯一实测可用的“机器可读峰谷价”来源。
   - **用户覆盖 + 余额校准**：中转站/私有报价只能由用户填；DeepSeek `/user/balance`、OpenRouter `/credits`、OpenAI/Anthropic 管理端 cost API 用于事后校准。
2. 静态表的偏差已经定位：`deepseek-v4-flash` 旧表是 `¥1 / ¥2` 每百万 token，实测是 `$0.15 / $0.60` × 7.2 = `¥1.08 / ¥4.32`，**输出价低估 2.16 倍**。这正好解释 issue 里“自估 0.85 / 实际 1.91”的 ×2.2。本分支已先把 DeepSeek 现价修正。
3. 峰谷窗口不用手写。OpenRouter 的 `deepseek/deepseek-v4.1-flash-1017` 路由把工作日 UTC `01:00–04:00`、`06:00–10:00` 标成 2x（= 北京时间 09:00–12:00、14:00–18:00），其余为基准价；与 PR #191 的硬编码窗口完全一致。把 PR #191 的“是否高峰”判断换成 catalog 的 tariff predicate 即可，调度骨架不用重写。
4. 对未知价格必须“保守地不知道”：`0` / 缺失 / 中转站未标价 → 不能当免费。用同模型跨 provider 的**最高价**做预算上界，标 `fallback` 置信度；用户覆盖后才转 `exact`。

## 2. 问题拆解

价格不是一个数，而是一个带维度的函数：

```
cost = f(provider, base_url, model, 计费项, 时间, prompt_tokens, cache_tokens)
```

需要同时解决：

| 维度 | 例子 | 难点 |
|---|---|---|
| provider × model | `deepseek/deepseek-v4-flash`、`zai/glm-5.3-flash` | 模型 ID 在官方/中转/聚合之间不一致 |
| 计费项 | input / output / cache read / cache write / request fee | 不是所有 provider 都报 cache |
| 时间 | DeepSeek 峰谷 2x、Gemini/OpenAI 批处理档 | 窗口、时区、工作日、节假日 |
| 上下文档位 | >200K prompt 翻倍 | 同一次请求内按 token 数选档 |
| 币种 | USD 报价、CNY 账单 | 汇率与历史汇率 |
| 可信度 | 官方页 / 社区目录 / 中转站 / 用户填 / 余额反推 | 预算必须知道“这个数有多可信” |

预算上限需要 **上界**（最坏情况），报表需要 **期望值**，调度需要 **时间函数**。三者不能共用一个静态数。

## 3. 数据源（2026-11-21 实测）

### 3.1 models.dev

- 端点：`https://models.dev/api.json`（公开、无需鉴权，约 6.8 MB）
- 结构：`{provider: {models: {model_id: {cost: {input, output, cache_read, cache_write}, limit, ...}}}}`
- 实测：324 providers / 10,018 models / 9,569 个有 `cost`；单位是 **USD / 百万 token**。
- 覆盖仓库里所有关心的模型 ID：`deepseek-v4-flash`（27 个 provider 报价）、`glm-5.2`、`glm-5.3-flash`、`claude-opus-4-7`、`gpt-6.2-astra` 都能查到。
- 没有峰谷窗口，只有静态价；`0` 可能是真免费，也可能是缺数据（见 4.2）。

### 3.2 OpenRouter models API

- 端点：`https://openrouter.ai/api/v1/models`（公开，约 925 KB）
- `pricing` 字段是 **USD / token**：`prompt`、`completion`、`input_cache_read`、`input_cache_write`。
- `pricing.overrides` 有两种：
  1. **时间档**：`utc_days` + `utc_start` + `utc_end`。单位是 **百分之一小时**（`100` = 1 小时），`utc_end = 0` 表示跨午夜收尾。
  2. **长上下文档**：`min_prompt_tokens`，超过阈值后整套单价替换。
- 实测 476 个模型，88 个带 overrides（**4 个时间档、54 个长上下文档**，其余是重复/无效条目）。DeepSeek 路由的窗口：

```
base: prompt $0.15/M, completion $0.60/M, cache_read $0.003/M
周一至周五 UTC 01:00–04:00  → 2x
周一至周五 UTC 06:00–10:00  → 2x
其余时段（含周末）           → base
```

### 3.3 OpenRouter endpoints API（中转站价格上界）

- 端点：`https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints`
- 返回每个上游转售商的报价。以 DeepSeek V4.1 Flash 为例，同一模型的 input 报价从 `$0.03/M` 到 `$0.15/M` 不等。
- 用途：给“未知中转站”估一个保守区间；**不能**直接当成用户的实际价格。

### 3.4 余额 / 账单 API（校准，不是主数据源）

| Provider | 端点 | 能拿到 | 限制 |
|---|---|---|---|
| DeepSeek | `GET /user/balance` | 余额、币种 | 只能算差值；同 key 其它工具会污染 |
| OpenRouter | `GET /api/v1/credits` | 总充值 / 总消耗 | 只能算差值 |
| OpenAI | `GET /v1/organization/costs` | 组织级每日花费 | 需要 admin key |
| Anthropic | `GET /v1/organizations/cost_report` | 组织级花费 | 需要 admin key |
| 其他 | — | 多数没有公开计费 API | 只能靠用户填 |

### 3.5 用户覆盖

中转站、企业协议价、地区价只能由用户提供。配置优先级最高，任何自动同步都不能覆盖它。

### 3.6 官方定价页

OpenAI / Anthropic / Google / DeepSeek 的定价页是人工可读的，没有稳定的机器接口。作为 **随包快照的校对源**：每次发版由维护者核对一次，写 `verified_at` 和 `source_url`。

## 4. 关键实测证据

### 4.1 DeepSeek 的 2.16 倍偏差

| 项 | 旧表 | 实测（models.dev + OpenRouter） | 比值 |
|---|---|---|---|
| input | ¥1.00 / M | $0.15 × 7.2 = ¥1.08 / M | 1.08x |
| output | ¥2.00 / M | $0.60 × 7.2 = ¥4.32 / M | **2.16x** |

LLM 输出 token 通常占账单大头，所以整体偏差接近 issue 里的 ×2.1。本分支已把 `pricing.py` 的 `deepseek-v4-flash` / `v4-pro` 改成实测价。

### 4.2 `0` 不等于免费

models.dev 里 `sensenova` 的 `deepseek-v4-flash` 报价是 `{input: 0, output: 0}`。用户的本机配置恰好走 `token.sensenova.cn`。如果代码把 `0` 当免费，每日上限永远不会触发。

规则：只有显式本地/免费 provider（`ollama`、`sponsored`、`siliconflow_free`）才允许 `0`；其它 provider 的 `0`、缺失、负值一律视为 **unknown**，走保守回退并打 WARN。

### 4.3 同模型跨 provider 的价格分布

`deepseek-v4-flash` 在 models.dev 有 27 个报价，output 从 `0` 到 `$1.32/M`，中位数 `$0.28/M`。

这给了一个可用的保守策略：

- 官方直连 / 精确 provider 匹配 → 用精确价，`confidence=exact`；
- 未知中转站 → 用同模型报价的 **最大值**（或 p90），`confidence=derived_relay`，并在 `cost` 里显示区间；
- 用户覆盖后 → `confidence=user`，不再回退。

## 5. 数据模型

建议新增 `src/openbiliclaw/llm/rate_card.py`（纯函数，无网络）：

```python
@dataclass(frozen=True)
class RateCard:
    provider: str                 # deepseek / openai / zai / relay:token.sensenova.cn
    model: str                    # 规范化后的 model id
    currency: str                 # "USD" | "CNY"
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None
    cache_write_per_mtok: float | None
    tiers: tuple[RateTier, ...]   # 长上下文档
    tariffs: tuple[TariffWindow, ...]  # 峰谷档
    source: str                   # "models.dev" / "openrouter" / "user" / "balance"
    source_url: str
    confidence: str               # user | exact | derived_relay | fallback | unknown
    verified_at: datetime
    effective_from: datetime | None

@dataclass(frozen=True)
class TariffWindow:
    days: frozenset[int]          # 0=Mon .. 6=Sun；空 = 每天
    start_minute_utc: int         # 0..1439
    end_minute_utc: int           # 0..1440；0 且 start>0 表示跨午夜
    input_per_mtok: float         # 绝对值，不用 multiplier，避免叠加歧义
    output_per_mtok: float
    cache_read_per_mtok: float | None
    source: str
    label: str                    # "peak" / "off_peak" / "batch"

@dataclass(frozen=True)
class EffectiveRate:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None
    currency: str
    tariff_label: str
    confidence: str
    next_change_at: datetime | None   # 下一次档位切换，供调度用
```

`RateCatalog` 负责：

1. 按 `provider + model` 精确查；
2. 按 `provider` 别名归一（`claude→anthropic`、`zai→zhipuai` 等）；
3. 按模型 ID 归一（去日期后缀、大小写、`-latest`、`deepseek-v4.1-flash-1017 → deepseek-v4-flash`）；
4. 查不到时用“同模型跨 provider 分布”的最大值；
5. 再查不到时用 provider 默认价（`fallback`）；
6. 仍查不到 → `unknown`，**不返回 0**。

### 5.1 币种

- catalog 内部统一存 **USD / 百万 token**；`RateCard.currency` 允许 `CNY` 覆盖。
- 汇率单独存：`{"usd_cny": 7.2, "fetched_at": "..."}`。
- 写 `llm_usage` 时按当时汇率换算；历史行不重算。
- 汇率过期时用 **对预算更不利** 的方向（人民币贬值方向）。

### 5.2 峰谷窗口

- 一律存 **UTC 分钟** + 星期集合，渲染时转本地时区。
- `start > end` 或 `end == 0` 表示跨午夜。
- 没有窗口 = 全天同价，不是“便宜”。
- 节假日：仓库没有权威节假日日历。DeepSeek 的公开规则目前只按工作日；**未知时按高峰处理**（多花钱不如多等），并在设置页说明。

### 5.3 解析顺序

| 顺序 | 来源 | confidence | 用途 |
|---|---|---|---|
| 1 | 用户配置覆盖 | `user` | 最终价 |
| 2 | provider + model 精确匹配 | `exact` | 最终价 |
| 3 | 同模型跨 provider 最大值 | `derived_relay` | 预算上界 |
| 4 | provider 默认价 | `fallback` | 预算上界，UI 标黄 |
| 5 | 完全未知 | `unknown` | 用 `fallback × 2`，UI 标红 |

## 6. 预算闸怎么用

### 6.1 报表用期望值，准入用最坏值

- `llm_usage.estimated_cost_cny`：按**响应时刻**的有效档位计算（含 cache 折扣、长上下文档），这是报表的“期望值”。
- 预算准入：`spent + reserved + worst_case(next_call) <= daily_budget_cny`。
  - `worst_case` = 当前档位 / 下一档位的较高价 × prompt 上界；没有 prompt 上界时用最近同 caller 的 p95。
  - 并发请求必须 **先预留**，完成后释放并按实际值结算，否则 N 个并发会同时看到同一份余额（当前每日上限的已知边界）。
  - 没有预留时，跨线那一次仍会发出；预留能把超调从“N 次调用”压到“一次结算误差”。

### 6.2 置信度安全系数

| confidence | 预算计价 | 显示 |
|---|---|---|
| `user` / `exact` | ×1.0 | 正常 |
| `derived_relay` | ×1.25 | “中转估算” |
| `fallback` | ×2.0 | 黄色警告 |
| `unknown` | ×2.0 + 顶部提示 | 红色警告 |

安全系数只在**预算判断**里用；`cost` 报表仍显示期望值，并单独标 `cost_confidence`，避免为了安全把报表永久吹大。

### 6.3 与每日上限的关系

- 每日上限是硬约束；峰谷只决定 **什么时候跑**。
- 高峰时段如果预计会把当日预算烧穿，而下一个低价窗口在可接受等待内 → 推迟。
- 低价窗口很长（例如整个夜间）也要保留补货地板，不能为了省钱把推荐流饿死（PR #191 的 `peak_refill_floor`）。

## 7. 峰谷调度

### 7.1 策略

```toml
[scheduler]
# off    = 不看峰谷，保持现状
# defer  = 只在低价窗口跑后台（PR #191 语义）
# prefer = 尽量低价窗口，预算/供给紧张时允许高峰跑
peak_policy = "off"
peak_refill_floor = 30
```

保留 PR #191 的 `pause_during_peak_hours` 作为兼容别名：

- `pause_during_peak_hours = true` → `peak_policy = "defer"`；
- `peak_hours` 仍可手填，覆盖 catalog 窗口（用于 provider 没给窗口或用户知道特殊规则）；
- 两者都空 → 用当前主实例的 `TariffWindow`；
- 没有窗口 → 等同 `off`，不假装有低价时段。

### 7.2 判断放在哪

复用 PR #191 的 `background_llm_work_allowed()`，只替换其中的“是否高峰”：

```python
# 旧：_now_in_peak_windows(now_utc, parse_peak_windows(scheduler.peak_hours))
# 新：catalog.effective_rate(provider, model, now_utc).tariff_label == "peak"
```

- 交互流量永不错峰。
- refill：池子低于 `peak_refill_floor` 时放行（现有逻辑）。
- maintenance：高峰完全暂停。
- 主实例不在链里 / 链里混了不同 tariff：**取链上最贵的当前档位**做保守判断；后续再做“按实例选择窗口”的 cost-aware routing。

### 7.3 PR #191 的合并方式

PR #191（`feat/peak-hour-deferral`，commit `54f0ec03`）已经在 `presence.py` / `refresh.py` / config / API / docs 上做了完整实现，测试也齐全。建议：

1. 先按原样合入（保留作者署名），作为 `peak_policy=defer` 的 v1；
2. 再单独一个 PR 把 `parse_peak_windows` 的硬编码默认值替换为 catalog 解析；
3. 配置键保留兼容：`pause_during_peak_hours`、`peak_hours`、`peak_refill_floor` 不删；
4. 测试里 `now_utc` 注入点全部保留，新增“catalog 窗口 + 跨午夜 + 周末”的用例。

## 8. 刷新、陈旧与离线

| 场景 | 行为 |
|---|---|
| 随包快照 | 发版时从 models.dev + OpenRouter 生成，带 `catalog_version` / `fetched_at` / `source_url` |
| 首次启动无网络 | 用随包快照，`confidence=exact`（对快照而言） |
| 在线同步 | `openbiliclaw price sync` 手动触发；原子写 `data/pricing/catalog.json`；保存前展示 diff |
| 自动同步 | 默认关闭；开启后每天一次，失败静默回退 last-good |
| 快照 > 30 天 | 启动 WARNING；UI 显示“价格可能过期” |
| 快照 > 90 天 | 预算安全系数额外 ×1.25；阻止 `peak_policy=defer` 自动启用 |
| 用户覆盖 | 永远优先；同步不覆盖、不删除 |
| 离线 | 不阻塞任何运行；只用 last-good + 置信度提示 |

不要把 6.8 MB 的 models.dev 原样塞进仓库。生成脚本只保留：

- 仓库支持的 provider + 用户配置里出现的模型；
- `input/output/cache_read/cache_write`、`tiers`、`tariffs`；
- 同模型跨 provider 的 min/median/max（用于保守回退）。

估计 100–300 个模型，JSON < 200 KB。

## 9. 失败模式

| 失败 | 后果 | 处理 |
|---|---|---|
| `0` / `-1` 被当成价格 | 预算永不触发 | 只允许显式免费 provider；其余视为 unknown |
| 单位混用（per token vs per M） | 100 万倍误差 | catalog 统一 per-M；解析时校验数量级 |
| USD/CNY 混用 | 7 倍误差 | 每个 card 带 `currency`；报表显示币种 |
| 中转站模型 ID 漂移 | 价格错配 | 规范化 + 用户覆盖 + 显示 `source` |
| cache_read 缺失 | 高估 | 预算用“无缓存折扣”的最坏值；报表用实际 |
| 长上下文 tier 边界 | 单次费用跳变 | 按 prompt tokens 选 tier；预留用上一档 |
| 跨午夜窗口 | 峰谷判断反了 | `start > end` 显式按跨日处理；测试 UTC 23:59/00:00 |
| 节假日 | 高峰判断错 | 无日历按高峰；用户可手填窗口 |
| 快照过期 | 预算偏差 | 30/90 天阈值 + 安全系数 + UI 警告 |
| 并发预留竞争 | 超调 | 预留表 + 事务；MVP 先用安全系数 |
| OpenRouter 手续费 | 低估 5.5% | OpenRouter 路由单独加 `fee_rate` |
| 中转站加价 | 低估 | 用同模型最大值；用户覆盖 |

## 10. 分阶段

| 阶段 | 内容 | 风险 |
|---|---|---|
| P0（本分支已做） | 修正 DeepSeek 现价；`llm.cost_multiplier` 保留为手动校准 | 低 |
| P1（已完成） | `rate_card.py` + 生成脚本 + 随包 catalog + `openbiliclaw price`；运行时计费未切换 | 低 |
| P2（已完成） | `UsageRecorder` 改用 `EffectiveRate`；`llm_usage` 增加 `cost_confidence`；预算安全系数改为“未知中转用最高价” | 中 |
| P3（已完成） | 合入 PR #191 的调度骨架；`peak_policy` + catalog 窗口；保留 floor | 中 |
| P4（已完成） | `price --sync` 原子刷新 data 目录 catalog；`cost` 显示低置信花费；设置页策略/水位/手动窗口 | 中 |
| P5（已完成） | `daily_budget_reserve_cny` per-call 预留；`llm.cost_aware_routing` 同模型按当前价格重排 | 高 |

P1/P2 做完，issue #353 的 ×2 偏差就从“手动 multiplier”变成“目录自动校准”；P3 做完才有真正的“只在谷时跑”。

## 11. 待决策

1. 未知中转站默认用 **最大价** 还是 **p75**？最大价安全但可能过度保守。
2. `peak_policy` 默认 `off` 还是 `prefer`？建议 `off`（不改变现有费用曲线），在设置页显式开启。
3. 汇率来源：`open.er-api.com` / 欧洲央行 / 手动配置？建议手动 + 可选同步，避免引入新的网络依赖。
4. 随包 catalog 的许可证与署名：models.dev 数据需要确认 license；OpenRouter 价格是事实数据，仍建议在文档里注明来源与抓取日期。
5. 是否接受“价格过期时预算偏保守”而不是“停止运行”？建议接受，并显示黄色提示。

## 12. 参考

- models.dev API：`https://models.dev/api.json`（2026-11-21 抓取：324 providers / 10,018 models / 9,569 带价）
- OpenRouter models：`https://openrouter.ai/api/v1/models`（476 models / 88 with overrides，其中 4 个时间档 / 54 个档位）
- OpenRouter endpoints：`https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints`
- DeepSeek 余额：`GET https://api.deepseek.com/user/balance`
- 相关实现：`src/openbiliclaw/llm/pricing.py`、`src/openbiliclaw/llm/usage_recorder.py`、`src/openbiliclaw/llm/concurrency.py`、`src/openbiliclaw/runtime/presence.py`
- 相关 PR：#191（高峰错峰，未合入）、#344（请求级次数预算）、#353（每日金额上限）
