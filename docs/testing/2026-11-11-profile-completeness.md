# 免费模型初始化画像空区块修复（2026-11-11）

分支：`fix/initial-profile-completeness`，基线 `49acfdf7`。本文保留合入前的定位与验证；2026-11-12 合入 main，尚未发布，不自动修改用户已有画像。

## 复现与根因

确定性复现使用生产 `ProfileBuilder` / `SoulEngine.build_initial_profile()`，给两个免费模型返回形状提供有合法来源索引的 `information_preferences`。修复前两模型的初始化保存测试均因 `core_traits=[]` 失败，八种有效信息偏好和无依据仍被标记成功的回归共 11 项失败。

1. 免费被动画像 prompt 限制模型只选择信息偏好，旧后处理又直接清空核心特质、需求、价值和驱动力，当前阶段被替换成未知。
2. 固定实例适配器已把 Provider 名称从 `sponsored` 改成 `siliconflow_free`，旧后处理只检查前者。回归增加真实 `LLMService → LLMRegistry → SponsoredChatAdapter` 链路，防止仅使用旧名替身造成假通过。
3. 另有独立失败回归：觉察和洞察已在长期层存在并传入画像生成，但返回 `SoulProfile` 没有携带它们，摘要 API 读取的画像快照仍为空。
4. 桌面 MBTI 收到 `{type:"", dimensions:{}}` 时渲染横杠；手机端直接省略整个未知态。

## 修复合同

- 模型仍负责从当前内容来源选择信息偏好。后端校验闭集 `kind` 和本次请求索引/完整来源标题，再用固定、保守语义生成内容侧初步画像；核心特质、需求、价值、驱动力均保存「内容侧初步推测，待确认」，不提升为确定人格事实。
- 有来源但缺少合法信息偏好时，最多紧凑重试一次；免费模式保留最多 12 个来源并重新编号。持续失败在提交屏障前报错，不覆盖旧画像。普通模型的原有无标题重试仍保留，避免把格式/拒绝恢复改成重复提交相同原文。
- 自述和内容选择可同时使用，直接自述优先；对话标题不充当内容来源。免费自述/被动 prompt 与普通 prompt 都使用静态模块级 system 常量，动态 JSON 采用确定性序列化。
- 已有有效觉察/洞察进入画像快照，窗口沿用认知周期的 8/6 条；来源引用、近似归属、置信度和未验证状态保留，用户否定的洞察不重新显示为活跃。
- 桌面、手机、插件、CLI 与 Markdown 明示未知 MBTI 的原因。无自述的现实身份、无负面信号的避雷偏好、无时间证据的使用场景，以及尚未发生的主动试探不强行生成。初始化并不意味着这些未知事实自动变得可知。
- 已有空画像需要在更新后重新初始化或等待正常画像重建，本次不自动重写用户存量数据或覆盖人工编辑。

## 自动验证

- 定向后端回归：695 passed，覆盖画像、来源边界、实例适配、Soul Engine、认知周期、画像视图、CLI、手机视图模型与桌面画像交互；包含普通重试隔离回归，画像构建和来源相关测试另有 62 passed。
- 全量后端：14,086 passed、75 skipped、19,630 warnings，1048.57 秒（17 分 28 秒），退出码 0。全量执行期间最后收紧了普通模型重试保留来源的条件并增加回归；最终代码由末次 695 项定向回归覆盖，未重复整套。
- `ruff check src/ tests/` 与 `mypy src/`（414 个源文件）通过。
- 插件 `npm run typecheck`、`npm run build` 和 `npm run verify:assets`（22 个 manifest/WAR 入口资产）通过；`npm test` 为 1904 passed、2 failed。两项失败均来自未修改的 `popup-helpers.test.ts` 中空库存提示文案期望，与本次改动无关；在主工作区 main 单独运行 `node --test --experimental-strip-types --test-name-pattern='getPopupState' tests/popup-helpers.test.ts` 同样 2 passed、2 failed。新增的未知 MBTI 实际渲染回归 4 项全通过。以上为当时的合入前基线；2026-11-12 收尾同步这两条旧测试组的三个文案预期后，插件完整回归 **1907 passed / 0 failed**，见[合入记录](2026-11-12-profile-live-e2e.md#合入收尾2026-11-12)。

为了缩短全量执行时间，仅向 `/tmp/obc-profile-test-tools` 安装 pytest-xdist / execnet，并用 `PYTHONPATH=/tmp/obc-profile-test-tools:src python -m pytest -n 4 --dist loadfile -q --tb=short` 执行；没有修改项目依赖。

## 页面与导出

使用隔离目录 `/tmp/obc-profile-ui-demo-20261111`（已清理），合成的「原理解释 + 分步教程」内容选择、觉察和待确认洞察，经真实 `create_app()`、画像保存和 `/api/profile-summary` 展示；模型返回采用确定性替身。本项验证持久化和页面消费，不冒充真实模型语义验收。

- 桌面 `/web/`：核心特质、需求、价值偏好、驱动力、当前内容关注、认知风格均有内容；未知 MBTI 显示完整说明。
- 手机 `/m/#/profile`：390 × 844 视口下同样显示这些字段，并显示活跃洞察与近期觉察。
- `openbiliclaw profile`：从隔离数据目录读取同一画像，推测标记与 MBTI 说明正常；Markdown 双写回归覆盖相同语义。
- 归档截图：[桌面](assets/profile-completeness-20261111/desktop.png)、[手机](assets/profile-completeness-20261111/mobile.png)；[CLI 输出](assets/profile-completeness-20261111/cli.txt)。截图为合成夹具，不含真实历史。

Ego Browser 首次设置尚未完成，因此使用 Playwright CLI 检查页面。插件实际安装/商店权限流程未重新验收；插件提供 4 项 DOM 渲染回归和类型检查，手动检查步骤见 [Extension](../modules/extension.md#初始化画像完整性2026-11-11待发布)。

## 两模型真实请求限制

本节保留首次本机阻断。2026-11-12 已在独立 Linux/macOS 环境完成两个模型的真实四阶段初始化与页面/CLI/重启验收，四组均通过，详见[后续真实验收](2026-11-12-profile-live-e2e.md)。

分别用 `scripts/sponsored_init_acceptance.py --events 12`、独立全新目录、正式验签 SDK 和只含被测模型的固定实例链尝试 Qwen3-8B / Qwen3.5-4B。两轮都在 SDK IPC 握手阶段失败，尚未产生上游请求；8B 的独立启动诊断明确为 `LEDGER_TAMPERED: keychain store status -25293`。不能把确定性回归描述为两个真实模型均已完成初始化。

请求元数据： [8B 阻断记录](assets/profile-completeness-20261111/8b-runtime-blocked.json)、[4B 阻断记录](assets/profile-completeness-20261111/4b-runtime-blocked.json)。没有读取用户 API Key、Cookie 或真实历史，没有修改钥匙串访问控制、绕过 SDK 安全策略或降低验签要求。测试进程、隔离 SDK/数据目录和临时浏览器已清理。脚本已增加六个画像区块的非空断言、推测标记和未知身份/MBTI 校验，恢复正常 SDK 环境后可以直接重跑。

## 文档范围

更新 Soul、初始化、LLM、CLI、桌面 Web、手机 Web、插件模块文档及变更日志；四份架构文档/README 图同步标注初始画像提交屏障与认知快照。既有模块连接、公开 HTTP/CLI 接口、配置、依赖和安装流程不变；没有新增架构块，不涉及安装器或发版。
