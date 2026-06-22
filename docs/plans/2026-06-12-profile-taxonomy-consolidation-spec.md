# Phase: 画像一二级分类整理（一级词表化 + 二级全量清理） — Specification

**Created:** 2026-06-12
**Ambiguity score:** 0.12 (gate: ≤ 0.20)
**Requirements:** 7 locked

## Goal

画像一级分类（category）从开放集（实测 83 个、大量同义变体与孤儿分类）收敛为
≤20 项的固定词表并在源头强制约束；二级标签（interest name）在 top-128 边界之外
做一次全量去重清理；同时修复"同名异类盲合"的同名异义风险。完成后
`openbiliclaw profile` 洋葱树第一层节点集合 ⊆ 词表，且词表外分类无法再入库。

## Background

画像真身是扁平标签表（`preference.json`），每个标签挂 `name` + `category` +
`weight`；洋葱树第一层按 `category` 分组重建（`profile.py:populate_from_flat_preference`）。

**一级现状**：分类由偏好分析 LLM 自由命名，唯一约束是"必须中文"
（`llm/prompts.py:150`）。真实画像已积累 1049 个标签摊在 83 个分类上，其中
大量同义变体（娱乐/泛娱乐/文娱/生活娱乐；萌宠/宠物/动物；科技/技术/人工智能/数码；
内容解读/内容偏好/内容形态/内容形式/内容消费/内容消费方式），39 个分类仅挂
1 个标签。没有任何机制整理分类词表本身。

**二级现状**：`soul/consolidator.py` 的 `ProfileConsolidator` 已实现
规则合并 → embedding 聚类 → no-merge 记忆 → LLM 裁决（merge/keep 操作）→
校验执行的流水线，含快照/回滚/changelog/overrides 重映射安全机制，12h 调度。
但它只看权重 top-128 边界，900+ 长尾标签从未清理；阶段 0 规则合并
（`_rule_merge_exact_names`）同名即盲合、不看分类，存在同名异义
（如 苹果(科技) vs 苹果(美食)）误合风险；judge payload 只有
`{name, weight}`，LLM 无分类上下文可用。

本 phase 是 PR1–PR3（LLM-judged profile consolidation，commit 456e3c21 起）
的直接延续，复用其全部安全机制。

## Requirements

1. **一级词表常量**: 一级分类词表作为模块级代码常量存在，是分类的唯一合法集合。
   - Current: 分类是自由中文字符串，无词表概念；83 个分类即自由命名的结果。
   - Target: 模块级常量（如 `soul/taxonomy.py` 中 `CATEGORY_VOCAB`），
     条目 ≤ 20、必含兜底项「其他」；内容基于现有分类直方图人工审定
     （高频分类原名保留，如 娱乐/生活/科技/知识/游戏/资讯/体育/健康/社会/
     音乐/动漫/财经 等）。词表是代码常量而非 config 项。
   - Acceptance: 常量存在；单测断言 `len(CATEGORY_VOCAB) <= 20` 且
     `"其他" in CATEGORY_VOCAB`。

2. **一次性分类迁移**: 存在 CLI 入口将存量分类按 LLM 产出的映射表迁移到词表，
   默认 dry-run，可回滚。
   - Current: 无任何迁移/改写分类的入口，83 个分类无法收敛。
   - Target: CLI 入口（挂 `profile-consolidate` 旗标或独立命令，命名由 plan 决定）：
     一次 LLM 调用产出「现存分类 → 词表项」完整映射（输入带各分类的标签数，
     新 prompt builder 遵循 prompt-cache 规范）；代码校验——每个现存分类被映射
     恰好一次、映射目标必须 ∈ 词表，校验不过整体放弃、不写任何数据；通过后
     改写全部标签的 `category` 字段并重建洋葱树。写入前完整快照存入
     `data/memory/consolidation_runs/`（新 run 类型），支持按 run_id 回滚；
     追加 `soul_changelog.md` 审计。LLM 不可用时降级为只读预览
     （与 62ad4b30 的降级行为一致）。
   - Acceptance: 对当前真实画像 dry-run 打印覆盖全部现存分类的完整映射；
     `--apply` 后词表外分类数 = 0、落「其他」的标签占比 ≤ 10%、
     洋葱树第一层节点集合 ⊆ 词表；`--revert <run_id>` 后
     `preference.json` 的 interests 与迁移前一致。

3. **源头约束（preference_analyzer）**: 词表外分类无法再入库。
   - Current: 偏好分析 prompt 对 category 只要求中文（`prompts.py:150,160`），
     LLM 自由发明；`preference_analyzer.py` 按 `(name, category)` 精确键合并，
     新变体分类直接入库。
   - Target: 偏好分析 system prompt 注入词表常量（静态常量 → 字节不变，
     缓存安全），要求 category 必须从词表中选择；代码侧兜底——返回分类 ∉ 词表
     时用 embedding 在词表内取最近邻，无 embedding 服务或相似度过低则落「其他」。
     任何路径都不允许词表外分类写入 preference 层。
   - Acceptance: 单测——分析器返回 `category="内容消费方式"`（词表外）时，
     入库 category ∈ 词表；修改后的 prompt builder 在
     `test_prompt_builder_system_messages_are_call_invariant` 中通过。

4. **同名异义防护（规则合并收紧）**: 阶段 0 规则合并只吃同名同类；同名异类交 LLM 裁决。
   - Current: `_rule_merge_exact_names` 按归一化名字盲合，不看 category——
     同名异义条目（苹果(科技) vs 苹果(美食)）会被错误合并。
   - Target: 同名 + 同分类 → 阶段 0 免费规则合并（迁移后大量浮现的精确重复
     由此消化）；同名 + 异分类 → 不再规则合并，构造为 2 成员嫌疑簇强制送
     LLM 裁决（不依赖 embedding 聚类是否抓到）。
   - Acceptance: 单测——fixture 苹果(科技)/苹果(美食) 不被规则合并且出现在
     送审簇中；猫咪(萌宠)×2（同名同类）被阶段 0 合并。

5. **judge payload 带 category + 同名异义规则**: LLM 裁决时能看到一级分类。
   - Current: likes payload 仅 `{name, weight}`（`consolidator.py:441-451`）；
     system prompt 无同名异义规则。
   - Target: likes 簇成员对象增加 `category` 字段（per-call 数据，置于 user
     消息、`sort_keys` 序列化，不破坏缓存规范）；system prompt 增补规则——
     同名/近名但分属不同一级且语义不同（同名异义）必须分别 keep。
     dislikes payload 不变（避雷主题无分类）。
   - Acceptance: 构造的 likes payload 含 `category` 字段；system prompt 含
     同名异义 keep 规则文本；prompt 不变性测试通过。

6. **二级全量清理（--full）**: 整理边界可一次性开到全量标签库，簇分批送审，单 run 可整体回滚。
   - Current: 边界固定 top-128（`likes_boundary` 构造参数存在但 CLI 不暴露）；
     全部簇打包为单次 LLM 调用，全量时输出 JSON 会顶到 max_tokens 截断。
   - Target: `profile-consolidate --full` 将 likes 边界开到全量标签库；
     嫌疑簇按 ≤30 个/批拆为多次 LLM 调用、逐批独立校验；全部批次的合并
     汇入单个 run 记录（一次 `--revert` 全量回滚）；no-merge 记忆、
     overrides 重映射、changelog 照常生效。不加 `--full` 时的默认行为
     与 12h 定时任务完全不变。
   - Acceptance: fixture >128 标签时 `--full` dry-run 能产出权重排名 128 之外
     的嫌疑簇；>30 簇时发起多次 LLM 调用且每次 ≤30 簇；`--apply` 产生单个
     run 记录且可整体 revert；无 `--full` 的现有行为回归测试通过。

7. **测试与文档同步**: 按仓库文档规则同步。
   - Current: `docs/modules/soul.md` / `cli.md` 描述 top-128 现状，无词表概念。
   - Target: `docs/modules/soul.md`（词表、迁移、同名异义防护、--full）、
     `docs/modules/cli.md`（新 flag/命令）、`docs/changelog.md`（当前版本块
     PR bullet）更新；新增/修改的 prompt builder 全部注册进
     `_builder_test_inputs()`；迁移 → `--full` 的推荐执行顺序写入 soul.md
     运维说明。
   - Acceptance: 上述文档项齐全；`pytest`、`ruff check`、`mypy src/` 全部通过。

## Boundaries

**In scope:**
- 一级词表常量（≤20 含「其他」）
- 一次性分类迁移 CLI（LLM 映射 + 校验 + dry-run/apply + 快照/revert + changelog）
- preference_analyzer 源头词表约束 + embedding 最近邻兜底
- 阶段 0 规则合并收紧（同名同类合 / 同名异类送审）
- judge payload 带 category + system prompt 同名异义规则
- `profile-consolidate --full`（边界全开 + 簇 ≤30/批分批送审 + 单 run 回滚）
- 配套测试与文档

**Out of scope:**
- 周期性一级分类整理 — 词表化后一级是闭集，漂移已被源头约束（R3）阻断，无需周期任务
- LLM 自动扩词表、「其他」膨胀自动报警 — 扩词表是人工决策（防词表再次失控）；报警留作后续小改进
- 让 LLM 一次性重写整个分类树 — 违反"LLM 只出操作、代码执行"契约，不可校验、不可回滚
- dislikes（避雷主题）一级化 — 避雷无分类字段，本就单层
- 洋葱树渲染 / 前端展示改动 — 树由重建自然反映新分类，无 UI 工作
- likes 长尾权重衰减策略调整 — 与本 phase 无关，现有衰减照旧

## Constraints

- **Prompt-cache 规范**（CLAUDE.md）：所有新增/修改的 prompt builder，system
  prompt 必须是模块级静态常量；词表注入 system prompt 依赖"词表是代码常量"
  这一事实（改词表 = 改代码 + 重新迁移，不做 config 项）；JSON 序列化一律
  `ensure_ascii=False, indent=2, sort_keys=True`。
- **复用既有安全机制**：快照/revert/changelog/no-merge 记忆/overrides 重映射
  全部沿用 `ProfileConsolidator` 现有实现，不另造一套。
- **成本**：一次性成本 ≈ 1 次映射调用 + ~1k embedding 调用 + ≤10 批裁决调用；
  稳态成本不变（12h top-128 + 输入脏检查 + no-merge 记忆，稳定画像零调用）。
- **执行顺序**：先分类迁移（R2）再 `--full`（R6）——迁移后同名同类精确重复
  大量浮现，由阶段 0 免费消化，减少送审簇数。
- 代码规范照旧：mypy strict、ruff、100 字符行宽、Conventional Commits。

## Acceptance Criteria

- [ ] `CATEGORY_VOCAB` 常量存在，≤20 项且含「其他」
- [ ] 迁移 dry-run 打印覆盖全部现存分类的完整映射，不写任何数据
- [ ] 迁移 `--apply` 后：词表外分类 = 0；「其他」标签占比 ≤ 10%；树第一层 ⊆ 词表
- [ ] 迁移 `--revert` 恢复 interests 至迁移前状态
- [ ] 映射校验失败（漏映射/目标不在词表）时整体放弃、零写入
- [ ] 分析器返回词表外分类时，入库 category ∈ 词表（最近邻或「其他」）
- [ ] 同名异类 fixture 不被规则合并、强制进入送审簇；同名同类被阶段 0 合并
- [ ] likes judge payload 含 `category`；system prompt 含同名异义 keep 规则
- [ ] `--full`：边界全开、簇 ≤30/批多次调用、单 run 记录可整体 revert
- [ ] 无 `--full` 的默认行为与 12h 定时路径回归不变
- [ ] 新增/修改 builder 全部通过 prompt 不变性测试
- [ ] `pytest` / `ruff check` / `mypy src/` 全绿；soul.md / cli.md / changelog.md 同步

## Ambiguity Report

| Dimension          | Score | Min  | Status | Notes                                  |
|--------------------|-------|------|--------|----------------------------------------|
| Goal Clarity       | 0.90  | 0.75 | ✓      | 收敛目标量化（≤20 词表、0 词表外分类） |
| Boundary Clarity   | 0.90  | 0.70 | ✓      | 显式 out-of-scope 六项                 |
| Constraint Clarity | 0.85  | 0.65 | ✓      | 缓存规范/成本/执行顺序均锁定           |
| Acceptance Criteria| 0.85  | 0.70 | ✓      | 13 条 pass/fail                        |
| **Ambiguity**      | 0.12  | ≤0.20| ✓      |                                        |

## Interview Log

需求澄清在本会话的设计讨论中完成（auto 模式，未另行提问），关键决策：

| Round | Perspective     | Question summary                     | Decision locked                                      |
|-------|-----------------|--------------------------------------|------------------------------------------------------|
| 1     | Researcher      | 现状是什么？                         | 1049 标签 / 83 分类；整理器只动二级 top-128           |
| 2     | Simplifier      | 一级怎么治本？                       | 固定词表（代码常量）+ 源头约束，否决周期性一级整理     |
| 3     | Boundary Keeper | 二级合并跨一级还是分一级？           | 保持全局跨一级（重复恰恰跨一级；top-64 名额是全局的） |
| 4     | Failure Analyst | 跨一级合并最坏会怎样？               | 同名异义盲合 → 规则合并收紧 + payload 带 category     |
| 5     | Seed Closer     | 全量清理怎么不超 token？             | 簇 ≤30/批分批送审，汇入单 run 记录                    |

---

*Spec created: 2026-06-12*
*Next step: /gsd-discuss-phase — 实现决策（CLI 旗标命名、taxonomy 模块边界、迁移 prompt 细节）*
