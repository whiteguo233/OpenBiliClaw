# Jev 上下文利用率与密集合批验收（2026-10-27）

## 结论与范围

用户要求尽量利用所选 Jev 的上下文做多候选、多 question 并行评估。
本轮在 `perf/jev-context-utilization` 独立 worktree，基于 `87d17d5c` 实现并验证：

- 自动字节预算取消固定 25% 的额外预留，标准 64k/32k token 容量对应 64,000/32,000
  UTF-8 字节上限；**不是已获得精确 tokenizer，也不是保证 32K token 满载**。
- 默认候选搜索上限 4→64，实际批量受双预算、问题字节/题数、总调用数和 deadline 约束。
  保留已有显式小候选上限、48,000/24,000 等旧字节限制，不静默放宽用户设置。
- first-fit 保留已开批次空位，后续小候选可回填；max_calls 用尽仍可填已有批次，
  不增加 HTTP 次数、不把同一候选拆成多轮评估，不按同行缩短候选视图。
- 所有候选都已选中且完全相同的整画像字段只发送一次；共享字段叠加目标候选字段精确还原
  它原来的选择。缺失/不同字段不共享，不借用同行兴趣、不摘要/修改源画像。
- 完整评估输入合同为 `jev-full-evaluation-v4`，预算为 `jev-wire-bytes-ceiling-v2`，
  打包为 `first-fit-open-batches-v1`；新命名空间不会重用旧缓存。

官方标准约束仍是 `state + Σ questions ≤ 64k` 与 `state + any question ≤ 32k`，
不是每条候选独享 32k；所有问题看到整份共享 state。
依据：[官方 Models](https://docs.typesafe.ai/models#context-length)（本轮重新读取）。
标准模型/文档索引的核实范围内未取得精确本地计数合同，所以不以第三方 tokenizer 冒充 Jev。
未启用实验性 long-context header、未自动换模型，也未新增摘要调用或付费溢出探测。

## 红绿回归与接口合同

测试走现有公共 profile view、完整 evaluator→typed provider、候选管线与配置接口：

| 回归 | 修复前 | 修复后 |
|---|---|---|
| 够大的已登记窗口，8 个完整候选 | 被默认 4 条上限拆成 2 请求 | 1 请求、80 题，双预算内 |
| 中/大/小候选，最多两次调用 | 第三条因已关闭前批而遗漏 | 第三条填前批空位；2 请求共 30 题，各候选仅一次 |
| 多候选相同整画像字段 | 可选字段重复传输 | 完全相同且每条都有的字段共享；overlay 逐项等于原视图，输入不变 |
| 两条长文本，原 25% 余量提前拆批 | 2 请求 | 1 请求，state＋最长题超过旧 24KB、仍在新 32KB 内 |

单候选保持原共享布局，画像预算探针仍稳定。同行独立性回归改为比较**还原后的完整逻辑视图**，
而不是要求已去重的物理子字典仍重复共享字段；没有放宽事实保留要求。
导演的完整 envelope 压力测试继续只留 20/300 bytes 空位，按新映射调整 fixture，
并非把“少于 10 个兴趣”的断言删除。旧显式收紧限制、无效配置、取消、deadline、
不缓存失败、预算停放和上下文变化恢复均保留回归。

## 零网络对比

对完全相同的私密画像/内容快照，仅在 typed provider 接口捕获请求并返回明确 preview unavailable，
不创建 HTTP client，不把本地捕获当作模型评分成功。

- 原三条快照在旧自动 48KB/24KB 护栏下为 3 个请求。
- 仅共享相同整字段、仍保持 48KB/24KB 时，这三条仍为 3 个请求；没有把其它样本的收益移给它们。
- 全部改动加新 64KB/32KB 自动字节预算后，同三条为 2 个请求（20题＋10题）。
- 12 条**合成短文本克隆**（沿用同一画像，不是 12 条独立真实视频）从 12 个请求降到 3 个，
  每批 4 条/40 题，最大请求 63,162/64,000 bytes；保留旧护栏时为 6 个请求。

克隆只用于打包压力测试，未为这些重复样本发送付费请求，不认证推荐质量或费用下降。

## 正式进程＋真实 OpenRouter 请求

独立 Uvicorn 使用正式 `create_app()` / lifespan / RuntimeContext / config apply /
候选 coordinator / native adapter / SQLite admission，无假的业务组件或模型答案。
旁路 HTTP hooks 仅检查输入长度、事实来源、完整 mandatory、题组与原文 offsets，
不修改 endpoint、认证、请求内容或响应。独立配置以 B 站专用回环代理阻止范围外抓取，
因此这是既有视频快照评估，不是当天源站抓取验收。

- 模型固定 `typesafe/jev-1.14-20261014`；Base URL `https://openrouter.ai/api`，
  实际原生路径 `/api/v1/systemone`，返回 provider `TypeSafe`；不使用浮动 Router。
- 真实画像文件副本 58,850 bytes；有效本地目录 24,686 bytes / 114 个兴趣，
  共享必需画像 7,375 bytes。两条真实视频快照与一条明确标注的合成长文（454 个源片段）。
- 正式 `PUT /api/config` 保存固定模型及 64,000/32,000 容量，等待实际 applied revision=1，
  再入队三条候选，由正常后台路径评估。
- 单次传输尝试，最多 3 个外部请求的硬上限；任何非 200 后不再发后续外部请求。
  实际仅两次，均 200 且 native evidence 校验通过，三条均完成评估：1 cached、2 rejected_low_score。

| 请求 | 候选 / questions | 请求 bytes / 64,000 | state＋最长题 bytes / 32,000 | 字节护栏利用率（总 / 单题） | 实际 input / output tokens |
|---|---|---|---|---|---|
| 两条真实视频 | 2 / 20 | 49,418 | 31,376 | 77.2% / 98.1% | 19,152 / 1,495 |
| 合成长文 | 1 / 10 | 40,647 | 31,899 | 63.5% / 99.7% | 17,889 / 1,635 |

不能为了填满总请求 64KB，违反已接近上限的 32KB 单题护栏。实际 input usage 也不能
反推出每道题的 token 数；表中的利用率明确是**字节**比例，不是 token 比例。
生产日志新增同名 `request_byte_utilization_pct` / `state_question_byte_utilization_pct`，
与实际 input/output tokens 分列，不记录画像、标题或原文。

所选兴趣分别为 18 / 16 / 1，均来自源目录；必需避雷/风格/场景完整。
两视频原文 8/8、5/5 片段完整发送；长文选择 105/454 片段（旧正常窗口为 65/454），
“必须先备份；否则不适用”条件与每个源字段/字符位置均验证保留。不是任意长内容语义无损证明。

远端耗时 3.187 / 1.367 秒，usage.cost 合计 **$0.001555722**。
虽然请求从 3 降到 2，但本轮发送了更多画像/长文信息，费用略高于之前正常窗口的
$0.001469244；不以更少请求宣称一定省钱，也不把评分差异作为推荐效果提升证明。
评分门槛保持 0.60，无图像输入、无生成式 LLM fallback。

私密脚本、trace、report 与隔离数据在 `/tmp/obc-jev-packing-20261027.aYFhI8`，未提交。
Token 仅以终端关闭回显后的临时环境变量传给自有进程，不写配置/报告，源画像 hash 未变；
未修改源数据库、日常配置或日常服务。完整 JSON report 的 `passed=true`。

## 验证命令与交付边界

```bash
PYTHONPATH=src pytest -q tests/test_jev_full_evaluator.py tests/test_jev_context_budget.py tests/test_jev_on_demand_profile.py tests/test_on_demand_profile_views.py
PYTHONPATH=src pytest -q tests/test_jev_runtime_e2e.py::test_process_restart_reuses_saved_jev_model_endpoint_and_capacity
ruff format --check src/ tests/
ruff check src/ tests/ scripts/jev_fake_backend.py
mypy src/
python scripts/build_i18n_catalogs.py --check
```

首次扩大选择中与旧 3/4 预算绑定的字面值/fixture 先失败，已按新合同同步；
不是降低远端响应校验或取消 mandatory 保留。单独的正式进程保存/重启 HTTP 集成测试已通过，
该测试使用本机合成 Jev 服务，没有真实模型收费。

最终较大选择为 **1318 passed、1 failed、4 deselected**（577.66 秒）；唯一失败为既有
`test_pair_composer.py::test_outer_deadline_cancels_noncompliant_slow_provider`。
该用例只给整个构造＋调用 20ms，负载下可能在 provider 进入前耗尽 deadline；生产代码
明确允许未调用 provider 的 deadline fallback。首跑未保留中间 proposal，未确证本次具体分支，
不以负载解释冒充已修复一个确定根因；本轮也未放宽生产取消/超时规则。
同一用例独立复跑通过，随后完整导演/配对选择 **817 passed、4 deselected**（258.05 秒）
也包含其通过。记录首次失败，不把该大选择改写为一次全绿；没有修改该用例或生产计时规则。
first-fit 非连续候选的分数/索引映射补充断言独立通过。

```bash
PYTHONPATH=src pytest -q tests/test_jev*.py tests/test_decision*.py tests/test_on_demand_profile_views.py tests/test_profile_views.py tests/test_profile_views_guards.py tests/test_director_adaptive_context.py tests/test_director_repair_context_budget.py tests/test_pair_composer.py tests/test_director_proxy_budget.py tests/test_jev_evaluator_wiring.py tests/test_discovery_candidate_pipeline.py tests/test_discovery_candidate_store.py tests/test_candidate_eval_coordinator.py tests/test_config.py tests/test_cli.py -k 'not login_codex_import' -m 'not integration' --tb=short --disable-warnings
PYTHONPATH=src pytest -q tests/test_pair_composer.py::test_outer_deadline_cancels_noncompliant_slow_provider
PYTHONPATH=src pytest -q tests/test_director*.py tests/test_pair*.py -m 'not integration' --tb=short --disable-warnings
```

另有保存/重启 HTTP 集成 **1 passed**，Ruff lint/format（891 个 Python 文件）、MyPy
（363 个源文件）、i18n inventory（2920 条）通过。CLI `config-show` 在上述 CLI 选择内验证，
明确排除会探测本机真实 OAuth 的 `login_codex_import`；不是全仓 pytest 或真实模型语义质量认证。
本轮未宣称全仓 pytest、安装版 MV3、人工浏览器体验或导演模型质量已验收。
三端共享表单只同步默认数值，未新增页面测试按钮或其它界面功能；CLI 命令、依赖及安装流程不变。
