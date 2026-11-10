# 认知 prompt 归档台账剔除真实链路验收（2026-11-10，issue #342）

原因：`build_cognition_profile_view_v1` 的 compact-v1 投影只按 `state="archived"` 过滤
`interests` 等正向兴趣集合，顶层 `archived_interests` 走通用透传，被整块序列化进
`<preference_summary>`。该台账由 consolidation 超水位裁剪和 12h `scan_lifecycle` 持续
追加、没有修剪路径，prompt 成本因此随账号年龄增长；报告实例（4,752 条）在
`soul.awareness_confusions` 上单次约 710k input tokens。

修复：`preference_prompt_payload()` 在剔除 `last_decay_at` 之外再剔除
`archived_interests`，`build_cognition_profile_view_v1` 同样剔除；持久化存储不变，
偏好分析器仍从库内做归档别名去重与证据驱动复燃。无台账输入的 prompt 逐字节不变。

本文件记录真实 Provider 的真实请求复测，不冒充无网络单测。raw 报告含真实画像片段，
只留在本机临时目录，仓库内仅提交脱敏汇总
[summary.json](assets/issue-342-cognition-archive/summary.json)。

## 环境与方法

- 代码：修复分支 `fix/issue-342-cognition-archive-projection`（worktree）对比 `main`
  `becb00fd`，两者共用同一份配置与数据副本，仅 `PYTHONPATH` 指向不同 `src`。
- 数据：本机真实库 `data/openbiliclaw.db` 的 APFS 克隆（228.9 MB / 109,390 条事件）+
  真实画像层（108 个 active 兴趣）复制到 `/tmp`。真实画像里**没有**已归档记录
  （0 条），因此按真实兴趣记录的字段结构生成 4,752 条合成台账行，**只注入内存中的
  preference layer**，磁盘上的画像与存储不改；每行约 460 B，序列化后 2,185,917 B，
  与报告里 ~1.6 MB / 4,752 条同量级。第 0 行名字带
  `ZZ-ARCHIVE-LEDGER-SENTINEL-0000`，用于证明它是否进入真实请求。
- 链路：`MemoryManager` → `LLMService`（真实 `openai_compatible` 实例）→
  `AwarenessAnalyzer(confusions_prompt_view="compact-v1")` →
  `CognitionCycle.run_if_due()`，只消费最新 6 条真实事件（批大小 6，单次调用），
  insight 与 cleanup 强制不 due。驱动脚本
  [`scripts/cognition_prompt_payload_e2e.py`](../../scripts/cognition_prompt_payload_e2e.py)。
- Provider：`token.sensenova.cn`；awareness 落到 `deepseek-v4-flash`，insight 落到
  `glm-5.2`；token 数取自 Provider 返回的 usage，落库到 `llm_usage`（`estimated_cost_cny`
  为仓内定价估算，不是账单）。

## 结果

同一次 `soul.awareness_confusions` 调用（4,752 条台账、6 条真实事件）：

| 构建 | 台账行数 | 出站请求 | Provider input tokens | 耗时 | 估算成本 | 请求内含归档台账 |
| --- | --- | --- | --- | --- | --- | --- |
| `main`（未修复） | 4,752 | 2,338,249 B | 852,649 | 27.42s | ¥0.854533 | 是（含 sentinel） |
| 修复分支 | 4,752 | 81,022 B | 25,787 | 13.56s | ¥0.029726 | 否 |
| 修复分支 | 0 | 81,022 B | 请求被限流拒绝（见下） | — | — | 否 |

- 修复后出站请求 `sha256 = bbd923d6…6c5e76`，与同一份数据、0 条台账时**逐字节相同**：
  4,752 条台账对真实请求贡献 0 B。
- 相对 `main`：input tokens 少 33.07 倍（852,649 → 25,787），请求体积少 28.86 倍
  （2.15 MB → 79 KB），单次估算成本少 28.75 倍。按报告里 145 次 / 16h 的频率外推，
  约 1.199 亿 input tokens / 16h 不再发生。
- `main` 的 insight prompt 从修复前的 2,802,079 B 降到 169,444 / 175,050 / 176,595 B
  （三次分片），前两次真实成功（input 48,359 / 49,357 tokens）。
- Provider 接受了 852k tokens 的单次请求，说明报告里 710k 的观测在当前长上下文模型上
  确实可复现；小于该上下文的模型会直接拒绝整个 cognition 调用。

## 边界

- 0 台账对照组由同一修复构建、同一份克隆数据发起，请求已在本地组装并完成哈希后才
  被 Provider 冷却拒绝（上一轮 852k 请求触发限流）；字节等价结论不受影响，但**该组
  没有一次成功的 0 台账响应**。修复组 4,752 台账的成功响应已证明同一 payload 可被
  Provider 接受（两份 payload 的 sha256 相同）。
- 每臂只跑 1 次 awareness 调用，不构成多 provider / 多模型矩阵；insight 只记录请求
  体积与前两次成功，第三次命中限流，未做质量评估。
- 台账是合成的：真实画像当前没有归档记录，所以无法用真实台账跑；字段结构、单行体积
  与规模对齐报告，但不是该用户的真实历史。
- 本轮没有重跑全量 pytest；相关回归见 PR（`tests/test_llm_*.py`、`tests/test_profile_*.py`、
  `tests/test_soul_*.py`、`tests/test_cognition_*.py` → 1143 passed）。

## 复现

```bash
D=/tmp/obc-342-e2e/fixed
mkdir -p "$D/data/memory"
sed 's#^data_dir = .*#data_dir = "'"$D"'/data"#' config.toml > "$D/config.toml"
cp -c data/openbiliclaw.db "$D/data/openbiliclaw.db"
for f in preference soul soul_profile profile_overrides awareness insight feedback_state; do
  cp -c "data/memory/$f.json" "$D/data/memory/$f.json" 2>/dev/null || true
done
PYTHONPATH=<checkout>/src python scripts/cognition_prompt_payload_e2e.py \
  --config "$D/config.toml" --arm fixed --ledger-rows 4752 --events 6 --out "$D/report.json"
```

对 `main` 复跑同一命令时把 `PYTHONPATH` 指向 `main` 的 `src` 即可（代码版本由
`sys.path` 决定，脚本本身不变）。
