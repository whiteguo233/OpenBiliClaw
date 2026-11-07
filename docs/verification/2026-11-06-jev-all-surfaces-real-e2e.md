# Jev 三端真实请求复验（2026-11-06）

## 结论：工程链路通过，评估质量尚未通过

桌面 Web、实际加载的 MV3 插件、移动 Web 的配置、草稿探测、保存、热重载、固定模型、
容量设置、Token 脱敏和持久化均已用真实 OpenRouter 请求验证。小窗口零请求停放、
相同配置不重试、容量变更恢复、重启不重复请求也通过。

**不能据此宣称 Jev 完整评估已经整体可上线。** 本轮 5 条不同的真实视频中，4 条出现
时效题之间的矛盾，未形成有效完整评估；另 1 条形成有效评估但低于现有入池阈值。
合成长文也形成有效评估但未入池。它们均未被伪装成成功入池；小样本不用于推断总体误拒率。
下一步需要改进有关联的时效题契约，再进行语义与准入质量验收，不能放宽校验或降低阈值
只为让测试变绿。可考虑由单个受控选项联合表达关联字段，仍禁止多次评估和隐式 LLM 回退。

iPhone 实机未完成：已找到配对设备，但 Safari 无法创建自动化会话，已请求用户解锁并开启
远程自动化。移动 Web 的 Chromium 390×844 触屏验证不能冒充 iOS Safari 实机验证。
本次没有合回 main、push、部署或重启日常服务。

## 环境与边界

- 独立分支：`feat/jev-live-acceptance-20261106`，基于 `bd691948`。
- 使用正式 `openbiliclaw.api.app:create_app` 工厂、真实配置 API / apply-status、运行时与 SQLite。
  观测器仅添加 HTTP 计量与防重复请求费用上限，不替换 evaluator、provider 或推理结果。
- 固定模型：`typesafe/jev-1.14-20261014`；原生接口 `https://openrouter.ai/api/v1/systemone`。
  未使用 Chat Completions、自动换模型或生成式 LLM fallback。
- 画像取日常数据的只读副本，保留 effective overrides；校验源文件 SHA-256 前后不变。
  本地按需画像目录为 24,686 UTF-8 bytes；并非把整份源画像原封不动发给模型。
- 主容量组使用两条真实 B 站记录及一条明确标记的合成长文；追加合批组使用三条不同的
  较短真实视频记录。原始正文、画像、Token 和原始网络响应不入仓库。
- 日常服务未被切换。隔离配置在测试期间由页面保存真实 Token，结束后删除本轮生成的
  `config.toml` 及其备份；API 的 `reveal_keys=true` 也不回显，实际扩展存储未包含 Token。
- 来源发现不是本次目标：后台外部 B 站直连接入被本地代理隔离，评估使用已保存记录。
  实际扩展曾额外采到候选，导致“全库恰好三条停放”的测试断言失败；后续关闭本轮扩展后
  在干净隔离 cohort 重跑移动容量验收。不能把该测试隔离错误当成模型超限。

## 本轮发现并修复

1. **OpenRouter 新增计费字段导致有效答案被拒绝。** 首个真实请求为 HTTP 200 / 10 题，
   但严格解析器拒绝 `usage.is_byok` 和 `usage.cost_details`。增加精确白名单和严格类型，
   保留未知字段拒绝、非负有限费用校验；不把计费字段混入业务证据。捕获的合成响应离线
   重放通过，后续三端真实探测也通过。
2. **矛盾时效答案重复产生费用。** 原流程把 `inconsistent_temporal_evidence` 折叠为
   `evaluation_response_missing`，后台反复请求；诊断轮由独立费用上限截停。
   改为 `InvalidEvaluationEvidence` → 租约 CAS 停放
   `failed_eval / invalid_evaluation_evidence:<context digest>`。不改为有效 unknown，
   不写成功评分/缓存、不借用 LLM；同上下文不重试，画像/模型/容量等有效上下文改变后有界恢复。
   新增 `candidate_eval_invalid_evidence_blocked`，与本地输入超限分开统计。
3. **补齐显式页面探测。** 三端共享“测试连接与评估”按钮，使用当前草稿，固定合成画像与
   1 条内容的完整 10 题；一次 HTTP 尝试，传输最多 15 秒、总计 20 秒。提示费用、不保存配置，
   编辑后旧结果失效；复用配置写鉴权。配置 readiness 不再被误当成真实连通性证明。

## 三端真实验收

| 表面 | 连接输入 | 草稿真实探测 | 保存与实际使用 |
|---|---|---|---|
| 桌面 `/web/` | OpenRouter Base URL、Token、固定 ID、64,000 / 32,000 | 1 请求 / 10 题，有效完整结果，约 1.72 秒；配置文件未变 | PUT 202 → applied → 候选实际使用新设置；Token 清空、不回显 |
| 实际 MV3 插件 | 完整原生 URL、Token、48,000 / 24,000 | 1 请求 / 10 题，有效完整结果，约 2.75 秒；不保存 | Chrome for Testing 有头模式，真实 service worker / `chrome.storage`，无 Chrome API stub；保存后容量与端点真实生效 |
| 移动 `/m/` | 同一表单设置 12,000 / 12,000，再调为 56,000 / 28,000 | 调大后的草稿 1 请求 / 10 题，有效完整结果，约 1.92 秒 | 小容量停放、相同容量零请求、调大恢复，关闭重开设置和后端重启均正确 |

MV3 初次 headless 未获得 service worker，改用仓库已有的有头 Chrome for Testing 路径后
通过；没有安装到用户日常浏览器。扩展元素截图初次超时，改为页面截图重跑；不是 HTTP
或配置失败。成功表面不因后续测试工具失败而重新发付费探测。

## 上下文与候选生命周期

以下均为包含原生 envelope 的 **UTF-8 字节数**；容量字段名是 token 上限，但当前实现用
保守 1:1 byte ceiling，而非官方精确 tokenizer。接近字节护栏不等于用满 token 窗口。

| 场景 | 总 / 单题护栏 | 真实请求 | 观察结果 |
|---|---|---|---|
| 桌面默认窗口，3 条 | 64,000 / 32,000 | 3 × 10 题 | 最长 state＋单题 31,899 bytes；2 条有效低分拒绝，1 条矛盾停放 |
| 插件收紧窗口，同 3 条 | 48,000 / 24,000 | 3 × 10 题 | 最长 23,894 bytes；同样有效拒绝与矛盾停放，无重复请求 |
| 移动小容量 | 12,000 / 12,000 | **0** | 3 条 `input_budget_exhausted`，两种 attempts 均为 0 |
| 移动保存相同容量 | 12,000 / 12,000 | **0** | 原停放项不重评、不额外收费 |
| 移动扩大容量，恢复原停放项 | 56,000 / 28,000 | 3 × 10 题 | 最长 27,894 bytes；3 条重新处理，矛盾项仍拒绝而非伪成功 |
| 重新启动正式服务 | 保持 56,000 / 28,000 | **0** | 本机 Token、固定模型、容量持久化；不复活相同上下文的停放项 |
| 追加 3 条短真实视频 | 64,000 / 32,000 | **2 请求：20 题 + 10 题** | 双候选请求 46,889 bytes、最长单题 29,333 bytes；实际多候选合批成立，但 3 条均因时效矛盾停放 |

请求侧逐条验证：必需画像与本地目录一致、保留正向兴趣且来自原目录、摘录的每个
`origins.start/end` 与源文本严格匹配。主容量组正向兴趣数分别为 `4/18/1`、`3/12/2`、
`3/16/2`；并非只剩负偏好。合成长文从 454 段按预算选择 105、64、84 段，保留
“如果用于生产，必须先备份；否则不适用”的条件与否定；覆盖标记明确表示 partial。
这证明局部出处与测试条件保留，**不证明语义无损压缩**。原画像/原文未回写替换。

## 费用与证据

修复后的主要验收为 3 次草稿探测 + 9 次候选请求 + 2 次追加合批请求，共 14 次请求 / 150 题。
另有诊断及工具恢复过程的真实调用，不能从账单中扣除：全部记录合计 32 次请求，
OpenRouter 返回的费用合计约 **US$0.01545**。这是返回的 usage 汇总，不是账单对账保证。

私有测试目录（均为本次生成，不含已删除的凭据配置）：

- `/tmp/obc-jev-all-e2e-20261106.gBHguA`：初始兼容失败、重复请求诊断、合成响应重放。
- `/tmp/obc-jev-final-e2e-20261106.eIidmM`：三端最终报告、保留的工具失败记录、脱敏请求计量。
- `/tmp/obc-jev-packing-e2e-20261106.Ff0Obs`：真实短视频合批报告。
- 工作树 `output/playwright/jev-real-{desktop,mv3,mobile}.png`：Token 已清空后的页面截图。

临时目录由操作系统管理，不保证长期保存；本记录保留不含私有原文的关键数字和失败结论。

## 回归

```bash
PYTHONPATH=src pytest -q tests/test_jev*.py tests/test_decision*.py \
  tests/test_discovery_candidate_store.py tests/test_candidate_eval_coordinator.py \
  tests/test_api_config_probe.py tests/test_desktop_web_config_probe.py \
  tests/test_profile_views_guards.py tests/test_i18n.py --tb=short --disable-warnings
```

结果：**593 passed**；包括正式工厂/真实本机 HTTP 的假推理回归，与上述付费实测分开记载。
另 `pytest tests/test_cli.py -k config_show`：6 passed；扩展 `npm test`：1857 passed；
`npm run build`、`ruff check src/ tests/`、`mypy src/`（364 文件）、i18n catalog check 均通过。
首次计费字段回归确实失败，修复后通过；矛盾证据单次请求/租约/恢复回归也先红后绿。

CLI、依赖、安装器流程和数据库 schema 未变；本次未声称 Docker 全新安装、Android/iOS
原生壳、手机实机、封面多模态或线上推荐满意度通过。Jev 完整评估仍为纯文本实验模式。
