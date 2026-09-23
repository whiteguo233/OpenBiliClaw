# OpenBiliClaw Sponsored Runtime 设计（无后端修订版）

- 日期：2026-09-22
- 状态：设计定稿，待实现
- 前置方案：SiliconFlow Sponsored Runtime 初版方案
- 约束（已确认）：
  1. SiliconFlow 允许把 Sponsored Key 打包进官方发行版；
  2. Key 需要加密/加固，尽量抬高破解成本；
  3. 客户端私有模块负责配额、rate limit、API 用量限制；
  4. 项目方没有后端、没有中转、没有 telemetry，Runtime 直连 SiliconFlow；
  5. Sponsored Runtime 放独立 private repo，不进当前开源仓库。

---

## 0. 本版推翻/修正了什么

初版方案把客户端校验（Rust + Signed Policy + Prompt Fingerprint + JSON Schema）当成安全边界。在“Key 允许打包 + 无后端”的前提下，这个定位不成立。本版重新定位如下：

| 能力 | 定位 |
| --- | --- |
| 限制请求范围（task / schema / model / tokens） | 降低单次请求的滥用价值 |
| Key 加密、分片、加固 | 抬高逆向成本，不承诺不可提取 |
| 本地配额 / rate limit / 用量账本 | 公平性与止损，可被 patch/重装绕过 |
| 专用 SiliconFlow 账号固定余额 + 账号级 rate limit + 低余额熔断 + 账号/Key 轮换 | **唯一的成本和安全边界** |
| Rust 私有二进制 | 提高 patch 成本，不是安全边界 |

本版之后，不应再出现以下验收口号：

- “用户无法提取 Key”；
- “无法把 Runtime 当免费 LLM proxy 使用”；
- “客户端可以防止刷额度”。

这些目标在用户完全控制本机的前提下不可达。可承诺的是：

> Key 不以明文出现在源码、配置、日志和静态字符串中；正常用户不会无意暴露 Key；简单复制二进制或调用 Runtime 不能绕过已定义的官方任务范围；最坏情况下的费用由专用赞助账号的余额上限兜底（预付费、不自动充值、低水位运行）。

---

## 1. 目标与非目标

### 1.1 目标

1. 官方安装包用户无需 API Key，即可使用 Sponsored 结构化任务。
2. Prompt、Profile、推荐数据不经过 OpenBiliClaw 自有服务器；Runtime 只连接 Sponsored endpoint。
3. Sponsored Key 只存在于私有 Runtime 进程内存中，且以包裹形式落盘/入二进制。
4. 本地配额、限流、用量账本由私有模块实现，提供基本公平性与失控止损。
5. Prompt 更新不需要重新编译 Rust：Prompt 继续由开源 Python 管理，Runtime 只按签名 Policy 校验。
6. 开源版与官方版能力边界清晰：源码部署走 BYOK / Ollama，官方版额外带 Sponsored Runtime。

### 1.2 非目标

1. 防御专业逆向、调试器 dump、代理抓包、patch 二进制。
2. 防御用户自写客户端调用 Runtime。
3. v1 提供通用 Chat / agent / 任意 Prompt。
4. 项目方遥测、账号体系、设备注册、云端激活。
5. 自研加密算法、white-box crypto、复杂 anti-debug 军备竞赛。

---

## 2. 威胁模型（修订版）

| # | 攻击者能力 | 本方案措施 | 真实兜底 |
| --- | --- | --- | --- |
| 1 | 打开源码/配置找 Key | Key 只在私有 Runtime；Python/config 无 Key | — |
| 2 | `strings binary` / grep `sk-` | AEAD 包裹 + 分片，无连续明文 | 定期轮换 |
| 3 | 修改 `sponsored-policy.json` 扩权 | Ed25519 签名，签名校验先于解析 | 防君子 |
| 4 | 直接 import Python provider 或自写 IPC 调用 | 不防（task/schema 公开） | 账号级 rate limit + 余额上限 |
| 5 | 自签 CA 代理 / SSLKEYLOGFILE / hook `SSL_write` | 不防 | 账号级 rate limit + 余额上限 + 账号轮换 |
| 6 | 调试器/内存 dump 发请求前的进程 | 不防（zeroize 只减少窗口） | 账号级 rate limit + 余额上限 + 账号轮换 |
| 7 | patch Runtime 跳过校验/配额 | 不防 | 账号级 rate limit + 余额上限 + 账号轮换 |
| 8 | 删本地账本/重装/多开刷任务 | 本地加固提高成本 | 账号级 rate limit + 余额上限 |
| 9 | Prompt injection 借用合法 schema 生成任意文本 | Output Contract 限 token/格式 | 余额上限 + 账号级 rate limit |

**结论：** 第 4-9 项不可能靠客户端解决。实现上要接受它们，并把工程资源投到“限制单次伤害（task/schema/token/model）”和“用专用账号 + 固定余额 + 账号级限流 + 低余额熔断设总闸”。

---

## 3. 总体架构

```text
┌──────────────────────────────────────────────┐
│ OpenBiliClaw Core（MIT，public）              │
│   Soul / Discovery / Recommendation          │
│   LLMService（caller + task 映射）            │
│   SponsoredProvider（IPC client，public）     │
└───────────────┬──────────────────────────────┘
                │ stdin/stdout framed JSON
                ▼
┌──────────────────────────────────────────────┐
│ obc-sponsored-runtime（private/closed）       │
│   Policy Verify → Contract Validate → Quota   │
│   Rate Limit → Key Unwrap → HTTP Client       │
└───────────────┬──────────────────────────────┘
                │ HTTPS（不收系统代理，见 §12）
                ▼
     SiliconFlow Sponsored Endpoint
```

不存在：

```text
用户 → OpenBiliClaw 服务器 → SiliconFlow
```

只存在：

```text
用户设备 → SiliconFlow
```

隐私文案必须写成“数据直接发送给 SiliconFlow，受其隐私政策约束；OpenBiliClaw 项目方不经过、不保留、不中转”，不能写成“没有任何第三方看到”。

---

## 4. 仓库与发布边界

### 4.1 Public：`OpenBiliClaw`

| 路径 | 内容 |
| --- | --- |
| `src/openbiliclaw/llm/sponsored_provider.py` | IPC client，实现显式 `execute_task()` |
| `src/openbiliclaw/llm/sponsored_contracts.py` | task_id / contract_version / caller 映射 |
| `sponsored/schemas/*.json` | Context Schema（Draft 2020-12） |
| `scripts/generate_sponsored_policy.py` | 从代码生成**未签名** policy |
| `sponsored/mock_runtime/` | 开发/CI 用假 Runtime，实现同一 IPC 协议 |
| `tests/sponsored/` | contract golden、字节级 canonical 测试 |
| `docs/` | 本文档、用户可见说明 |

Public 仓库不包含：真实 Key、Key wrapping 密钥、签名私钥、Rust Runtime 源码、SiliconFlow 管理凭证。

### 4.2 Private：`openbiliclaw-sponsored-runtime`

```text
openbiliclaw-sponsored-runtime/
├── Cargo.toml
├── src/
│   ├── main.rs          # IPC loop / lifecycle
│   ├── ipc.rs           # framing / handshake / cancel
│   ├── policy.rs        # Ed25519 verify + parse
│   ├── validator.rs     # task/topology/schema/limits
│   ├── quota.rs         # ledger / rate limit
│   ├── secret.rs        # unwrap / zeroize / header build
│   ├── siliconflow.rs   # HTTPS client
│   └── error.rs         # stable error codes
├── tools/wrap-key/      # 构建期 Key 包裹工具
├── tests/               # 真实 Policy/Contract 测试
└── .github/workflows/   # protected release pipeline
```

**Key 和签名私钥都不 commit 到这个仓库。** Private repo 只保存代码；Key 只存在 release CI 的 secret / KMS，签名私钥只存在受保护 environment 或 HSM。

### 4.3 发布链路

```text
public repo @ core_sha
   │ generate unsigned policy + contract goldens
   ▼
private repo @ runtime_sha
   │ 拉取 public artifacts（pin SHA）
   │ contract tests
   │ CI 注入 wrapped key（secret/KMS）
   │ build + sign runtime
   │ Ed25519 sign policy
   ▼
official bundle: Core + Runtime + policy + policy.sig
   │ 代码签名/notarize
   ▼
Release
```

Release manifest 记录 `{core_sha, runtime_sha, policy_version}`，保证可追溯。

---

## 5. Contract 模型

### 5.1 task_id 必须显式传递

现有代码只有 `caller` 字符串（`soul.preference`、`recommendation.evaluate_batch` 等）。Provider 层拿不到 `caller`，不能靠 Prompt Hash 反查 task（脆弱且难调试）。

实现要求：

- `LLMService` 在调用链中把 `caller`/task 显式传给 SponsoredProvider；
- SponsoredProvider 不实现通用 `complete()` 路由；新增 `execute_task(task_id, contract_version, messages, ...)`；
- Runtime 的 `execute` 必须收到显式 `task_id`，只用它查 Policy；
- `health_check()` 不得发 Runtime 请求，本地直接返回 `False`（Sponsored 不可用时 fallback）。

v1 task 白名单直接映射现有 caller：

```text
soul.preference              soul.awareness
soul.awareness_confusions    soul.insight
soul.consolidation           discovery.keyword_planner
discovery.evaluate_single    discovery.search.queries
recommendation.evaluate_batch
recommendation.write_expression
profile.consolidate
```

是否有任务需要合并/拆分，Phase 1 逐项审计后再定。

### 5.2 Sponsored v1 只接受静态 system prompt

现状：`LLMService.complete_with_core_memory()`（`src/openbiliclaw/llm/service.py:536-545`）默认把用户级 core memory 拼进 system content：

```python
parts = [system_instruction.strip()]
if stable_block:
    parts.append("以下是当前用户的 core memory，请作为理解背景：")
    parts.append(stable_block)
system_content = "\n\n".join(parts)
```

这意味着 `soul.*` 任务的 system prompt 每个用户都不同，无法进 Hash 白名单。

**v1 决策：**

- Sponsored 任务的 system prompt 必须是 `canonical_system_prompt(task_id)` 的逐字节输出；
- Sponsored 任务禁止把 core memory / 用户数据注入 system message；
- Profile / core memory 作为 user schema 的显式字段传入；
- 对 sponsored 任务设置 `inject_core_memory=False`，或由 SponsoredProvider 在发送前本地拒绝非 canonical system prompt 并给出 `CONTRACT_MISMATCH`。

这是对现有 Prompt 行为的改动，会改变发给模型的字节。实施时必须跑相关任务的 eval/回归，不能只改代码不看输出质量。

### 5.3 canonicalization 单一来源

```python
def canonical_system_prompt(task_id: str, contract_version: int) -> str:
    """唯一允许生成 Sponsored system prompt 的函数。"""
```

- `generate_sponsored_policy.py` 和运行时发送路径必须调用同一个函数；
- 统一 UTF-8、LF、无 BOM；不做会随环境变化的 trim/拼接；
- `_structured_json_contract()` 这类改写必须在 canonical 路径内完成，不能在发送前再加一层；
- 增加 golden-byte 测试：固定 task 的 system prompt SHA-256 写进测试，任何 Prompt 改动都会显式更新黄金值，CI 阻止忘记重签 Policy 的发布。

### 5.4 Context Schema 与限额

- JSON Schema Draft 2020-12；禁止 `$ref` 到网络；顶层 `additionalProperties: false`；
- Schema 描述 user message 的结构（含 profile、items、core_memory 等显式字段）；
- 限额双层：
  - 本地：`max_input_bytes`（Rust 无 tokenizer 时保守按 bytes）、`max_output_tokens`、字段级上限；
  - 权威：专用赞助账号的余额与平台账号级 rate limit；
- 自由文本字段必须设字段级上限；Schema 不防语义注入，只降低单次输出价值。

### 5.5 Output Contract

- `response_format=json_object`；
- v1 不流式：流式无法在发送前校验输出契约，且 token 已消耗；
- Runtime 不尝试修复模型输出；解析失败交给 Python 现有 tolerant parser。

---

## 6. IPC 协议 v1

- 父进程 spawn Runtime 子进程，stdin/stdout 二进制帧，stderr 只放日志；
- 帧格式：4 字节大端长度 + UTF-8 JSON；单帧上限 16 MiB；
- 不开放 localhost 端口、不监听 LAN、不被浏览器访问；
- 生命周期由 Core 管理，父进程退出即关闭 stdin，Runtime 自行退出。

### 6.1 Handshake

```json
{"type":"hello","protocol":1,"core":"0.4.0","runtime":"0.1.0","policy_version":27}
```

版本不匹配 → `UNSUPPORTED_PROTOCOL`，Core fallback 到 BYOK。

### 6.2 Request

```json
{
  "type": "execute",
  "id": "9f2c...",
  "task_id": "recommendation.evaluate_batch",
  "contract_version": 3,
  "messages": [
    {"role": "system", "content": "<canonical system prompt>"},
    {"role": "user", "content": "<schema-validated JSON>"}
  ],
  "requested": {"max_tokens": 4096}
}
```

规则：

- `model` / `temperature` / `max_tokens` / `reasoning_effort` 由 Policy 决定；请求里的值只作为提示，Runtime 有权覆盖或拒绝；
- Python 不能指定任意 endpoint、model、headers、response_format。

### 6.3 Response

```json
{
  "type": "result",
  "id": "9f2c...",
  "ok": true,
  "content": "{\"items\":[...]}",
  "model": "siliconflow-model-id",
  "usage": {"input_tokens": 1234, "output_tokens": 456},
  "error": null
}
```

失败：

```json
{"type":"result","id":"...","ok":false,"error":{"code":"QUOTA_EXCEEDED","message":"local daily token budget exhausted"}}
```

### 6.4 Cancel / timeout / concurrency

- `{"type":"cancel","id":"..."}`：取消上游 HTTP 请求；
- 默认超时由 Policy 决定，Core 侧也有超时；
- v1 每个 Runtime 子进程同一时间只处理一个请求；需要并发时由 Core 管理进程池；
- 所有错误必须映射到稳定错误码（见 §10），message 不得包含 Prompt/用户内容。

---

## 7. Signed Policy

### 7.1 文件

- `sponsored-policy.json`：由 public `scripts/generate_sponsored_policy.py` 生成；
- `sponsored-policy.sig`：private release CI 用 Ed25519 私钥签名；
- Runtime 内置 Ed25519 公钥（支持 `signing_key_id` 轮换）；
- 启动时先验签，再解析 JSON；Policy 随 release bundle 发布，不远程拉取。

### 7.2 结构示例

```json
{
  "policy_version": 27,
  "generated_at": "2026-09-22T00:00:00Z",
  "expires_at": "2026-12-21T00:00:00Z",
  "signing_key_id": "release-2026",
  "core": {"min": "0.4.0", "max": "0.4.*"},
  "keys": [
    {
      "key_id": "sf-sponsored-2026-09-a",
      "wrapped_key": "base64:....",
      "nonce": "base64:....",
      "not_before": "2026-09-22T00:00:00Z",
      "not_after": "2026-11-30T00:00:00Z",
      "endpoint": "https://api.siliconflow.cn",
      "caps": {
        "daily_requests": 5000,
        "daily_tokens": 8000000,
        "monthly_tokens": 150000000,
        "rpm": 60
      }
    }
  ],
  "tasks": {
    "recommendation.evaluate_batch.v3": {
      "system_prompt_sha256": "sha256:....",
      "message_topology": ["system", "user"],
      "input_schema": "schemas/recommendation-evaluate-batch-v3.json",
      "max_input_bytes": 1048576,
      "max_output_tokens": 8192,
      "model": "siliconflow-sponsored-model",
      "temperature": 0.2,
      "response_format": "json_object",
      "limits": {
        "daily_requests": 200,
        "daily_tokens": 500000
      }
    }
  }
}
```

### 7.3 签名规则

- Ed25519 对 Policy 文件**原始字节**签名，不依赖 JSON canonicalization；
- 验证通过后才反序列化；`expires_at` / `not_before` 校验；
- 签名私钥永不进入 repo、安装包、Rust binary、构建日志；
- 公钥轮换：Runtime 内置多个 `signing_key_id → public_key`，Policy 指定用哪个。

### 7.4 Policy 生成与 CI 门禁

```text
当前 public 代码 @ core_sha
   ↓
extract contracts（task_id / version / canonical prompt / schema / limits）
   ↓
生成 unsigned sponsored-policy.json
   ↓
contract tests（正常请求 PASS，越权请求 DENY）
   ↓
private CI 注入 wrapped key + Ed25519 签名
   ↓
bundle release
```

Public CI 必须包含：

- canonical prompt golden-byte 测试；
- Python/Rust schema parity 测试（同一组 fixtures 两端跑）；
- Policy 生成结果 diff 检查；
- 任一项失败阻止 release。

---

## 8. Key 保护设计（private repo 实现）

### 8.1 诚实声明

用户控制本机，因此 Key 无法做到不可提取。以下措施只把攻击成本从 `strings | grep sk-` 提高到“附加调试器 / 代理抓包 / patch unwrap 逻辑”。

### 8.2 构建期

> 安全纪律：任何在聊天、issue、PR、工单或文档中出现过的 Key 都视为已泄漏：能删除就立即删除；不能删除则停止给该账号充值并按 §13.2 废弃账号，重新生成专用 Key。Key 只能由 release owner 从控制台直接写入 CI secret/KMS，不经过聊天或仓库。仓库启用 gitleaks/secret scanning 防止回填。

1. CI 从 secret/KMS 读取明文 Key，只在构建内存中存在；
2. `tools/wrap-key` 生成随机 KEK，用 AEAD（ChaCha20-Poly1305 或 AES-256-GCM）包裹 Key；
3. KEK 被拆成 N 个 fragment，散布到 binary 不同位置；fragment 之间可做 XOR / HKDF 组合，不出现连续明文；
4. 只把 `ciphertext + nonce + fragments` 嵌入 Runtime；构建结束后 CI 清除临时文件；
5. 不把明文 Key 写进源码、配置、测试 fixture、日志、CI artifact。

### 8.3 运行期

1. 从 fragments 按顺序组装 KEK；
2. AEAD 解密到 `Secret<Vec<u8>>` / `Zeroizing`；
3. 只在构造 `Authorization` 头时短暂存在；
4. 请求发出后立即 zeroize；错误路径同样 zeroize；
5. Key 不经过 IPC、不写日志、不进 panic message、不进 core dump。

### 8.4 加固项（按性价比）

- `lto = true`、`strip = "symbols"`、`panic = "abort"`；
- 关闭 core dump（macOS/Windows/Linux 各自配置）；
- macOS hardened runtime，不启用 `get-task-allow`；
- Windows Authenticode；macOS Developer ID + notarize；
- 可选：代码段自校验，patch 后 unwrap 失败（提高 patch 成本，但不是边界）。

### 8.5 明确不做

- 自研加密算法 / 自定义握手 / 隐藏 Hash 算法；
- white-box crypto 采购；
- 大量 anti-debug、反虚拟机、神秘 Device ID；
- 账号体系、云端 activation、设备绑定、telemetry。

### 8.6 Key 轮换

- Policy 支持多个 `keys[]`，Runtime 按 `not_before/not_after` 选择；
- 每次 release 轮换 Key，同时保留 1 个旧 Key 的升级窗口；
- 窗口结束由 SiliconFlow 将旧 Key cap 降级或吊销，旧版本用户 fallback BYOK；
- 建议每次 release 打 N 把 Key（见 §9.5），降低单 Key 泄漏的爆炸半径。

---

## 9. 本地配额 / Rate limit / 用量账本（private repo）

### 9.1 定位

本地账本解决两件事：

1. **公平性**：单机不会无限占用；多 Key 分片时避免一只脚本吃光所有 Key；
2. **止损**：一般用户无意的大批量任务会被拦下。

它不解决“恶意用户刷额度”——patch / 重装 / 多开都可以绕过。**总闸在 §9.8 的专用账号余额 + 账号级限流。**

### 9.2 账本状态

```text
install_id           随机 128-bit，首次运行生成
generation           单调递增，用于防回滚
updated_at_wall      最近一次写入的墙钟
last_seen_mono       进程内单调时钟
days[]               {date, requests, input_tokens, output_tokens}
months[]             {month, requests, input_tokens, output_tokens}
rate_window[]        最近 N 秒请求时间戳（可粗粒度持久化）
disabled_until       触发限流后的冷却截止
tamper_flag          检测到异常后置位
```

### 9.3 存储与完整性

- 优先使用 OS 安全存储：
  - macOS Keychain；
  - Windows DPAPI（附加 entropy）；
  - Linux libsecret，不可用时 `~/.local/share/openbiliclaw/sponsored/` + `0600`；
- 账本写入前用 HMAC-SHA256 保护，HMAC key 由 `install_id` + binary 常量派生；
- 原子写 + 上一代备份，加载时校验 generation 与 HMAC；
- 检测到以下情况视为篡改：HMAC 失败、generation 倒退、墙钟明显倒退、备份与主文件都不一致；
- 篡改时返回 `LEDGER_TAMPERED`，关闭 Sponsored 并 fallback，**不静默清零**。

### 9.4 执行算法

```text
execute(request):
  1. verify policy signature / expiry
  2. validate task / contract / system hash / topology / schema / limits
  3. rate_limit_check(install_id, now)
  4. reserve = estimate_input_tokens + max_output_tokens
  5. if day/month remaining < reserve: return QUOTA_EXCEEDED
  6. send HTTPS request
  7. usage = response.usage or conservative estimate
  8. reconcile(reserve, usage); persist atomically
  9. return result
```

- 并发请求先 reserve、后 reconcile，避免并发穿透；
- 写入可合并/延迟，但进程退出前必须 flush；
- Runtime 崩溃后恢复时以最后一次成功写入为准，因此 reserve 不能太小。

### 9.5 多账号/多 Key 分片（建议）

```text
account_index = HMAC-SHA256(install_id, "sponsored-account-select") mod len(accounts)
```

- 平台不支持 per-key cap，所以每个分片使用**独立 SiliconFlow 账号**（各自有独立余额与账号级 rate limit），而不是同一账号下的多个 Key；
- 单账号泄漏/被刷不炸全量，可以把该账号余额自然耗尽后废弃并发布新账号；
- 攻击者虽然能拿到全部 Key，但总损失被各账号余额之和分割；
- 账号数量建议 2-4，按 release 轮换；每个账号余额保持低水位。

### 9.6 限额来源

全部来自签名 Policy，避免硬编码：

- 全局 per-install：日/月请求数、日/月 token、RPM、最大并发；
- per-task 覆盖：例如后台评估任务额度大、生成类任务额度小；
- 单请求上限：`max_input_bytes` / `max_output_tokens`（Contract 层已限制）。

### 9.7 局限（必须写进文档，不能对外承诺）

- 删除 Keychain / 重装 / 多开进程可以重置本地计数；
- patch Runtime 可以跳过配额检查；
- `install_id` 只防“误删文件”，不防“恶意重置”；
- 因此不能把本地账本当作成本边界，必须叠加 §9.8 的账号余额模型。

### 9.8 服务端边界：专用余额账号模型（无 per-key cap 的补偿）

官方文档确认：SiliconFlow 的 rate limit 按**用户账号**维度执行，不按 API Key 维度；余额是预付费制，余额耗尽即停止服务。基于这两个事实，设计如下：

1. **专用账号**：Sponsored 流量使用独立 SiliconFlow 账号，不与个人或其他业务共用；账号内只放赞助 Key。
2. **余额即 hard cap**：不开启任何自动充值/代扣；账号余额就是最坏情况损失上限。按周/双周预算少量充值，保持低水位（例如 100-300 元，按实际消耗调整）。
3. **账号级限流**：平台账号级 RPM/TPM/RPD/TPD 仍生效，可挡住单 Key 泄漏后的瞬时刷量；免费模型与付费模型的档位不同，需在账号侧确认。
4. **低余额熔断**：Runtime 定期调用 `GET /v1/user/info` 读取 `balance` / `totalBalance` / `status`；低于 Policy 中的 `balance_floor` 或 `status != normal` 时，返回 `BALANCE_LOW` / `AUTH_FAILED`，关闭 Sponsored 并 fallback。**2026-09 实测：`api.siliconflow.cn` 的 `/v1/user/info` 返回 410/20092 deprecated，`.com` 也不接受 `.cn` Key**；Runtime 对探针失败保持上一状态并打日志，同时把 chat 侧 402 / “余额不足” 映射为 `BALANCE_LOW` 作为兜底。
5. **人工监控**：项目方用同一 Key 或控制台定期查看余额消耗速率；日消耗突增即按泄漏处理（见 §13.2）。
6. **轮换 = 换账号/换 Key + 发版**：新账号/新 Key 写进新 Policy；旧账号停止充值，让余额自然耗尽；控制台支持删除 Key 则删除，不支持则废弃账号。
7. **多账号分片**：按 §9.5 把 install 分到不同账号，降低单次泄漏的爆炸半径。

**限制：** 账号级 rate limit 无法按 Key 细分；`/user/info` 查询也可被 patch 绕过；因此“不自动充值 + 低余额水位”是最后一道、也是唯一可强制执行的边界；控制台可删除 Key，所以泄漏响应可以立即吊销。

### 9.9 按正常用户预算与可用性策略

原则（已确认）：**per-install 预算完全按“正常用户正常使用量”来定，不用总量反推收紧。** 总量只决定项目方要备多少账号/余额；总量不足时依次做：扩账号/加预算 → 按任务优先级降级 → 提示用户切换其他模型（BYOK / Ollama）。不能靠长期压低单用户额度来省钱。

#### 9.9.1 单用户预算怎么定（权威口径）

```text
单用户日消耗 C_user = Σ(task 正常日均次数 × task 正常平均 tokens)
per-install 限额 = P99(C_user) × 2
```

- 以“正常用户不会碰到”为准，不参与总量除法；
- 每个 task 的正常日均次数/平均 tokens 先用保守估算，实测后回写 Contract；
- 异常行为（高频重复、超大输入、明显脚本化）单独限流，不通过降低全体用户预算解决；
- 发布前对每个 Sponsored task 做 token 均值/峰值压测。

#### 9.9.2 分级降级（Runtime 本地按账号余额执行）

Runtime 定期调用 `/user/info`，把账号余额映射到 Policy 声明的阈值：

| 级别 | 触发 | 行为 |
| --- | --- | --- |
| `normal` | 余额 > `balance_conservation_floor` | 正常支持全部 Sponsored task |
| `conservation` | 余额低于 `balance_conservation_floor` | 关闭低价值/生成类任务（如文案生成），保留理解、评估、画像等核心任务 |
| `stopped` | 余额低于 `balance_stop_floor` 或 `status != normal` | 关闭全部 Sponsored，fallback BYOK/Ollama，UI 提示切换模型 |

- 阈值写进签名 Policy，不硬编码；
- 多个分片账号各自判断；不同分片可处于不同级别；
- 账号级 429 时本地退避，不重试轰炸；
- 这是“尽力而为”的协同降级，无后端也能工作（每个客户端都能读到账号余额）。

#### 9.9.3 per-install 配额与总量解耦

- 单用户额度见 §9.9.1，与账号余额/目标用户数无关；
- 额度用尽只影响该 install，不阻塞其他用户，也不触发全局降级；
- 账号余额低水位只触发 §9.9.2 的全局分级降级，不改写单用户额度；
- 额度值随版本和实测数据调整，不承诺永久固定。

#### 9.9.4 用户可见性

- UI 显示 Sponsored 免费额度状态（可用/降级/暂时不可用）；
- 额度用尽或被限流时明确提示原因与选项：等待恢复、切换 BYOK、切换到 Ollama；
- 不暴露账号、余额、Key、内部错误细节。

#### 9.9.5 项目侧监控（无 telemetry）

- Runtime 本地账本记录用量，用户可导出诊断；
- 项目方按账号余额消耗速率 + 控制台用量做人工/脚本监控（无法精确统计活跃用户）；
- 预警线（运营参考）：按正常用户预算估算的 14 天消耗不足时提前充值/扩账号；单账号日消耗 > 估算 2 倍时排查泄漏；
- 赞助到期或模型下架时，提前发布 Policy 切换模型或关闭 Sponsored，避免客户端报错。

---

## 10. 错误模型与 fallback

| code | 含义 | Sponsored 行为 | Fallback |
| --- | --- | --- | --- |
| `UNAVAILABLE` | Runtime 缺失/崩溃/握手失败 | disable 本轮 | 是，静默降级 |
| `UNSUPPORTED_TASK` | task 不在 Policy | deny | 是（该任务走 BYOK） |
| `CONTRACT_MISMATCH` | prompt hash/schema/version 不一致 | deny | **否**，本地 diagnostics + 报错 |
| `REQUEST_TOO_LARGE` | 超出输入限额 | deny | 是（可能是用户内容合法过大） |
| `QUOTA_EXCEEDED` | 本地日/月预算耗尽 | deny | 是，但 UI 明确提示 |
| `RATE_LIMITED` | 本地滑动窗口/上游 429 | deny/退避 | 是，UI 提示 |
| `AUTH_FAILED` | Key 失效/被吊销/额度耗尽 | disable 本轮 | 是，本地 diagnostics |
| `BALANCE_LOW` | 账号余额低于 floor / 账号状态异常 | 关闭 Sponsored | 是，本地 diagnostics |
| `LEDGER_TAMPERED` | 账本 HMAC/generation 异常 | 关闭 Sponsored | 是，本地 diagnostics |
| `UPSTREAM_ERROR` | SiliconFlow 5xx/网络错误 | 有限重试后 deny | 是 |

原则：

1. **契约类错误不进 fallback**，否则线上 Prompt/Policy 不同步会被静默掩盖；
2. fallback 到 BYOK 可能产生用户费用，UI 必须提示，配置项默认开启但要可见；
3. 所有错误只记录 code/task/latency/token，不记录 Prompt/画像/响应内容。

---

## 11. Python 侧设计

### 11.1 Provider 接口

```python
class SponsoredProvider:
    async def execute_task(
        self,
        *,
        task_id: str,
        contract_version: int,
        system_prompt: str,
        user_payload: dict[str, Any],
        caller: str,
    ) -> LLMResponse: ...
```

- 不实现通用 `complete()`；避免被当 BYOK 通道使用；
- 输入组装、canonical system prompt、schema 校验在 Public 侧完成；
- Runtime 返回后映射为现有 `LLMResponse`，usage 写入现有 usage recorder；
- Key、endpoint、model 对上层不可见。

### 11.2 LLMService 集成

- 新增 `execute_sponsored_task()` 路径，`caller` → `task_id/contract_version` 映射由 `sponsored_contracts.py` 维护；
- Sponsored 任务强制 `inject_core_memory=False`，profile 数据进入 `user_payload` 的显式字段；
- `model` / `temperature` / `max_tokens` 不传给 Runtime 生效，由 Policy 决定；
- Registry 的通用 fallback chain 保持现状，但 Sponsored 错误需按 §10 分类；
- `health_check()` 本地短路，不发 Runtime。

### 11.3 配置与当前实现

官方配置（已实现，见 `docs/modules/config.md`）：

```toml
[llm.sponsored]
enabled = false                  # 源码部署保持 false；官方安装包启用
runtime_path = ""                # 官方包内 obc-sponsored-runtime 路径/命令行
fallback_to_user_provider = true
notify_on_fallback = true
request_timeout_seconds = 180.0
```

- `load_config_with_diagnostics()` 把 `[llm.sponsored]` 安装为进程级设置，`LLMService` 在首次需要时懒加载 Runtime 子进程，避免改动 9 个 `LLMService` 构造点；
- 开发/测试仍可用环境变量兜底：`OPENBILICLAW_SPONSORED_ENABLED=1` + `OPENBILICLAW_SPONSORED_RUNTIME="<runtime command line>"`；
- 配置 enabled 但 runtime_path 为空 → blocking 配置问题；路径不存在 → warning，不影响 BYOK；
- `execute_sponsored_task()` 对可用性/配额/限流错误回退，对 `CONTRACT_MISMATCH` 直接失败并写本地 diagnostics，不花用户自己的模型费用。

### 11.4 UI 状态

- “OpenBiliClaw 免费模型 / SiliconFlow 提供算力 / 无需 API Key”；
- 本地额度用尽/上游限流时显示明确状态与恢复时间；
- fallback 到用户自己的模型时提示可能计费；
- Key 失效时提示“免费模型暂不可用，可在设置中切换自己的模型”，不暴露技术细节。

---

## 12. 网络、日志与隐私

### 12.1 网络

- Runtime 只允许连接 Policy 中声明的 `endpoint`；
- 默认忽略 `HTTPS_PROXY` / `HTTP_PROXY` / `SSL_CERT_FILE` / 系统自定义 CA，使用 rustls + webpki roots；
- 可选证书 pinning（抬高自签 CA 抓包成本；注意 SiliconFlow 证书轮换）；
- 超时、有限重试、429 退避；4xx 不重试；
- 不访问 OpenBiliClaw 域名、analytics、telemetry。

### 12.2 日志

默认不记录：

```text
Prompt / 用户画像 / Content / LLM Response / API Key / Authorization header
```

只允许本地记录：

```text
task_id / success / failure / latency / error code / token usage / policy version
```

日志只写 stderr；不写用户目录之外的持久化文件；核心路径不因日志失败而失败。

### 12.3 其他隐私加固

- 关闭 core dump；
- panic message 不包含 Key/请求体；
- 错误消息不只回显请求内容；
- 崩溃上报、诊断上报如果存在，必须过滤 Prompt 字段；
- 用户可见隐私说明明确写出“数据直连 SiliconFlow”。

---

## 13. 发布与轮换 Runbook

### 13.1 每次 release

1. public repo 冻结 `core_sha`，跑 contract golden / schema parity；
2. 生成 unsigned policy；
3. private CI（protected environment）拉取 public artifact；
4. 注入 wrapped keys、构建 Runtime、跑真实合同测试（可用 fake endpoint）；
5. 用 Ed25519 私钥签名 Policy；
6. bundle Core + Runtime + policy + sig，代码签名/notarize；
7. 发布 manifest 记录三个 SHA 与 policy_version；
8. 确认专用账号余额水位、账号级 rate limit 状态、旧账号/旧 Key 处理方式（删除或停止充值）。

### 13.2 Key 泄漏应急

1. 控制台已确认可以删除/禁用 Key：发现泄漏立即删除泄漏 Key；
2. 余额低水位仍作为第二道保险：删除前的窗口内，损失上限就是账号当前余额；
3. 其他分片账号保持服务；评估泄漏路径，提前发布新账号/新 Key 版本；
4. 旧版本用户会 fallback 到 BYOK；UI 提示升级可获得免费模型；
5. 复盘本地配额/分片策略与账号水位。

### 13.3 Policy 过期

- `expires_at` 对诚实用户生效；对恶意用户可通过改时钟绕过，不作为安全边界；
- 过期后 Sponsored 自动禁用并 fallback，避免旧版本长期持有旧 Key。

---

## 14. 测试计划

Public：

- canonical prompt golden-byte（每个 sponsored task）；
- caller → task_id/contract_version 映射测试；
- Schema fixture 校验（与 Rust 共享同一组 JSON）；
- Policy 生成快照/diff；
- SponsoredProvider IPC mock 测试：正常、超时、取消、Runtime 不存在、错误码映射；
- fallback 分类测试。

Private：

- IPC framing / 超大帧 / 非法 JSON fuzz；
- Policy 签名验证：篡改、过期、未知 key_id、截断；
- Contract 校验矩阵：正常 / 非白名单 task / system hash 错 / topology 错 / schema 错 / 输入超限 / model 越权 / max_tokens 越权；
- 配额账本：并发 reserve、日/月切换、时钟回退、HMAC 篡改、generation 回退、文件损坏；
- Key unwrap：fragment 缺失、nonce/tag 错、zeroize 路径；
- HTTP：超时、429、5xx、usage 缺失。

对抗性测试（明确记录为“预期可绕过”）：

- 用 public prompts 手写合法 IPC 请求 → 期望 PASS，用于验证账号余额/账号级 rate limit 是唯一兜底；
- 自签 CA 代理 → 期望能抓到 Key，用于验证轮换 runbook；
- 删除账本 → 期望重置，用于验证多账号分片和余额账号模型的必要性。

---

## 15. Phase 计划

| Phase | 内容 | 产出 |
| --- | --- | --- |
| 0 | 确认专用账号余额水位/不自动充值、账号级 rate limit、Key 删除能力、数据政策 | 书面确认 |
| 1 | Public contracts、canonicalization、Policy 生成器、goldens | 可生成 unsigned policy |
| 2 | Private IPC + fake runtime + validator + 错误模型 | PASS/DENY 可跑通，无真实 Key |
| 3 | Key wrap、账本、真实 endpoint、Python Provider 集成 | 官方版可用 |
| 4 | 打包签名、notarize、轮换 runbook | 可发布 |
| 5 | 可选 hardening（自校验、pinning、更多分片） | 提高摩擦 |

Phase 5 永远最低优先级；不要为了 hardening 延后 Phase 1-3。

实现进度（2026-09-23）：

- Public Phase 1/2 已完成并在 PR #263（最新 `8681f9bd`）验证：contracts / policy 生成器 / provider / protocol / mock runtime / typed error fallback / `[llm.sponsored]` 配置 / eval harness；`soul.consolidation` 使用独立 JSON-native 精简 prompt、每批 8 簇、16384 max_output，成功响应写入项目 `llm_usage`；
- Private repo `openbiliclaw-sponsored-runtime`（`main` 最新 `030f11c`）：Policy 验签、contract 校验、IPC、quota 账本（reserve/commit 修正）、Key fragment/公钥 build-time 内嵌、`wrap-key`/`sign-policy`/`public-key`、真实 HTTP client、余额降级、release matrix、对抗测试与 threat model，50 个 Rust 测试通过（另有 1 个 Keychain 测试 `--ignored`）；
- **真实干净实例验证**：独立临时项目根 + 独立 data_dir + 独立空配置，只挂 Sponsored + 本地 mock BYOK；真实 SiliconFlow/`XingChenAGI/Xing4.0-29B` 下 `profile-consolidate --full --apply` 成功：`merges=19`、`errors=0`、likes 44→26、正确保留「篮球/NBA」；重复运行稳定，全部 consolidation 请求走 sponsored（mock BYOK 0 次 chat 调用），`llm_usage` 与 runtime quota 账本均记录真实 token；
- 真实 API 暴露并修复：旧长 prompt 与 JSON 输入不兼容导致 repetition；21-32 簇单批不稳定；4096 输出被 thinking 吃光导致空响应；runtime usage key 与项目 recorder 不匹配导致成本账本为 0；quota reserve 漏记 token 导致用量低估；
- 待完成：release workflow 实跑（protected environment/secrets、notarize/Authenticode、多平台打包），以及基于真实数据的 pilot 质量对比后决定默认启用。

---

## 16. Endpoint / 模型信息与待确认项

已确认（2026-09-22）：

1. **Endpoint**：`https://api.siliconflow.cn/v1/chat/completions`，OpenAI-compatible，`Authorization: Bearer <key>`；Runtime 只允许连接该地址（或后续 Policy 声明的 Sponsored endpoint）。
2. **模型**：`XingChenAGI/Xing4.0-29B`，由 Policy 写死，Python 不可覆盖。
3. **Key**：`sk-` Bearer 形式；**聊天中出现过的那把 Key 已视为泄漏**：控制台已确认可以删除/禁用 Key，立即删除即可；新 Key 由 release owner 从控制台直接写入 CI secret/KMS，不进入任何仓库/CI 日志/文档。
4. **Key 轮换能力**：控制台支持手动删除/禁用 API Key；轮换 = 删除旧 Key + 发布包含新 Key 的版本，低余额水位作为删除前的损失上限。
5. **预算原则**：per-install 预算按正常用户用量独立设定（P99×2），不做总量除法；总量不足时扩账号/加预算，或按 §9.9 分级降级并引导用户切换 BYOK/Ollama。

官方文档查证（2026-09-22）：

1. **Rate limit 是账号级，不是 Key 级**：官方文档明确 "Rate Limits are defined at the user account level, not at the API key level"；账号按消费分 L0-L5，L0 为 1000 RPM / 40000 TPM；免费模型 rate limit 固定。来源：[SiliconFlow Rate Limits](https://docs.siliconflow.com/en/userguide/rate-limits/rate-limit-and-upgradation)
2. **余额是预付费、无自动充值说明**：充值协议说明账户余额用完即无法使用服务，充值包无有效期；这使“专用账号 + 低余额 + 不自动充值”成为实际 hard cap。来源：[用户充值协议](https://docs.siliconflow.com/en/legals/recharge-policy)
3. **可查询余额**：`GET /v1/user/info` 返回 `balance` / `chargeBalance` / `totalBalance` / `status`，Runtime 可用于低余额熔断。来源：[Retrieve user info](https://docs.siliconflow.com/en/api-reference/userinfo/get-user-info.md)。**2026-09 实测 `.cn` 该端点已 deprecated（410/20092），`.com` 不接受 `.cn` Key；低余额保护当前为 best-effort + chat 402 兜底。**
4. **JSON Mode**：官方文档称除 DeepSeek R1/V3 外大多数模型支持 `response_format={"type":"json_object"}`；Xing4.0 需要真实 probe 确认。来源：[JSON Mode](https://docs.siliconflow.com/en/userguide/guides/json-mode.md)
5. **模型**：开放权重为 Xing4.0-29B-A4B，原生 256K context、可扩展 512K，MoE 29B total / 4B active，支持 tool calling；SiliconFlow 侧模型 ID 为 `XingChenAGI/Xing4.0-29B`，实际托管上下文/输出上限需 probe。来源：[GitHub - XingChen-AGI/Xing4.0-29B-A4B](https://github.com/XingChen-AGI/Xing4.0-29B-A4B)

仍待确认：

1. 专用赞助账号是否已存在、当前余额水位、是否与其他业务共用；
2. 正常用户容量目标：预计安装量/DAU、人均日任务数、可接受的月度预算；
3. SiliconFlow 对 Sponsored 流量的数据保留/训练政策；
4. 官方发行平台（macOS arm64/x86_64、Windows、Linux）与签名能力；
5. v1 是否只做后台结构化任务，Chat 继续 BYOK/Ollama。
