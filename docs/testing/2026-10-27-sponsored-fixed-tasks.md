# Sponsored 固定任务验收（2026-10-27）

公共分支 `feat/sponsored-fixed-tasks` 基于旧 Sponsored 接入分支 `44b1937c9a945cac4ee49cdbd4d416b272b9c411`，没有合并到 main，也没有发布。配套私有 Runtime 分支 `feat/security-task-contracts` 的提交为 `b18616259eb72613efbdb49c34532e5e0dcbf0f0`。两端需一起升级到 IPC v2。

## 本次范围

只开放已有业务接入的 `soul.consolidation` / contract2。请求仅传有界簇数据，拒绝额外字段与任意 messages/模型/URL/token 参数。新类型必须增加两端明确结构、renderer 和测试；不能通过修改 Policy 把它扩展为通用 Chat。旧 BYOK builder 仍为业务入口，公共轻量 Prompt 模块与私有 golden vectors 检查原业务消息字节。

源码和 Docker 默认仍关闭 Sponsored；没有读取真实凭据、调用真实模型或触发发行流程。当前 worktree 与原主工作区改动分离。

## 通过的检查

```sh
PYTHONPATH=src python -m pytest tests/test_sponsored_*.py \
  tests/test_llm_sponsored_routing.py tests/test_generate_sponsored_policy.py \
  tests/test_llm_prompts.py -q --tb=short
PYTHONPATH=src python -m mypy src/
python -m ruff check src/ tests/
git diff --check
```

- Scoped pytest：181 passed、1 skipped。跳过的是需要显式提供真实 Rust development 命令的桥接项。
- 使用配套 Rust development 二进制、假 Policy、`--mock --allow-unsigned` 与一次性状态目录补跑 `tests/test_sponsored_fixed_tasks.py`：7 passed，包含上述桥接项；没有触及真实模型或系统钥匙串。
- MyPy：290 source files 无问题；Ruff 与 diff whitespace 检查通过。
- 私有 Rust：默认 52 passed / 1 ignored；development 51 passed / 1 ignored；fmt、Clippy 通过。
- 私有发布工具：2 项模拟系统工具测试通过；假材料正式构建、策略解封装、产物扫描、manifest 签名和篡改拒绝检查通过。

## 全量测试的限制

已尝试普通全量 pytest；改为四 worker 全量后在约 99% 长时间无进展，主动停止，没有完整通过结果。该轮记录的 16 个失败逐项用当前代码重跑：15 passed，1 failed。

剩余失败为 `tests/test_desktop_web_list_stability_e2e.py::test_pool_refill_event_keeps_loaded_cards_and_scroll_position`：库存数字断言期望 70，实际 40。在未修改的原 Sponsored 分支 `44b1937c9a945cac4ee49cdbd4d416b272b9c411` 上使用同一解释器与测试命令，也得到同样失败。本次没有修改网页库存逻辑，亦不把这次全量尝试描述为通过。

其他失败重跑已通过。Sponsored 握手超时另有定位和修复：mock 原先启动加载全部模型 SDK，繁忙时超过现有时限；现在保持公开 import API 并按需加载，协议与 mock 使用纯契约/Prompt，不增加生产握手时限。新子进程测试验证不会导入 OpenAI/Anthropic/Google GenAI SDK。

## 发行前仍需验证

真实 OS 签名/公证、四平台 CI、安装器与更新器消费 bundle、供应商侧模型权限/硬额度/撤销均未在本机验收。本地混淆、清零和配额提高提取与滥用成本，无法保证用户控制的设备永远取不出共享 Bearer Key。详见[协议与范围](../plans/2026-10-27-sponsored-fixed-tasks.md)。
