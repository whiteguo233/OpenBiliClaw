# 全项目 Sponsored SDK 验收（2026-10-27）

本次修正的是“SDK必须支持项目全部LLM任务”的范围。公共分支 `feat/sponsored-all-tasks` 基于 `fb2bc7d95ef85a9ea7cec4270e06c1e803fff5a4`；配套私有 Runtime 提交 `839843b66a56c6abf9a452762f28eadd439417b8`。两端成套升级，没有合并main、发布或启用线上服务。

## 覆盖与结果

[扫描清单](sponsored-task-inventory.json) 记录98个项目LLM/向量调用点，包含画像、发现、推荐、对话/工具、视觉、向量、模拟/评测和优化器旁路。扫描识别已知调用/别名/getattr形态，仍需代码审阅；不能把静态清单当作任意动态代码的来源认证。

| 检查 | 结果 |
| --- | --- |
| 最终SDK、路由、Prompt、向量、并发、初始化回归 | 564 passed / 1 skipped |
| 真实Rust development子进程的独立桥接 | 41 passed，覆盖新六类和旧合并；替代上述需显式Rust命令的skip |
| MyPy | 295源文件无问题 |
| Ruff、git diff --check | 通过 |
| 隔离样例配置 config-show | 退出0；未使用本机真实配置 |
| 私有Rust默认 / development | 61 passed / 60 passed；各1个系统Keychain测试ignored |
| 私有发布工具 | 4项通过；未批准模型、缺失有限预算、公证非Accepted均阻断 |
| 假材料正式构建演练 | production构建、验签、解封装、材料扫描、manifest验证、篡改拒绝、开发开关隔离通过 |
| 示例策略/SDK fixture | Draft2020-12 schema校验通过 |

独立Rust桥接使用冻结的 development 可执行副本、假策略、mock upstream和临时状态目录；先前并行构建覆盖过测试二进制路径，最终串行固定副本复验通过。没有用真实赞助Key或模型进行验收。

## 全量测试

```sh
PYTHONPATH=/tmp/obc-sponsored-test-tools:src python -m pytest \
  -n 4 --dist loadfile --timeout=90 --timeout-method=signal -q --tb=short
```

9310 passed、66 skipped、2 failed，13分01秒完成。xdist/timeout仅安装到临时测试工具目录，未修改项目依赖；全量运行后对最终SDK改动补跑上表564项和41项桥接。

两项失败在当前代码和未修改的 `fb2bc7d9` 基线上都独立复现，输出一致：

- `test_saved_sync_identity_pipeline.py::test_legacy_duplicate_keeps_selected_temporal_evidence_publication_as_one_group`：pool_status期望fresh，实际temporal_review_hold。
- `test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`：库存数字期望70，实际40。

没有修改这两处业务逻辑，也没有宣称全量通过。

## 可复验命令

```sh
PYTHONPATH=src python -m pytest tests/test_sponsored_*.py \
  tests/test_llm_sponsored_routing.py tests/test_generate_sponsored_policy.py \
  tests/test_llm_service.py tests/test_llm_registry.py tests/test_llm_prompts.py \
  tests/test_embedding_service.py tests/test_speculation_evaluator.py \
  tests/test_discovery_evaluator.py tests/test_llm_concurrency.py tests/test_init_prereqs.py \
  -q --tb=short
PYTHONPATH=src python -m mypy src/
python -m ruff check src/ tests/ scripts/audit_sponsored_coverage.py
python scripts/audit_sponsored_coverage.py --output docs/testing/sponsored-task-inventory.json
```

设置 `OBC_TEST_RUST_RUNTIME` 为配套 development 二进制的 mock/unsigned命令，再运行 `tests/test_sponsored_all_tasks.py tests/test_sponsored_fixed_tasks.py` 可复验41项桥接组。正式生产二进制不支持这些开发开关。

## 未验证的发行事项

接口接线完整不代表所有上游能力免费：新增视觉/多模态向量模型的实际赠送额度、可用性、质量和向量阈值重校准尚未验证。正式发布要求获批模型清单和有限预算，不自动启用或付费。OS签名、公证、四平台CI及安装/更新整合仍待发行环境验收。

业务Prompt和原解析流程由Core保留；固定六类SDK操作不能证明调用者一定是本项目。Key仍只在私有Runtime中解封装，编译期固定出口、签名模型/预算和原有密钥加固继续生效。详见[完整设计](../plans/2026-10-27-sponsored-all-tasks.md)。
