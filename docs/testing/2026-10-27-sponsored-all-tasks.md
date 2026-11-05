# 全项目 Sponsored SDK 验收（2026-10-27）

本次按“SDK支持项目全部LLM任务”修正范围。公共分支 `feat/sponsored-all-tasks` 已将当前 main `23de203a72a9b59d2c555d66b9e70ab75583aa2c` 合入开发分支；配套私有 Runtime 源码提交 `839843b66a56c6abf9a452762f28eadd439417b8`。两端需成套升级。没有合回主工作区 main、发布或启用线上服务；原主工作区的 outputs/promo 未跟踪目录保持原状。

## 当前主线覆盖

[扫描清单](sponsored-task-inventory.json) 记录108个已知LLM/向量调用点，逐项涵盖画像初始化、偏好、合并、洞察、推测、发现/推荐、推荐导演Agent、鱼实验、普通/工具对话、视觉、文本/文档/图片向量、模拟/评测和优化器旁路。业务入口进入六类SDK，旧合并contract2兼容保留。已指定目标的配置探针（含默认链/embedding）绕过赞助替代；Jev是独立非生成式DecisionProvider，保留原路径。

扫描识别已知调用、别名和getattr形态，配套静态回归防止未审阅入口加入；不能把静态清单或caller字段当作客户端身份认证。

主线原生工具接口由现有prompt模拟与解析适配，保留schema、多轮工具调用和结果，工具只在Core执行。流式接口当前为缓冲返回（完整响应后一次正文delta + terminal），未实现上游逐token SSE；输出后失败不再重发用户模型。

## 最终检查

| 检查 | 结果 |
| --- | --- |
| 最终SDK/工具/流式/显式探针回归 | 285 passed / 1 skipped |
| 配置及保存回读回归（legacy + instance routing） | 377 passed |
| 扩展至推荐导演的定向回归 | 1254 passed / 1 skipped / 2前端可见ACK断言失败；波动见下 |
| 真实Rust development子进程桥接 | 49 passed；新六类与旧合并，独立覆盖上述需显式Rust命令的skip |
| MyPy | 378源文件无问题 |
| Ruff check / format --check | 通过；915个源码/测试文件格式通过 |
| 隔离样例配置 config-show | 退出0；未使用本机真实配置 |
| 私有Rust默认 / development | 61 passed / 60 passed；各1个系统Keychain测试ignored |
| 私有发布工具 | 4项通过；未批准模型、缺失有限预算、公证非Accepted阻断 |
| 假材料正式构建演练 | production构建、验签、解封装、材料扫描、manifest验证、篡改拒绝、开发开关隔离通过 |
| 示例策略 / 共享SDK fixture | Draft2020-12 schema通过；公共/私有两份fixture一致 |

最终SDK/探针组包含纯赞助registry、无自有Key实例配置、配置完整持久化、探针不改变活动设置、原生工具多轮结果、共享子进程/向量缓存和流式回退边界。真实Rust桥接使用冻结的development可执行副本、假策略、mock upstream及临时账本。后续新增探针工厂回归在285项组中验证；没有用真实模型验证质量。

假材料发布演练的Runtime快照树为 `f2aebe183352283ea670159fb60711384b8f19e4`，最终Core快照树为 `dc34546a5474669a93911dbd0c6d3469e355bd49`；私有机器记录提交 `a48bb16`。只记录离线构建/材料验证，不代表OS签名或模型权限验收。

## 全量回归及既存边界

```sh
PYTHONPATH=/tmp/obc-sponsored-test-tools:src python -m pytest \
  -n 4 --dist loadfile --timeout=90 --timeout-method=signal -q --tb=short
```

12262 passed、70 skipped、7 failed，24分26秒完成。xdist/timeout只安装到临时工具目录，未修改项目依赖。全量启动后完成的配置持久化/探针调整，另由最终377项和285项组验证；不把早期快照当成全部最终检查。

7项失败：

| 测试 | 本轮失败 |
| --- | --- |
| test_extension_native_save_broker.py::test_hanging_wake_is_bounded_by_dispatch_deadline | wait_for超时 |
| test_desktop_web_settings_save_e2e.py::test_settings_save_unlocks_before_runtime_apply_finishes | 设置按钮30秒内未稳定可点击 |
| test_desktop_web_issue_98_e2e.py::test_recommendations_and_runtime_recover_without_leaving_init_gate | 库存期望30、实际27 |
| test_desktop_web_list_stability_e2e.py::test_disabled_autoload_does_not_consume_pool_during_background_hydration | 卡片期望24、实际0 |
| test_director_frontend_e2e.py::test_initial_generation_and_exact_batch_feedback_in_real_browser[m] | presented为空 |
| test_desktop_web_platform_recommendations_e2e.py::test_enabled_zero_stock_platform_survives_config_snapshot_retry | 平台Tab未及时出现 |
| test_jev_runtime_e2e.py::test_browser_save_hot_reload_and_ingest_use_saved_jev_connection | select期望jev、实际llm |

干净当前main `23de203a` 对同7项复验：5 passed / 2 failed（68.46s）；本分支同组复验：4 passed / 3 failed（66.68s）。共同稳定失败是 issue_98 库存30/27与 zero-stock平台Tab，两端错误一致。其余原全量失败项中的broker deadline、设置按钮、autoload、导演mobile在本分支重跑通过；Jev在并行组里仍失败，单独复验本分支1 passed（7.98s）、干净main1 passed（8.90s）。因此保留2项主线复现问题和5项时序/环境波动，不将重跑通过写成产品修复。

本分支没有更改网页静态资源、上述测试或extension broker实现。两个稳定失败的业务/UI修复不属于本次SDK接口范围。最终SDK/配置/桥接检查通过；全量结果仍按7项失败原样记录。

推荐导演前端测试 `test_initial_generation_and_exact_batch_feedback_in_real_browser[web/m]` 在扩展定向组里两项失败，断言均为 `presented` 空列表。单独重跑本分支1通过/1失败（mobile），干净当前main基线也是1通过/1失败（desktop）；参数结果有波动。这些测试直接加载静态网页并mock API，相关测试和静态资源与main一致，不能把这组结果写成全通过，也不把一次通过当作修复。

## 可复验命令

```sh
PYTHONPATH=src python -m pytest tests/test_sponsored_*.py \
  tests/test_llm_sponsored_routing.py tests/test_llm_service.py \
  tests/test_llm_native_tools.py tests/test_token_streaming.py \
  tests/test_api_config_probe.py tests/test_api_llm_instance_routing.py -q --tb=short
PYTHONPATH=src python -m pytest tests/test_sponsored_config.py tests/test_config.py -q --tb=short
PYTHONPATH=src python -m mypy src/
python -m ruff check src/ tests/ scripts/audit_sponsored_coverage.py
python -m ruff format --check src/ tests/
python scripts/audit_sponsored_coverage.py --output docs/testing/sponsored-task-inventory.json
```

设置 `OBC_TEST_RUST_RUNTIME` 为配套development二进制的mock/unsigned命令，再运行 `tests/test_sponsored_all_tasks.py tests/test_sponsored_fixed_tasks.py` 可复验桥接组。正式生产二进制不支持这些开发开关。

## 未验证的发行事项

接口接线不代表所有上游能力免费：新增视觉/多模态向量模型的赠送额度、可用性、质量和向量阈值重校准尚未验证。正式发布要求获批模型清单和有限预算；没有读取真实赞助Key或主动运行真实模型验收。OS签名、公证、四平台CI及安装/更新整合仍待发行环境验收。

业务Prompt和原解析流程留在Core，固定SDK操作不能证明调用者来自本项目；本机owner仍可能逆向。Key只在私有Runtime中解封装，固定出口、签名模型/预算、受控密钥副本清零和布局混淆继续生效。详见[完整设计](../plans/2026-10-27-sponsored-all-tasks.md)。
