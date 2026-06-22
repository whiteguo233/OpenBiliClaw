# 画像一二级分类整理（一级词表化 + 二级全量清理）实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task is test-first with an atomic commit.

**Goal:** 实现 `docs/plans/2026-06-12-profile-taxonomy-consolidation-spec.md` 的全部 7 条需求：一级分类从开放集（实测 83 个）收敛为 ≤20 项固定词表并在源头强制约束（R1/R2/R3）；修复阶段 0 规则合并的同名异义盲合风险（R4/R5）；二级标签清理边界可一次性开到全量标签库（R6）；测试与文档同步（R7）。完成后 `openbiliclaw profile` 洋葱树第一层节点集合 ⊆ 词表，词表外分类无法再入库。

**Architecture:** 新增 `soul/taxonomy.py`（词表常量 + 最近邻分类解析器，零依赖小模块）与 `soul/category_migration.py`（一次性迁移引擎，复用 `ProfileConsolidator` 的 run 记录目录 / revert / changelog 安全机制）。`llm/prompts.py` 新增 `build_category_mapping_prompt`、改造偏好分析 system prompt 注入词表。`preference_analyzer` 在归一化后、`(name, category)` 键合并前做词表 clamp。`consolidator.py` 收紧阶段 0 规则合并（同名同类才合，同名异类构造强制嫌疑簇）、judge payload 带 category、`_judge` 按 ≤30 簇/批分批送审。CLI 在既有 `profile-consolidate` 上挂 `--migrate-categories` 与 `--full` 两个旗标。

**Tech Stack:** Python 3.11+ / asyncio / Typer / pytest（asyncio_mode=auto）/ ruff / mypy strict。测试与检查一律用 `.venv/bin/python`（裸 `python`/`pytest` 无包）。

---

## Source Spec

- Spec: `docs/plans/2026-06-12-profile-taxonomy-consolidation-spec.md`（7 条需求 + 13 条验收，本计划逐条实现，不重开其决策）
- 直接延续 PR1–PR3（LLM-judged profile consolidation，commit 456e3c21 / bb0af82a / 3a49e97f），复用其全部安全机制（快照 / revert / changelog / no-merge 记忆 / overrides 重映射），不另造一套。

## Design Decisions（已锁定 — 不要重开）

- **CLI 形态：** 扩展既有 `profile-consolidate` 命令，不加新命令。新增 `--migrate-categories`（一次性分类迁移，默认 dry-run，配 `--apply`）与 `--full`（likes 边界开到全量，配 `--apply`）。两旗标同一次调用互斥。回滚复用既有 `--revert <run_id>`——run 记录新增 `"kind"` 字段区分类型（`"consolidation"` | `"category_migration"`），`revert()` 本身不分支（两种记录共享 `before` 快照结构）。
- **词表（19 项，含「其他」）：** 基于真实分类直方图（娱乐223 生活133 科技123 知识109 游戏76 资讯61 体育36 健康31 社会25 音乐23 技术21 动漫20 财经20 …）审定为：
  `娱乐 生活 科技 知识 游戏 资讯 体育 健康 社会 音乐 动漫 财经 影视 美食 教育 文化 萌宠 汽车 其他`
  （技术→科技、二次元→动漫、商业→财经 由迁移映射收敛；影视/萌宠为 B 站高频天然域。）词表是 `soul/taxonomy.py` 的代码常量，不做 config 项。
- **taxonomy 模块边界：** `soul/taxonomy.py` 只含 `CATEGORY_VOCAB` 常量 + `resolve_category()` 异步解析器 + `SupportsEmbed` Protocol。**禁止在模块顶层 import `openbiliclaw.llm`**（`prompts.py` 要 import 本模块，避免环）；cosine 用函数内惰性 import（与 `consolidator._cosine` 同款）。
- **迁移引擎独立成 `soul/category_migration.py`：** 一次性迁移与 12h 稳态整理生命周期完全不同，独立小模块让 `consolidator.py` 保持聚焦（仅抽一个共享的树重建 helper）。
- **迁移 prompt：** 新 builder `build_category_mapping_prompt`，system 为静态模块常量 `_CATEGORY_MAPPING_SYSTEM_PROMPT`（任务：每个现存分类映射到恰好一个词表项；规则：完整覆盖、不得发明目标、语义优先、兜底「其他」；严格 JSON 输出 `{"mapping": {"旧分类": "词表项", ...}}`）；user 消息装词表 + `[{"category": ..., "tag_count": ...}]`，序列化一律 `ensure_ascii=False, indent=2, sort_keys=True`；注册进 `_builder_test_inputs()`。
- **偏好分析词表注入位置：** R3 明确要求词表注入 **system** prompt（词表是代码常量 → 字节不变 → 缓存安全）；迁移 prompt 的词表则按上条放 **user** 消息。两者不冲突，分别照办。
- **任务分三个 PR 波次**（spec 锁定执行顺序：先迁移 R2 再 `--full` R6——迁移后同名同类精确重复大量浮现，由阶段 0 免费消化）。

---

## PR1：词表常量 + 一次性分类迁移（R1 + R2）

### Task 1: `soul/taxonomy.py` — 词表常量与分类解析器

**Files:**
- Create: `src/openbiliclaw/soul/taxonomy.py`
- Test: `tests/test_taxonomy.py`

**Step 1: 写失败测试** `tests/test_taxonomy.py`：

```python
import pytest

from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB, FALLBACK_CATEGORY, resolve_category

@pytest.fixture(autouse=True)
def _clear_vocab_vector_cache() -> None:
    # _vocab_vectors 是模块级缓存；各测试用不同 stub embedding，残留向量遇上
    # cosine_similarity 的 zip(strict=False) 静默截断，会让相似度依测试顺序漂移。
    from openbiliclaw.soul import taxonomy
    taxonomy._vocab_vectors.clear()

def test_vocab_bounded_with_fallback() -> None:
    assert len(CATEGORY_VOCAB) <= 20
    assert "其他" in CATEGORY_VOCAB
    assert FALLBACK_CATEGORY == "其他"
    assert len(set(CATEGORY_VOCAB)) == len(CATEGORY_VOCAB)  # 无重复

async def test_resolve_exact_match_passthrough() -> None:
    assert await resolve_category("科技", None) == "科技"
    assert await resolve_category(" 科技 ", None) == "科技"  # strip 后命中

async def test_resolve_without_embedding_falls_back() -> None:
    assert await resolve_category("内容消费方式", None) == "其他"
    assert await resolve_category("", None) == "其他"

async def test_resolve_nearest_neighbor_with_stub_embedding() -> None:
    # stub：把「技术」和「科技」给同向量（cos=1），其余正交
    assert await resolve_category("技术", _StubEmbed({"技术": "科技"})) == "科技"

async def test_resolve_low_similarity_falls_back() -> None:
    # stub：全部正交（cos=0 < 阈值）
    assert await resolve_category("完全无关词", _StubEmbed({})) == "其他"

async def test_resolve_embedding_failure_falls_back() -> None:
    # stub embed() 抛异常 → 「其他」，不向上抛
```

stub 写法参考 `tests/test_profile_consolidator.py::_StubEmbedding`（同组同向量、异组正交）。

**Step 2: 跑测确认失败**

```bash
.venv/bin/python -m pytest tests/test_taxonomy.py -q
```

预期：ModuleNotFoundError / ImportError。

**Step 3: 实现** `src/openbiliclaw/soul/taxonomy.py`：

```python
"""Closed category vocabulary for the first level of the interest tree."""
from __future__ import annotations
from typing import Protocol

CATEGORY_VOCAB: tuple[str, ...] = (
    "娱乐", "生活", "科技", "知识", "游戏", "资讯", "体育", "健康", "社会",
    "音乐", "动漫", "财经", "影视", "美食", "教育", "文化", "萌宠", "汽车", "其他",
)
FALLBACK_CATEGORY = "其他"
# 分类最近邻是「语义归属」判断，不是 consolidator 的「近重复」判断（0.85），
# 阈值放低；低于它宁可落「其他」也不硬塞。
_NN_SIMILARITY_THRESHOLD = 0.55

class SupportsEmbed(Protocol):
    async def embed(self, text: str) -> list[float]: ...

# 词表向量惰性缓存。单用户单 embedding 配置每进程，按词缓存即可。
_vocab_vectors: dict[str, list[float]] = {}

async def resolve_category(raw: str, embed: SupportsEmbed | None = None) -> str:
    """Exact vocab match → as-is; else embedding NN over vocab; else 其他."""
    name = str(raw or "").strip()
    if name in CATEGORY_VOCAB:
        return name
    if not name or embed is None:
        return FALLBACK_CATEGORY
    try:
        from openbiliclaw.llm.embedding import cosine_similarity  # 惰性，避免环

        raw_vec = await embed.embed(name)
        if not raw_vec:
            return FALLBACK_CATEGORY
        best, best_sim = FALLBACK_CATEGORY, 0.0
        for term in CATEGORY_VOCAB:
            if term == FALLBACK_CATEGORY:
                continue  # 「其他」是兜底桶，不参与语义最近邻
            vec = _vocab_vectors.get(term)
            if vec is None:
                vec = await embed.embed(term)
                if vec:
                    _vocab_vectors[term] = vec
            if not vec:
                continue
            sim = cosine_similarity(raw_vec, vec)
            if sim > best_sim:
                best, best_sim = term, sim
        return best if best_sim >= _NN_SIMILARITY_THRESHOLD else FALLBACK_CATEGORY
    except Exception:
        return FALLBACK_CATEGORY
```

注意：调用方都是 async（preference_analyzer、迁移引擎），不做 sync wrapper。

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_taxonomy.py -q
git add src/openbiliclaw/soul/taxonomy.py tests/test_taxonomy.py
git commit -m "feat: add soul category taxonomy vocab and resolver"
```

---

### Task 2: `build_category_mapping_prompt` — 迁移映射 prompt builder

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`
- Test: `tests/test_llm_prompts.py`（`_builder_test_inputs()` 注册 + 内容断言）

**Step 1: 写失败测试**

在 `tests/test_llm_prompts.py` 的 `_builder_test_inputs()`（465 行起）追加一行：

```python
(
    "build_category_mapping_prompt",
    dict(categories=[{"category": "泛娱乐", "tag_count": 12}]),
    dict(categories=[{"category": "内容消费方式", "tag_count": 3},
                     {"category": "宠物", "tag_count": 7}]),
),
```

另加内容测试：

```python
def test_category_mapping_prompt_user_message_carries_vocab_and_histogram() -> None:
    from openbiliclaw.llm.prompts import build_category_mapping_prompt
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    msgs = build_category_mapping_prompt(
        categories=[{"category": "泛娱乐", "tag_count": 12}]
    )
    user = msgs[1]["content"]
    assert all(term in user for term in CATEGORY_VOCAB)  # 词表在 user 消息
    assert "泛娱乐" in user and '"tag_count": 12' in user
    sys = msgs[0]["content"]
    # 探针不能用「泛娱乐」——它出现在 system 静态示例里（规则 3 的归属示例 + output_schema 示例），
    # 静态常量含示例词不是缓存违规。用只可能来自 per-call 序列化的片段验证 system 无逐次数据：
    assert '"tag_count": 12' not in sys
    assert '"mapping"' in sys  # 输出 schema 在 system
```

**Step 2: 跑测确认失败**

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -k "category_mapping or invariant" -q
```

**Step 3: 实现** — `llm/prompts.py` 末尾（`build_profile_consolidation_prompt` 之后）追加：

```python
_CATEGORY_MAPPING_SYSTEM_PROMPT = (
    "<task>\n"
    "你是用户画像分类体系的迁移器。user 消息提供：vocab（固定一级分类词表）和\n"
    "categories（现存分类及各自的标签数 tag_count）。\n"
    "你要为【每一个】现存分类选择词表中【恰好一个】目标分类。\n"
    "</task>\n"
    "\n"
    "<rules>\n"
    "1. mapping 必须覆盖 categories 里的每一个分类，一个都不能漏，也不能多出输入里没有的分类。\n"
    "2. 映射目标必须逐字来自 vocab，不得发明新分类、不得返回 vocab 之外的写法。\n"
    "3. 优先语义归属（如 泛娱乐/文娱→娱乐；宠物/动物→萌宠；技术/数码/人工智能→科技；\n"
    "   二次元→动漫；商业→财经）。\n"
    "4. 现存分类本身已在 vocab 中的，映射到它自己。\n"
    "5. 实在无法归属的才映射到「其他」，不要偷懒批量扔「其他」。\n"
    "6. 输出严格 JSON，不要附带解释文本。\n"
    "</rules>\n"
    "\n"
    "<output_schema>\n"
    "{\n"
    '  "mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "内容消费方式": "其他"}\n'
    "}\n"
    "</output_schema>"
)


def build_category_mapping_prompt(
    *,
    categories: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Map existing free-form categories onto the fixed taxonomy vocab.

    System prompt is fully static (cache-friendly); vocab + histogram both
    live in the user message with deterministic serialization.
    """
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    payload = {
        "categories": sorted(
            categories, key=lambda c: (-int(c.get("tag_count", 0) or 0), str(c.get("category", "")))
        ),
        "vocab": list(CATEGORY_VOCAB),
    }
    user_prompt = "\n\n".join(
        [
            "<category_mapping_context>",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            "</category_mapping_context>",
        ]
    )
    return [
        {"role": "system", "content": _CATEGORY_MAPPING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
```

（`from openbiliclaw.soul.taxonomy import ...` 放函数内，保持 prompts.py 顶层不依赖 soul 包——taxonomy 零依赖所以顶层 import 也无环，但函数内 import 与本文件其它惰性引用一致、且彻底排除未来环风险。）

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -q
git add src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat: add category mapping prompt builder"
```

---

### Task 3: `soul/category_migration.py` — 迁移引擎 + run 记录 kind 字段

**Files:**
- Create: `src/openbiliclaw/soul/category_migration.py`
- Modify: `src/openbiliclaw/soul/consolidator.py`（`_write_run_record` 加 `"kind"`；抽共享树重建 helper）
- Test: `tests/test_category_migration.py`、`tests/test_profile_consolidator.py`（kind 断言）

**Step 1: 写失败测试** `tests/test_category_migration.py`（fixture 复用 `tests/test_profile_consolidator.py` 的 `_FakeMemory` / `_FakeLayer` / `_StubLLM` / `_interest` 模式——直接拷贝这几个类到新文件或抽到 conftest；二选一，倾向拷贝保持测试文件自包含，与现有仓库风格一致）：

```python
async def test_dry_run_prints_full_mapping_and_writes_nothing(tmp_path):
    # interests: 泛娱乐 x2, 宠物 x1, 科技 x1（in-vocab）
    # StubLLM mapping: {"泛娱乐": "娱乐", "宠物": "萌宠", "科技": "科技"}
    report = await migrator.run(dry_run=True)
    assert set(report.mapping) == {"泛娱乐", "宠物", "科技"}   # 覆盖全部现存分类
    assert all(v in CATEGORY_VOCAB for v in report.mapping.values())
    assert memory.get_layer("preference").save_count == 0      # 零写入
    assert not (tmp_path / "consolidation_runs").exists()

async def test_validation_gap_aborts_with_zero_writes(tmp_path):
    # StubLLM 漏掉「宠物」 → report.errors 非空、mapping 为空 dict、零写入
    # 第二个用例：StubLLM 返回目标「数码」（∉ 词表）→ 同样整体放弃

async def test_apply_rewrites_categories_and_records_run(tmp_path):
    report = await migrator.run(dry_run=False)
    cats = {i["category"] for i in memory.get_layer("preference").data["interests"]}
    assert cats <= set(CATEGORY_VOCAB)                          # 词表外 = 0
    record = json.loads(next((tmp_path / "consolidation_runs").glob("*.json")).read_text(...))
    assert record["kind"] == "category_migration"
    assert record["mapping"]["泛娱乐"] == "娱乐"
    assert len(record["before"]["interests"]) == 4              # 完整快照
    assert "分类迁移" in (tmp_path / "soul_changelog.md").read_text(...)

async def test_revert_restores_interests_byte_identical(tmp_path):
    before = [dict(i) for i in memory.get_layer("preference").data["interests"]]
    report = await migrator.run(dry_run=False)
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)
    assert consolidator.revert(report.run_id)
    assert memory.get_layer("preference").data["interests"] == before  # 逐字段一致

async def test_in_vocab_categories_forced_identity(tmp_path):
    # StubLLM 恶意返回 {"科技": "生活", ...} → 代码强制 in-vocab 恒等映射，科技仍是科技

async def test_llm_unavailable_degrades_to_preview(tmp_path):
    # llm_service=None → report.errors 含 "llm: service unavailable"，histogram 有值，零写入

async def test_empty_category_assigned_fallback(tmp_path):
    # category="" 的条目不送 LLM，apply 后落「其他」

async def test_apply_rebuilds_onion_tree_first_level_within_vocab(tmp_path):
    # 带 soul 层 fixture（参照 test_apply_rebuilds_onion_tree）：
    # apply 后 OnionProfile.interest.likes 的 domain 集合 ⊆ CATEGORY_VOCAB
```

`tests/test_profile_consolidator.py::test_llm_merge_applies_weight_and_timestamps` 追加一行断言：`assert record["kind"] == "consolidation"`。

**Step 2: 跑测确认失败**

```bash
.venv/bin/python -m pytest tests/test_category_migration.py -q
```

**Step 3: consolidator.py 小改**

1. `_write_run_record` 的 record dict 加 `"kind": "consolidation"`（放 `"run_id"` 之后）。
2. 把 `_rebuild_profile_tree`（595–612 行）的函数体抽成模块级函数，供迁移引擎复用：

```python
def rebuild_profile_tree(memory: MemoryManager, preference_data: dict[str, object]) -> None:
    """Rebuild the Onion interest tree from flat preference (shared helper)."""
    # …原 _rebuild_profile_tree 函数体原样搬入…

class ProfileConsolidator:
    def _rebuild_profile_tree(self, preference_data: dict[str, object]) -> None:
        rebuild_profile_tree(self._memory, preference_data)
```

`revert()` 不改——迁移记录共享 `before` 快照结构，`merges` 为空数组（no-merge pin 空转，无害），`overrides_before` 为 `None`（跳过 overrides 恢复）。

**Step 4: 实现** `src/openbiliclaw/soul/category_migration.py`：

```python
"""One-shot migration of free-form interest categories onto CATEGORY_VOCAB."""
from __future__ import annotations
# dataclass / json / logging / datetime / Path / Counter…

from openbiliclaw.llm.json_utils import DEFAULT_STRUCTURED_MAX_TOKENS, parse_llm_json_tolerant
from openbiliclaw.llm.prompts import build_category_mapping_prompt
from openbiliclaw.soul.consolidator import (
    _CHANGELOG_FILENAME, _RUNS_DIRNAME, SupportsStructuredTask, rebuild_profile_tree,
)
from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB, FALLBACK_CATEGORY


@dataclass
class CategoryMigrationReport:
    ran: bool = False
    dry_run: bool = False
    run_id: str = ""
    histogram: dict[str, int] = field(default_factory=dict)      # 现存分类 → 标签数
    mapping: dict[str, str] = field(default_factory=dict)        # 旧分类 → 词表项（校验通过才非空）
    target_counts: dict[str, int] = field(default_factory=dict)  # 词表项 → 迁移后标签数
    other_ratio: float = 0.0                                     # 「其他」占比（apply/dry-run 都算）
    applied: bool = False
    errors: list[str] = field(default_factory=list)


class CategoryMigrator:
    def __init__(self, *, memory, llm_service: SupportsStructuredTask | None,
                 data_dir: Path | str | None = None) -> None: ...

    async def run(self, *, dry_run: bool, now: datetime | None = None) -> CategoryMigrationReport:
        # 1. interests = preference 层 interests（dict 且 name 非空，逐项 dict() 拷贝）
        # 2. histogram = Counter(str(i.get("category","")).strip())；
        #    空串分类不送 LLM，代码直接预定为 FALLBACK_CATEGORY
        # 3. llm_service None → errors.append("llm: service unavailable")，
        #    填 histogram 后直接 return（只读预览，与 62ad4b30 降级一致）
        # 4. 一次 LLM 调用：build_category_mapping_prompt(categories=[{category, tag_count}, …])
        #    （全部非空分类都送，含 in-vocab 的——给模型完整上下文）；
        #    complete_structured_task(temperature=0.2, max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
        #                             caller="soul.category_migration")
        #    parse_llm_json_tolerant → parsed["mapping"]（非 dict → errors + return）
        # 5. in-vocab 键强制恒等映射（代码覆写 LLM 输出——确定性优于模型自由度，一行防呆）
        # 6. 校验（任一失败 → errors.append(原因)，mapping 清空，零写入直接 return）：
        #    a) set(mapping) == set(非空现存分类)（漏映射 / 多余键都算失败）
        #    b) all(v in CATEGORY_VOCAB for v in mapping.values())
        # 7. 计算 target_counts / other_ratio（空串分类计入「其他」）；
        #    other_ratio > 0.10 → logger.warning + errors 不追加（展示性警告，不阻断）
        # 8. dry_run → return（与 consolidator 同契约：dry-run 永不写）
        # 9. apply：
        #    before = {"interests": [dict(i) for i in 原始列表], "disliked_topics": list(原样)}
        #    逐项 item["category"] = mapping.get(old) or FALLBACK_CATEGORY
        #    preference_layer.save() → rebuild_profile_tree(memory, preference_layer.data)
        #    run 记录 {run_id, kind: "category_migration", before, mapping,
        #              merges: [], rename_map: {}, overrides_before: None}
        #      写入 data_dir / _RUNS_DIRNAME / f"{run_id}.json"
        #    changelog 追加 "## 分类迁移 {run_id}（YYYY-MM-DD HH:MM）" +
        #      每行 "- 旧分类(n 个标签) → 词表项" + 「其他」占比一行
```

实现要点：
- `run_id` 格式与 consolidator 一致（`%Y%m%d-%H%M%S`），保证 `--revert` 入口通用。
- `data_dir` 解析必须复刻 consolidator 的回退链（consolidator.py:169）：
  `Path(data_dir or getattr(memory, "_data_dir", None))`（None 则禁用落盘）。否则 CLI
  不传 `data_dir` 时 run 记录静默不落盘、却照样打印「已备份」，直接废掉验收 #4。
- 迁移**不动 overrides**：overrides 的 pins / list_edits 以兴趣名、避雷主题字符串为匹配键（`soul/overrides.py`），分类改名不影响；domain 级 `interest_edits` 若引用了被迁移的旧分类名，迁移后自然失配——这些编辑针对的正是要被消灭的脏域，打 `logger.warning` 列出受影响键即可，不做重映射（重映射有「分类名撞兴趣名」误改风险，得不偿失）。
- mypy strict：所有公开函数全注解；`parse_llm_json_tolerant` 返回值做 isinstance 收窄。

**Step 5: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_category_migration.py tests/test_profile_consolidator.py -q
git add src/openbiliclaw/soul/category_migration.py src/openbiliclaw/soul/consolidator.py \
        tests/test_category_migration.py tests/test_profile_consolidator.py
git commit -m "feat: one-shot category migration engine with snapshot and revert"
```

---

### Task 4: CLI `--migrate-categories` 旗标

**Files:**
- Modify: `src/openbiliclaw/cli.py`（`profile_consolidate`，5465 行起）

**Step 1: 实现**（CLI 是薄壳，引擎测试已在 Task 3 兜底；本任务以人工 smoke 验证为主）

在 `profile_consolidate` 签名追加：

```python
migrate_categories: bool = typer.Option(
    False,
    "--migrate-categories",
    help="一次性把存量一级分类迁移到固定词表（默认 dry-run，配 --apply 写入；可 --revert 回滚）。",
),
```

命令体内，在 `--revert` 分支之后插入迁移分支（复用函数里已就绪的 `memory` / `llm_service` 构建与降级逻辑；迁移不需要 embedding，跳过 embedding 不可用的提示也行，保留亦无害）：

```python
if migrate_categories:
    from openbiliclaw.soul.category_migration import CategoryMigrator

    migrator = CategoryMigrator(memory=memory, llm_service=llm_service)
    report = _asyncio.run(migrator.run(dry_run=not apply))
    for err in report.errors:
        console.print(f"[yellow]  ⚠ {err}[/yellow]")
    console.print(f"  现存分类: {len(report.histogram)} 个，标签 {sum(report.histogram.values())} 条")
    for old, new in sorted(report.mapping.items(), key=lambda kv: -report.histogram.get(kv[0], 0)):
        console.print(f"  {old}({report.histogram.get(old, 0)}) → [bold]{new}[/bold]")
    if report.mapping:
        console.print(f"\n  「其他」占比: {report.other_ratio:.1%}"
                      + ("  [yellow]⚠ 超过 10%[/yellow]" if report.other_ratio > 0.10 else ""))
    if not apply and report.mapping:
        console.print("\n  [dim]满意的话用 --apply 真正写入。[/dim]")
    if report.applied:
        console.print(f"\n  [dim]已备份，run_id={report.run_id}（--revert {report.run_id} 可回滚）[/dim]")
    # 降级只读预览（LLM 不可用，打印 histogram 即为成功，62ad4b30 契约）→ code=0；
    # 其余 errors 且无 mapping（映射校验失败 / LLM 调用异常）→ 非零退出。
    degraded = report.errors == ["llm: service unavailable"]
    if report.errors and not report.mapping and not degraded:
        raise typer.Exit(code=1)
    return
```

docstring 的 `\b` 列表追加一行 `- --migrate-categories 一次性分类词表迁移（同样 dry-run/--apply/--revert）`。

**Step 2: 验证**

```bash
.venv/bin/python -m pytest tests/test_category_migration.py -q
.venv/bin/python -m mypy src/openbiliclaw/cli.py
# 人工 smoke（真实画像，只读）：
.venv/bin/python -m openbiliclaw.cli profile-consolidate --migrate-categories
```

smoke 预期：打印覆盖全部 83 个现存分类的完整映射 + 「其他」占比，不写任何数据（验收 #2）。

**Step 3: 提交**

```bash
git add src/openbiliclaw/cli.py
git commit -m "feat: profile-consolidate --migrate-categories CLI flag"
```

---

### Task 5: PR1 文档同步

**Files:**
- Modify: `docs/modules/soul.md`、`docs/modules/cli.md`、`docs/changelog.md`

**Steps:**

1. `docs/modules/soul.md`：
   - 「已实现功能」表新增一行「分类词表 + 一次性迁移」：`taxonomy.CATEGORY_VOCAB`（19 项含「其他」，代码常量非 config）、`resolve_category`（精确 → embedding 最近邻 ≥0.55 → 其他）、`CategoryMigrator`（一次 LLM 映射 + 代码校验完整覆盖 / 目标 ∈ 词表，失败零写入；快照 `consolidation_runs/<run_id>.json` kind=category_migration；`--revert` 通用回滚）。
   - 公共 API 节补 `soul/taxonomy.py` 与 `soul/category_migration.py` 条目。
2. `docs/modules/cli.md`：46 行命令总表与 383 行起的 `profile-consolidate` 详情节补 `--migrate-categories` 用法示例（dry-run / `--apply` / `--revert <run_id>`）。
3. `docs/changelog.md`：当前版本块（`## v0.3.120 …`）追加一条 PR bullet：「**画像一级分类词表化（PR1）**：新增 19 项固定分类词表 `CATEGORY_VOCAB` 与 `profile-consolidate --migrate-categories` 一次性迁移（LLM 映射 + 完整覆盖校验，失败零写入；dry-run 默认、快照可回滚、changelog 审计）」。
4. 跨模块布线没变（无新依赖块 / 数据流不变），architecture 图不动。

```bash
git add docs/modules/soul.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: sync category taxonomy and migration docs (PR1)"
```

---

## PR2：源头约束（R3）

### Task 6: 偏好分析 prompt 注入词表（system 静态注入）

**Files:**
- Modify: `src/openbiliclaw/llm/prompts.py`（`build_preference_analysis_prompt`，134 行起）
- Test: `tests/test_llm_prompts.py`

**Step 1: 写失败测试**

`_builder_test_inputs()` 追加（该 builder 此前未注册——本次必须补上，R3 验收点）：

```python
(
    "build_preference_analysis_prompt",
    dict(events=[{"event_type": "view", "title": "A"}], existing_preference={"a": 1}),
    dict(events=[{"event_type": "like", "title": "B"}], existing_preference={"a": 2}),
),
```

内容测试：

```python
def test_preference_analysis_system_prompt_contains_full_vocab() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    msgs = build_preference_analysis_prompt(events=[], existing_preference={})
    sys = msgs[0]["content"]
    assert all(term in sys for term in CATEGORY_VOCAB)
    assert "category 必须" in sys  # 约束语句存在
```

**Step 2: 跑测确认失败 → Step 3: 实现**

1. 把函数内的 `system_prompt = """…""".strip()` 提升为模块级常量 `_PREFERENCE_ANALYSIS_SYSTEM_PROMPT`，在模块导入期注入词表（常量拼常量 = 每次调用字节不变，缓存安全）：

```python
def _category_vocab_line() -> str:
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB
    return "、".join(CATEGORY_VOCAB)

_PREFERENCE_ANALYSIS_SYSTEM_PROMPT = """
<task>
…（原文不动）…
5. 所有文本字段（name、context 下的 patterns/session_type、disliked_topics）必须用中文。
   category 必须从以下固定词表中逐字选择，不得发明新分类、不得使用同义变体：
   __CATEGORY_VOCAB__。拿不准归属时用「其他」。
…（其余规则原文不动，规则编号顺延检查一遍）…
""".strip().replace("__CATEGORY_VOCAB__", _category_vocab_line())
```

（必须用 `.replace` 占位符而非 `.format`：原 prompt 的 `<output_schema>` 含 `{` `}` 字面量，`.format()` 会在模块导入期直接 KeyError。常量在导入期完成替换后仍是字节不变的模块级常量，缓存安全。）

2. `build_preference_analysis_prompt` 函数体改为引用该常量；user_prompt 不动。
3. 原规则 5 的「（name、category、…）必须用中文」中去掉 category（其约束已被词表条款取代且更强）。

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -q
git add src/openbiliclaw/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat: constrain preference analysis categories to taxonomy vocab"
```

---

### Task 7: preference_analyzer 入库 clamp + 引擎布线

**Files:**
- Modify: `src/openbiliclaw/soul/preference_analyzer.py`
- Modify: `src/openbiliclaw/soul/engine.py`（构造 + `set_embedding_service` 穿透）
- Test: `tests/test_preference_analyzer.py`

**Step 1: 写失败测试**（追加到 `tests/test_preference_analyzer.py`，stub LLM 仿照该文件既有模式；同时复刻 `tests/test_taxonomy.py` 的 `_clear_vocab_vector_cache` autouse fixture——NN 测试共享 `taxonomy._vocab_vectors` 模块级缓存，不清会测试顺序相关）：

```python
async def test_off_vocab_category_clamped_via_embedding_nn() -> None:
    # LLM 返回 interests=[{"name": "AI 工具", "category": "内容消费方式", …}]
    # stub embedding 把「内容消费方式」贴近「生活」 → 入库 category == "生活"
    merged = await analyzer.analyze_events(events=[…], existing_preference={})
    cats = {i["category"] for i in merged["interests"]}
    assert cats <= set(CATEGORY_VOCAB)

async def test_off_vocab_category_without_embedding_falls_to_other() -> None:
    # embedding_service=None → category == "其他"

async def test_in_vocab_category_passthrough_unchanged() -> None:
    # LLM 返回 category="科技" → 原样入库（不被改写）

async def test_clamp_collapses_variants_onto_same_merge_key() -> None:
    # existing_preference 已有 ("Python", "科技", w=0.5)；本批 LLM 返回 ("Python", "技术", w=0.8)
    # stub embedding 技术→科技 → clamp 后 (name, category) 键重合，合并为单条 w=0.8
    assert len([i for i in merged["interests"] if i["name"] == "Python"]) == 1

async def test_speculative_interests_clamped_too() -> None:
    # speculative_interests 的 category 同样 ∈ 词表
```

**Step 2: 跑测确认失败 → Step 3: 实现**

1. `PreferenceAnalyzer` dataclass 加字段（带默认值字段区，`satisfaction_filter_enabled` 附近）：

```python
from openbiliclaw.soul.taxonomy import SupportsEmbed, resolve_category
# dataclass 字段:
embedding_service: SupportsEmbed | None = None
```

2. 新增 helper，并替换**两处** `_normalize_preference(raw…)` 调用点（`_analyze_events_single` 209 行、`_run_chunk_once` 351 行——clamp 必须发生在 `merge_preferences` 的 `(name, category)` 键合并**之前**，变体分类才会塌缩到词表键上）：

```python
async def _normalize_and_resolve(self, raw_preference: dict[str, object]) -> dict[str, object]:
    normalized = self._normalize_preference(raw_preference)
    for key in ("interests", "speculative_interests"):
        for item in normalized.get(key, []):           # _as_list 收窄
            if isinstance(item, dict):
                item["category"] = await resolve_category(
                    str(item.get("category", "")), self.embedding_service
                )
    return normalized
```

- 209 行：`normalized = await self._normalize_and_resolve(raw_preference)`
- 351 行：`return raw, await self._normalize_and_resolve(raw)`
- `_normalize_preference` 本体与其它调用方（如有）不动——clamp 只挂分析主流程。

3. `soul/engine.py`：
- 152 行 `PreferenceAnalyzer(...)` 构造加 `embedding_service=embedding_service`。
- 214 行 `set_embedding_service` 内加 `self._preference_analyzer.embedding_service = embedding_service`（embedding 服务常在引擎之后构建，必须穿透）。

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_preference_analyzer.py tests/test_soul_engine.py -q
git add src/openbiliclaw/soul/preference_analyzer.py src/openbiliclaw/soul/engine.py \
        tests/test_preference_analyzer.py
git commit -m "feat: clamp off-vocab categories at preference ingestion"
```

---

### Task 8: PR2 文档同步

**Files:**
- Modify: `docs/modules/soul.md`、`docs/changelog.md`

**Steps:**

1. `docs/modules/soul.md`：PreferenceAnalyzer 行补「源头词表约束」描述——偏好分析 system prompt 注入 `CATEGORY_VOCAB`（静态常量、缓存安全）；代码侧 `resolve_category` 兜底（词表外 → embedding 最近邻 ≥0.55 → 「其他」），任何路径词表外分类无法写入 preference 层。
2. `docs/changelog.md` 当前版本块追加 bullet：「**画像分类源头约束（PR2）**：偏好分析 prompt 注入固定词表 + 入库 embedding 最近邻 clamp，词表外一级分类无法再入库」。
3. cli.md / 架构图不涉及，不动。

```bash
git add docs/modules/soul.md docs/changelog.md
git commit -m "docs: sync source-side vocab constraint docs (PR2)"
```

---

## PR3：同名异义防护 + judge 带 category + 全量清理（R4 + R5 + R6）

### Task 9: 规则合并收紧——同名同类免费合并，同名异类强制送审（R4）

**Files:**
- Modify: `src/openbiliclaw/soul/consolidator.py`（`_rule_merge_exact_names`、`_Cluster`、`run()`、`_has_unjudged_pair`、模块 docstring）
- Test: `tests/test_profile_consolidator.py`

**Step 1: 写失败测试**

```python
async def test_homonym_not_rule_merged_and_forced_into_cluster(tmp_path):
    # 苹果(科技, 0.9) + 苹果(美食, 0.5)，llm_service=None、无 embedding
    report = await consolidator.run(dry_run=False)
    names_cats = {(i["name"], i["category"]) for i in interests}
    assert names_cats == {("苹果", "科技"), ("苹果", "美食")}   # 没被盲合
    assert report.rule_merges == []
    assert report.clusters_sent >= 1                            # 强制嫌疑簇已构造
    assert any("llm" in e for e in report.errors)               # 送审但 LLM 不可用

async def test_same_name_same_category_merged_at_stage_zero(tmp_path):
    # 猫咪(萌宠, 0.8) + 猫咪(萌宠, 0.6)（迁移后典型精确重复）
    report = await consolidator.run(dry_run=False)
    assert len(interests) == 1 and interests[0]["weight"] == 0.8
    assert len(report.rule_merges) == 1

async def test_homonym_keep_both_pins_no_merge_with_qualified_keys(tmp_path):
    # StubLLM 对强制簇返回两条 keep（name 都是 "苹果"）
    first = await consolidator.run(dry_run=False)
    assert first.clusters_sent == 1 and llm.calls == 1
    second = await consolidator.run(dry_run=False)
    assert second.clusters_sent == 0 and llm.calls == 1         # 限定键 no-merge 生效

async def test_homonym_merge_collapses_both_entries(tmp_path):
    # StubLLM 返回 merge members=["苹果","苹果"] canonical="苹果"
    # → 单条存活，category 取高权重条目（科技），weight=max
```

**改写既有测试** `test_rule_layer_merges_same_name_across_categories`（92 行）——其断言的跨类盲合正是 R4 要消灭的行为：改名为 `test_same_name_cross_category_no_longer_rule_merged`，断言 人工智能(技术)/人工智能(科技) 两条均存活且进入强制簇（注：该 fixture 在 PR2 后属于「迁移会消灭的脏数据」，但 consolidator 必须对任意存量数据正确——保留 fixture 不变）。

**Step 2: 跑测确认失败 → Step 3: 实现**

1. `_Cluster` 加字段：

```python
@dataclass
class _Cluster:
    cluster_id: str
    scope: str
    members: list[str]
    member_categories: list[str] | None = None  # 与 members 平行；强制同名簇必填

    @property
    def member_keys(self) -> list[str]:
        """no-merge 记忆配对键。同名异类条目用 name::category 限定，其余用裸名。"""
        if self.member_categories is None:
            return list(self.members)
        return [f"{n}::{c}" for n, c in zip(self.members, self.member_categories, strict=True)]
```

2. `_rule_merge_exact_names` 重写（返回三元组）：
   - 分组键从 `_normalize_name(name)` 改为 `(_normalize_name(name), str(item.get("category", "")).strip())`——同名同类 → 原有合并逻辑（weight max、first/last_seen 极值）。
   - 合并后按 `_normalize_name(name)` 二次分组：仍有 ≥2 条（异分类）的名组收集为 `homonym_groups: list[list[dict[str, Any]]]`（存活条目的引用）。
   - 签名：`-> tuple[list[dict[str, Any]], list[str], list[list[dict[str, Any]]]]`。
   - rule_merges 文案改为 `f"同名同类合并: {name} ({category})"`。
3. `run()` 在 Stage 1 处构造强制簇（**基于全量 interests，不受 boundary slice 限制**——spec R4 明确不依赖聚类是否抓到）：

```python
forced_clusters = [
    _Cluster(
        cluster_id=f"H{i + 1}",
        scope="likes",
        members=[str(it["name"]) for it in grp],            # 存储原样名（apply 按精确名匹配）
        member_categories=[str(it.get("category", "")) for it in grp],
    )
    for i, grp in enumerate(homonym_groups)
]
clusters = [c for c in (*forced_clusters, *like_clusters, *dislike_clusters)
            if self._has_unjudged_pair(c, no_merge)]
```

4. `_has_unjudged_pair` 改用 `cluster.member_keys`（普通簇 keys == members，行为不变）。
5. no-merge 记录段改写：

```python
for cluster in judged_clusters:
    if cluster.member_categories is not None:
        # 强制同名簇：无 merge 落地（= 全 keep）才 pin 限定键对；合并了则条目已塌缩，无需 pin
        if not any(op.get("cluster_id") == cluster.cluster_id for op in valid_ops):
            keys = cluster.member_keys
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    no_merge.add(_pair_key(a, b))
        continue
    survivors = self._cluster_survivors(cluster, valid_ops)
    # …原有逻辑不动…
```

6. 校验与 apply **不需要改**——逐项核对并写入测试佐证：`_validate_cluster_ops` 的 coverage 用 `sorted(covered) != sorted(cluster.members)`，重复名时要求 keep×2 或 merge 把同名条目逐条列出，单条 keep → 覆盖不全 → 整簇拒绝（安全默认=全保留）；`_apply_like_merge` 按名收集 involved → 同名两条目一起塌缩为单条（base 取高权重条目，其 category 随之存活）——正是同名异义「确属同概念」时想要的语义。
7. 模块 docstring 第 1 条「identical names with different categories merge in code」与 `_rule_merge_exact_names` docstring 同步改写（同名同类免费合并；同名异类强制送审）。

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_profile_consolidator.py -q
git add src/openbiliclaw/soul/consolidator.py tests/test_profile_consolidator.py
git commit -m "feat: rule-merge same name+category only; force homonym clusters to LLM"
```

---

### Task 10: judge payload 带 category + system prompt 同名异义规则（R5）

**Files:**
- Modify: `src/openbiliclaw/soul/consolidator.py`（`_judge` payload）
- Modify: `src/openbiliclaw/llm/prompts.py`（`_PROFILE_CONSOLIDATION_SYSTEM_PROMPT`）
- Test: `tests/test_profile_consolidator.py`、`tests/test_llm_prompts.py`

**Step 1: 写失败测试**

```python
async def test_likes_judge_payload_carries_category(tmp_path):
    # 普通 embedding 簇（智能体开发/智能体开发与实现，category=科技）
    await consolidator.run(dry_run=False)
    payload = llm.last_user_input
    assert '"category": "科技"' in payload

async def test_forced_homonym_payload_distinguishes_by_category(tmp_path):
    # 强制簇：苹果(科技)/苹果(美食) → payload 两个成员对象 category 各异
    assert '"category": "科技"' in payload and '"category": "美食"' in payload

def test_consolidation_system_prompt_has_homonym_keep_rule() -> None:
    from openbiliclaw.llm.prompts import _PROFILE_CONSOLIDATION_SYSTEM_PROMPT as sys
    assert "同名异义" in sys and "category" in sys
```

dislikes payload 形状不变（既有测试回归兜底）。

**Step 2: 跑测确认失败 → Step 3: 实现**

1. `_judge` 的 likes payload（441–451 行）：构建 `category_by_name`（遍历 interests，按权重最高条目取 category），成员对象加 `category`：

```python
likes_payload = [
    {
        "cluster_id": c.cluster_id,
        "members": [
            {
                "name": name,
                "weight": round(weight_by_name.get(name, 0.0), 3),
                "category": (
                    c.member_categories[i] if c.member_categories is not None
                    else category_by_name.get(name, "")
                ),
            }
            for i, name in enumerate(c.members)
        ],
    }
    for c in clusters if c.scope == "likes"
]
```

（per-call 数据在 user 消息、`sort_keys=True` 序列化，缓存规范不破。）

（可选保真：强制同名簇的两个同名成员经 `weight_by_name` 裸名键会拿到同一权重——对裁决无实质影响。如要逐条精确，Task 9 构造强制簇时与 `member_categories` 同 pass 记录 `member_weights`，此处优先读取。）

2. `_PROFILE_CONSOLIDATION_SYSTEM_PROMPT` 增补（规则 7 与 8 之间插入新规则，原文其余字节不动）：

```
"7.5 likes 成员带 category（一级分类）。同名/近名但 category 不同且语义不同的条目\n"
"   是【同名异义】（如 苹果(科技) vs 苹果(美食)），必须分别 keep，严禁合并。\n"
"   只有确认它们是同一概念被误标了不同分类时才 merge；此时 members 必须把每个\n"
"   同名条目逐条重复列出（与簇内成员一一对应），keep 也要逐条各出一条。\n"
```

并把规则 9 的「members 及其权重元数据」改为「members 及其权重 / category 元数据」。system 仍是纯静态常量，`test_prompt_builder_system_messages_are_call_invariant` 继续通过。

**Step 4: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_profile_consolidator.py tests/test_llm_prompts.py -q
git add src/openbiliclaw/soul/consolidator.py src/openbiliclaw/llm/prompts.py \
        tests/test_profile_consolidator.py tests/test_llm_prompts.py
git commit -m "feat: include category in judge payload + homonym keep rule"
```

---

### Task 11: `--full` 全量清理 + 簇 ≤30/批分批送审（R6）

**Files:**
- Modify: `src/openbiliclaw/soul/consolidator.py`（`_judge` 分批、`run()` 对接）
- Modify: `src/openbiliclaw/cli.py`（`--full` 旗标 + 互斥）
- Test: `tests/test_profile_consolidator.py`

**Step 1: 写失败测试**

```python
async def test_full_boundary_surfaces_clusters_beyond_top128(tmp_path):
    # 150 条 interests：权重 #140/#141 是一对同义（StubEmbedding 同组）
    # likes_boundary=128（默认）→ clusters_sent == 0（回归基线）
    # likes_boundary=150 → dry-run clusters_sent == 1（边界外簇被抓到）

async def test_judge_batches_at_most_30_clusters_per_call(tmp_path):
    # 62 条 interests = 31 对同义（StubEmbedding 31 组）→ 31 个簇
    # 升级 StubLLM 为 payload 感知：解析 user_input 里的 likes_clusters，
    # 对收到的每个 cluster 返回 merge（members 原样、canonical=首名），并记录每次 user_input
    report = await consolidator.run(dry_run=False)
    assert llm.calls == 2
    for user_input in llm.user_inputs:
        sent = json.loads(extract_likes_clusters_block(user_input))
        assert len(sent) <= 30
    assert len(report.merges) == 31                       # 两批合并全部落地

async def test_multi_batch_apply_writes_single_run_record_and_full_revert(tmp_path):
    # 接上：apply 后 consolidation_runs 恰好 1 个文件；revert(run_id) 后 62 条全部恢复
    runs = list((tmp_path / "consolidation_runs").glob("*.json"))
    assert len(runs) == 1
    assert consolidator.revert(report.run_id)
    assert len(interests) == 62

async def test_single_batch_failure_isolated(tmp_path):
    # StubLLM 第 1 次调用抛异常、第 2 次正常 → 第 2 批的 merge 照常落地，
    # report.errors 记录 1 条批次错误；失败批的簇不进 rejected_clusters（下轮重试）

async def test_default_path_single_call_regression(tmp_path):
    # ≤30 簇 + 默认 boundary：llm.calls == 1（既有各测试同时兜底此回归）
```

**Step 2: 跑测确认失败 → Step 3: 实现 consolidator**

1. 模块常量 `_JUDGE_BATCH_SIZE = 30`。
2. `_judge` 重构为分批，签名改为：

```python
async def _judge(
    self, clusters: list[_Cluster]
) -> tuple[dict[str, list[dict[str, Any]]], set[str], list[str]]:
    """Returns (ops_by_cluster, judged_cluster_ids, errors). 每批 ≤30 簇独立调用，
    单批失败只记 error、不拖垮其余批次。"""
    ops_by_cluster: dict[str, list[dict[str, Any]]] = {}
    judged_ids: set[str] = set()
    errors: list[str] = []
    for start in range(0, len(clusters), _JUDGE_BATCH_SIZE):
        batch = clusters[start : start + _JUDGE_BATCH_SIZE]
        # …对 batch 构建 likes/dislikes payload（Task 10 形状）→ LLM 调用 → 解析…
        # try/except: 失败 errors.append(f"llm batch {start//_JUDGE_BATCH_SIZE + 1}: {exc}")
        #             + logger.warning，continue
        # 成功: judged_ids.update(c.cluster_id for c in batch)，ops 累入 ops_by_cluster
    return ops_by_cluster, judged_ids, errors
```

3. `run()` Stage 2 对接：

```python
if clusters and self._llm_service is not None:
    ops_by_cluster, judged_ids, judge_errors = await self._judge(clusters)
    report.errors.extend(judge_errors)
    for cluster in clusters:
        if cluster.cluster_id not in judged_ids:
            continue  # 失败批：跳过（不 reject、不 pin），下轮自动重试
        ops = ops_by_cluster.get(cluster.cluster_id, [])
        # …原有校验 / 收集逻辑不动…
```

（外层原 try/except 删除——错误已在批级捕获；保留对 `_judge` 自身意外异常的兜底 except 也可，二者择一，倾向删除以免双重路径。）

4. apply / run 记录 / no-merge / changelog 全部不动——多批 ops 天然汇入同一个 `valid_ops` → 单 run 记录、一次 revert 全量回滚。

**Step 4: 实现 CLI `--full`**

签名追加：

```python
full: bool = typer.Option(
    False,
    "--full",
    help="把 likes 整理边界从 top-128 开到全量标签库（嫌疑簇 ≤30/批分批送审，单 run 可整体回滚）。",
),
```

命令体：

```python
if full and migrate_categories:
    console.print("[bold red]  --full 与 --migrate-categories 不能同时使用（先迁移、后全量清理）。[/bold red]")
    raise typer.Exit(code=1)
...
likes_boundary = None
if full:
    _interests = memory.get_layer("preference").data.get("interests", [])
    likes_boundary = max(len([i for i in _interests if isinstance(i, dict)]), 128)
    console.print(f"  [cyan]--full：likes 边界开到全量（{likes_boundary} 条）。[/cyan]")

consolidator = ProfileConsolidator(
    memory=memory,
    llm_service=llm_service,
    embedding_service=embedding_service,
    **({"likes_boundary": likes_boundary} if likes_boundary is not None else {}),
)
```

默认（无 `--full`）构造参数与现状完全一致；12h 定时路径（`run_if_due`，pipeline 构造的 consolidator）不经过 CLI，不受任何影响。

**Step 5: 跑测 + 提交**

```bash
.venv/bin/python -m pytest tests/test_profile_consolidator.py -q
.venv/bin/python -m mypy src/openbiliclaw/soul/consolidator.py src/openbiliclaw/cli.py
git add src/openbiliclaw/soul/consolidator.py src/openbiliclaw/cli.py tests/test_profile_consolidator.py
git commit -m "feat: profile-consolidate --full with <=30-cluster judge batches"
```

---

### Task 12: PR3 文档同步 + 运维顺序说明

**Files:**
- Modify: `docs/modules/soul.md`、`docs/modules/cli.md`、`docs/changelog.md`

**Steps:**

1. `docs/modules/soul.md` ProfileConsolidator 行（41 行）更新：「规则层同名合并」→「规则层同名**同类**合并（零成本）；同名**异类**不合并、构造强制嫌疑簇送 LLM 裁决（同名异义防护，no-merge 记忆用 `name::category` 限定键）」；补「likes judge payload 带 category，system prompt 含同名异义 keep 规则」；「单次 batch LLM」→「嫌疑簇 ≤30/批分批 LLM 调用（单批失败隔离），全部批次汇入单 run 记录」；补 `--full` 边界全开说明。
2. `docs/modules/soul.md` 增加**运维说明**小节（spec R7 要求）：推荐执行顺序——
   `profile-consolidate --migrate-categories`（dry-run 审映射）→ `--migrate-categories --apply` → `profile-consolidate --full`（dry-run 审合并）→ `--full --apply`。迁移后同名同类精确重复由阶段 0 免费消化，显著减少送审簇数；之后稳态交给 12h 定时任务（top-128 + 脏检查 + no-merge 记忆，稳定画像零调用）。
3. `docs/modules/cli.md`：`profile-consolidate` 总表行与详情节补 `--full` 与互斥说明、推荐顺序示例。
4. `docs/changelog.md` 当前版本块追加 bullet：「**画像二级全量清理 + 同名异义防护（PR3）**：规则合并收紧为同名同类、同名异类强制送审；judge payload 带 category；`profile-consolidate --full` 边界全开、≤30 簇/批分批送审、单 run 整体回滚；默认与 12h 定时路径行为不变」。
5. 架构图不涉及（模块边界 / 数据流未变）。

```bash
git add docs/modules/soul.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: sync consolidation full-clean and homonym guard docs (PR3)"
```

---

### Task 13: 全量验证

**Files:** 无计划内源码改动（验证暴露真问题才改）。

**Step 1: lint + 类型 + 全量测试**

```bash
.venv/bin/python -m ruff format src/ tests/
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m pytest
```

**Step 2: 真实画像人工 smoke**（只读优先，逐步放行写入）

```bash
.venv/bin/python -m openbiliclaw.cli profile-consolidate --migrate-categories          # dry-run：83 分类完整映射
.venv/bin/python -m openbiliclaw.cli profile-consolidate --migrate-categories --apply  # 词表外=0、其他≤10%、树第一层⊆词表
.venv/bin/python -m openbiliclaw.cli profile                                           # 目检洋葱树第一层
.venv/bin/python -m openbiliclaw.cli profile-consolidate --full                        # dry-run：边界外嫌疑簇出现
.venv/bin/python -m openbiliclaw.cli profile-consolidate --full --apply                # 单 run 记录
# 如需回退演练: --revert <run_id> 后 diff data/memory/preference.json
```

**Step 3: 验证若改了文件则收尾提交**

```bash
git status --short
git commit -m "test: verify profile taxonomy consolidation end-to-end"
```

---

## 验收清单（spec 13 条 → 任务映射）

| # | Spec 验收条目 | 落点任务 / 测试 |
|---|----------------|-----------------|
| 1 | `CATEGORY_VOCAB` 存在，≤20 项且含「其他」 | Task 1（`test_vocab_bounded_with_fallback`） |
| 2 | 迁移 dry-run 打印覆盖全部现存分类的完整映射，不写任何数据 | Task 3（`test_dry_run_prints_full_mapping_and_writes_nothing`）+ Task 4/13 真实画像 smoke |
| 3 | 迁移 `--apply` 后词表外分类=0、「其他」≤10%、树第一层 ⊆ 词表 | Task 3（apply / tree 测试 + other_ratio 告警）+ Task 13 smoke（10% 是真实画像运维验收点） |
| 4 | 迁移 `--revert` 恢复 interests 至迁移前 | Task 3（`test_revert_restores_interests_byte_identical`） |
| 5 | 映射校验失败（漏映射/目标不在词表）整体放弃、零写入 | Task 3（`test_validation_gap_aborts_with_zero_writes`） |
| 6 | 分析器返回词表外分类时入库 category ∈ 词表（最近邻或「其他」） | Task 7（NN / 无 embedding / passthrough 三测） |
| 7 | 同名异类 fixture 不被规则合并、强制进送审簇；同名同类被阶段 0 合并 | Task 9（苹果 / 猫咪 fixtures） |
| 8 | likes judge payload 含 `category`；system prompt 含同名异义 keep 规则 | Task 10 |
| 9 | `--full`：边界全开、簇 ≤30/批多次调用、单 run 记录可整体 revert | Task 11（四个测试） |
| 10 | 无 `--full` 的默认行为与 12h 定时路径回归不变 | Task 11（默认构造参数不变 + 单调用回归测试；`run_if_due` 既有测试全量回归） |
| 11 | 新增/修改 builder 全部通过 prompt 不变性测试 | Task 2 / 6 / 10（`build_category_mapping_prompt`、`build_preference_analysis_prompt` 注册进 `_builder_test_inputs()`；consolidation prompt 仍静态） |
| 12 | `pytest` / `ruff check` / `mypy src/` 全绿 | Task 13 |
| 13 | soul.md / cli.md / changelog.md 同步（含迁移→`--full` 推荐顺序运维说明） | Task 5 / 8 / 12 |

## Execution Notes

- **taxonomy.py 保持零依赖。** `llm/prompts.py` 会 import 它；它绝不能顶层 import `openbiliclaw.llm`（cosine 走函数内惰性 import）。
- **clamp 时机是语义关键。** `resolve_category` 必须在 `merge_preferences` 的 `(name, category)` 键合并之前执行（两处调用点：`_analyze_events_single` 209 行、`_run_chunk_once` 351 行），变体分类才会塌缩到词表键上合并，而非并存。
- **`.format()` 会被 prompt 里的 JSON 花括号炸掉。** 偏好分析 system prompt 注入词表用占位符 `.replace()`，不要用 `.format()`/f-string。
- **同名异类的 no-merge 配对键必须带 category 限定**（`name::category`），否则 keep-both 的强制簇每轮都会重复送审（裸名 pair 会被 `dict.fromkeys` 去重成单元素、记不上对）。
- **`revert()` 不分支 kind。** 迁移记录复用 `before` 快照结构即可被既有 revert 恢复；迁移记录 `merges=[]`（no-merge pin 空转）、`overrides_before=None`（跳过 overrides 恢复），都是设计内行为。
- **迁移不重映射 overrides。** overrides 以兴趣名/避雷主题字符串匹配，分类改名不影响；domain 级编辑若引用旧分类名仅打 warning，不自动改写（分类名可能撞兴趣名，盲目重映射风险大于收益）。
- **`--full --apply` 后的 digest 备注：** `_input_digest` 按构造时的 boundary 计算，全量 apply 写入的 digest 与下一次定时 run（boundary=128）不同 → 定时任务会多跑一轮便宜 pass（no-merge 记忆兜底，LLM 调用为 0）。这不是默认路径行为变化，无需处理。
- **既有测试 `test_rule_layer_merges_same_name_across_categories` 必须改写**（Task 9）——它锁定的跨类盲合正是本 phase 修复的 bug 行为。
- **强制同名簇对 LLM 的输出契约**已写进 system prompt（members 同名条目逐条重复列出 / keep 逐条各出一条）；LLM 不照办 → coverage 校验拒绝整簇 → 安全默认 = 全保留。
- **成本符合 spec 约束：** 一次映射调用 + 词表 18 词向量缓存（每进程一次）+ 全量清理 ≤10 批裁决；稳态 12h 路径零增量。

Plan complete. Use `superpowers:executing-plans` to implement it task-by-task.
