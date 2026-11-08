# #334 Codex OAuth json_mode 端到端复测

针对 [issue #334](https://github.com/whiteguo233/OpenBiliClaw/issues/334) 的修复（提交 `627473c1`，合并 `04b3a65c`），在真实配置、真实服务栈与真实 Codex ChatGPT 后端上复测初始化时的「偏好分析」失败点：

- 两个正向批次都通过：单批 12 条事件 1 次调用 27.1 秒；init 同款分片路径 60 条事件 2 个分片 56.7 秒，最终合并出 18 个带权兴趣。
- 对照组只剥掉请求体末尾的 `Return valid json.` 标记，同一套服务栈立刻复现 issue 里的异常链（`All providers failed ... must contain the word 'json'`）。

本页记录的是**偏好分析阶段**的端到端证据，不是完整 `openbiliclaw init`、桌面打包产物或商店版本的验收，边界见「范围与未覆盖」。

## 根因与修复位置

`CodexChatGPTProvider` 在 `json_mode=True` 时发送 `text.format={"type":"json_object"}`，但 Codex `/responses` 只用 `input` items 校验该约束，完全忽略 `instructions`。结构化任务（偏好分析 / 觉察 / 洞察等）的「严格 JSON」规则由 `LLMService._structured_json_contract()` 写进 system message，落到请求里是 `instructions`，因此 `input` 中没有 `json` 时后端先返回 HTTP 200，再在 SSE 流里回 `error` 事件，`_parse_codex_sse_response()` 抛出 `LLMResponseError`；既有回退只覆盖 HTTP 非 2xx 与空正文两种情况，抓不到该错误。

修复在 `_build_request_body()`：`json_mode=True` 且 input 中没有大小写不敏感 `json` token 时，在**末尾**追加一条 `Return valid json.` user 指令，与 OpenAI Platform Responses flavor 的既有补偿（#265）一致。追加点在尾部，`instructions` 与调用方消息组成的缓存前缀保持字节一致。

## 环境

| 项 | 值 |
|---|---|
| 代码 | `main` @ `04b3a65c`（editable 安装，运行的即是该提交） |
| 模型 | `gpt-6.1-sol`（Codex 后端目录返回的首个可见模型，与 issue 报告者一致） |
| 端点 | `https://chatgpt.com/backend-api/codex/responses` |
| 配置 | 真实 `load_config()`：v2 instance routing（`[llm.instances.codex] provider_type="openai"`、`auth_mode="codex_oauth"`），临时配置文件，未改用户 `config.toml` |
| 凭据 | `openbiliclaw login codex --import` 从官方 `~/.codex/auth.json` 导入；产品自带探测通过（`gpt-6.1-sol`，2889ms，有效期至 2026-11-06） |
| 网络 | `[network] mode="system"` + `HTTPS_PROXY=127.0.0.1:7897`；Codex 传输的 `trust_env` 由该模式决定，本机直连 chatgpt.com 不通 |
| 数据 | 本机 `data/openbiliclaw.db` 的只读副本（109,390 条事件），取最近的真实 B 站 `view` 事件；画像库未写入 |
| 时间 | 2026-10-29 03:37–03:41（本地） |

## 真实请求结果

| 批次 | 事件 | prompt 字符 | 分片 | 真实调用 | tokens（in/out/total） | 耗时 | 产物 |
|---|---|---|---|---|---|---|---|
| A 单批 | 12 | 11,676 | 1 | 1 | 5,028 / 967 / 5,995 | 27.1s | interests / cognitive_style / exploration_openness / favorite_up_users / source_platform_mix |
| B init 分片 | 60 | 超过 24k 预算阈值 | 2（1/2、2/2） | 2 | 16,798 / 2,162 / 18,960 | 56.7s | 18 个带权兴趣、6 条认知风格、openness 0.6 |

批次 A 的 prompt 中 user 消息确认不含 `json`（`user_contains_json=false`），即只有修复后的标记能让该请求满足后端约束。实际发送体末尾为 `Return valid json.`，`text.format` 保留，`instructions` 未变。

批次 B 走 init 的分片路径（`event_chunk_size=200` + 预算分片），两个分片并发真实调用 `soul.preference.chunk`，合并后兴趣示例：

| 兴趣 | 权重 |
|---|---|
| 游戏资讯与行业动态 | 0.56 |
| 人工智能动态与应用 | 0.53 |
| 篮球比赛与球员故事 | 0.52 |
| 动漫评论与观影反应 | 0.52 |
| 王者荣耀英雄玩法 | 0.50 |
| 动漫与二次元文化 | 0.47 |

## 对照组：剥掉标记即复现 issue

同一套配置、`LLMService`、`PreferenceAnalyzer` 与事件，仅在发送前把尾部 `Return valid json.` 剔除，真实后端返回的异常链与 issue 日志一致（provider 名为实例 ID `codex`，issue 中是 `chatgpt`）：

```text
Provider codex failed; no fallback provider left to try.
PreferenceAnalysisError: All providers failed (codex). Last error:
openai codex stream error: Response input messages must contain the word 'json'
in some form to use 'text.format' of type 'json_object'.
```

这说明失败点与修复点一致，不是靠单测形状推断。

## 复现方式

1. `openbiliclaw login codex --import --probe`（`[network] mode="system"` + `HTTPS_PROXY`）。
2. 临时配置：`[llm] routing_version=2`、`default_chain=["codex"]`、`[llm.instances.codex] provider_type="openai" auth_mode="codex_oauth" model="gpt-6.1-sol"`。
3. 从 `data/openbiliclaw.db` 只读副本取最近 B 站 `view` 事件，调用真实 `PreferenceAnalyzer.analyze_events()`；对照组在发送前剥掉尾部标记。

驱动脚本与批次 B 的完整输出留在本机 `/tmp/e2e-334/`（未入库）：`run_e2e.py`、`run_e2e_v2.py`、`run_e2e_control.py`、`preference_result.json`。

## 范围与未覆盖

- 只复测 issue 中失败的**偏好分析**阶段。觉察 / 洞察 / 画像等其它 `json_mode` 调用共用同一传输层与同一 `_build_request_body()` 路径，单测覆盖请求体契约，但没有逐一做真机请求。
- 没有运行完整 `openbiliclaw init`：本轮不涉及 B 站登录、账号同步、画像写库与桌面 / 网页 UI，因此不把本页当作完整初始化验收。
- 已安装的 0.3.232 桌面 / 商店包不包含该修复，需要重新构建后才生效；本页没有验证打包产物。
- 测试使用临时配置与数据库副本，未修改用户 `config.toml`，未写入用户画像库。

## 副作用与清理

- 本机原先的 `~/.openbiliclaw/codex_auth.json` 已过期且刷新失败；导入后更新为有效期至 2026-11-06 的凭据，旧文件备份为 `~/.openbiliclaw/codex_auth.json.bak-20261029`。
- 218MB 的数据库副本在测试结束后删除；脚本与结果 JSON 保留在 `/tmp/e2e-334/`。
- 全部请求只使用临时配置中的 Codex 实例，没有产生其它来源或第三方调用。
