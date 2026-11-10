# 初始化表单按需展开验收（2026-11-09）

范围：首启向导移除手动免费组件准备按钮；首启、PC 初始化卡片与插件的 Bangumi / GitHub 额外字段随平台勾选展开。

## 验证

- Chrome 打开独立 worktree 的 `/setup/`，使用 `test_web_guided_init_e2e.py` 的隔离 HTTP 夹具并补齐两个只读免费实例。免费模型页无手动准备按钮，点击保存后顺序调用 `POST /api/config/sponsored/prepare`、两次 `POST /api/config/probe-service`、`PUT /api/config`，进入下一步。
- 首启及 `/web/`：默认未选 Bangumi / GitHub 时用户名、令牌和帮助全部不可见；单独勾选各平台只展开其字段；取消再勾选保留输入。没有修改用户正在运行的后端或个人配置。
- 插件采用同样的分组与显示逻辑，完成 JavaScript 语法检查及相关现有回归；本轮未加载候选插件进行浏览器实测。
- `node --check extension/popup/popup.js`、`node --check src/openbiliclaw/web/desktop/assets/js/app.js` 通过。
- `PYTHONPATH=src pytest -q tests/test_web_guided_init.py tests/test_desktop_asset_bundle.py tests/test_web_llm_instance_routing.py tests/test_sponsored_settings_api.py tests/test_desktop_web_config_probe.py`：105 passed，仅既有 FastAPI 生命周期弃用警告。
- `git diff --check` 通过。

这是界面与请求编排验收；本轮不重复调用真实模型。安装与后端准备接口、配置字段、初始化请求协议保持不变，尚未发布安装包。

## 真实后端与真实模型请求补充

后续验收使用 `d081d5be`、两个空数据目录、真实 Rust 候选及验签策略、SiliconFlow HTTPS 服务，以及 GitHub 官方公开 Star 接口。无模型或来源 mock。沿用已存在的安装额度账本，没有清空额度；两组均固定单个 LLM，不跨模型兜底。限流、鉴权、工具校验均保持启用。

同首启页面的请求顺序验证：免费组件准备 → 所选 LLM 探测 → BGE-M3 探测 → 配置保存并等待后台应用 → 初始化前置检查 → `POST /api/init` → 完整画像及初轮推荐。每组从公开账号 `whiteguo233` 导入 40 条 Star，偏好分析各 4 批；用户名从 `source_options.github` 提交，Bangumi 省略。配置回读确认 GitHub 启用及用户名保存、Bangumi 未启用且未写入用户名、默认调用链保持所选模型。

| 检查 | Qwen3-8B | Qwen3.5-4B |
| --- | --- | --- |
| 自动准备、真实 LLM / BGE 探测 | 通过 | 通过 |
| 偏好分析及画像持久化 | 4/4 批通过 | 4/4 批通过 |
| 从启动初始化至推荐完成 | 281.84 秒 | 201.36 秒 |
| 首轮推荐 | 4 条 | 2 条 |
| 两轮流式聊天及会话持久化 | 通过 | 通过 |
| 三路并发聊天 | 3/3，均有实时 delta | 3/3，均有实时 delta |
| 推荐反馈及重复请求只入库一次 | 通过 | 通过 |
| 真实 `get_profile` 工具读取、总结 | 首次通过 | 首次失败，后两次通过 |

两组阶段 1 均为 `warning / github_partial`，原因明确为本次人为设定的 40 条采集上限 `item_cap`。阶段 2、3、4 均为 `ok`，画像与推荐可用；不可把有限采样描述为完整 Star 历史采集，也不把 `strict_init_success=false` 改写为无警告成功。全程没有初始化超时或上下文超限。

4B 的首次画像工具请求持久状态确实为 `failed`，日志链为 `InvalidToolActionError: invalid tagged tool name or arguments` → `PROTOCOL_ERROR`；没有成功工具事件。此错误指工具调用契约不合法，不是超时。两次相同只读请求复测成功且有真实 `get_profile` 工具结果，耗时分别 3.52 / 39.69 秒。保留首次失败，不以重试覆盖，也未放宽工具名/参数验证。样本仅三次，不据此推算整体故障率。该偶发问题仍未解决，本轮不能宣称所有能力稳定通过。

插件初始化/来源状态单元回归 86/86 通过。真实操作所用两个后端在测试后已停止，用户原应用未改动。

### 浏览器验收待续

本轮两次调用浏览器工具均提示 Work 设置禁用了本机浏览器控制，其中一次在用户表示已开启后重试。用户随后要求“先保留页面，稍后再测”。因此上面的真实请求来自 HTTP 验收脚本，不能算真实页面点击完成；上一节的浏览器截图仍仅是夹具验证。

已另建两个空目录真实浏览器验收环境（各设 GitHub 上限 20 条，尚未触发初始化），保留服务与 `/setup/` 访问地址供下轮使用；实际浏览器标签未成功打开。位置、端口、PID 与恢复步骤仅保存在本机私有验收目录。当前没有发布新版安装包。

脱敏证据：[summary.json](assets/setup-real-e2e-20261109/summary.json)。原始服务日志、数据库和配置留在本机私有目录，不入库。验收脚本曾将配置应用状态误读为 `status`，核对实际 `state=applied` 后更正并重跑；该脚本问题不是产品应用超时。插件测试第一次从仓库根目录执行导致相对路径错误，从 `extension/` 重跑后 86 项通过。

## 2026-11-09 合入验证与清理

按用户要求将本次初始化界面与此前免费模型超时、断连和本地限流修复一并收尾，包含 Rust 私有仓的请求预算扩展。合并前对最终代码运行：

- Core Sponsored、初始化预算、偏好/Soul、配置热应用和 Web 相关 `pytest -q -m 'not integration'`：707 passed、1 skipped、5 deselected；保留现有 FastAPI 生命周期弃用警告。未重跑全库约 1.4 万项测试。
- `ruff check src/ tests/` 通过；`mypy src/` 413 个源文件通过；PC / 插件三个改动 JS 的 `node --check` 通过。
- Rust `cargo test --locked`：81 passed、0 failed、3 ignored；本轮未重复 190 秒手动延迟项，既有验证见 Rust 请求预算记录。
- 新增公共差异未检出疑似真实 Key 或私钥；仅合入脱敏统计与截图。

此次“清理收尾”替代前述保留运行服务的临时状态：四个代理创建的隔离后端均已停止，配置、验收证据与恢复脚本仍保留在本机私有目录；不会清理用户 `outputs/`、`promo/` 或其它任务 worktree。此前浏览器权限阻断的真实页面点击仍待补测，4B 偶发工具协议失败仍未解决。

此次仅合并源代码及文档，不发布安装包或 Rust SDK。后续发行需要同时构建并签名新版 Rust；既有发布包不会因 main 更新而自动获得新预算能力。
