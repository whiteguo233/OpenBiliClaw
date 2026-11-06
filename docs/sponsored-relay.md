# 免费模型 HTTPS 中继

本次交付中继代码、客户端路由、部署文件与真实请求证据。**生产服务器/域名尚未提供，未切换线上服务。** `runtime/relay_release.py` 的官方地址目前为空，兼容发行包仍包含本地 Rust SDK；不能将“支持中继”写成“共享 Key 已从已发布包中移除”。

## 连接和身份

```text
插件 / PC / 手机 → 本机后端（本机 HTTP；跨设备使用 HTTPS 或加密私网）
  → HTTPS + 独立设备 Ed25519 请求签名 + 5分钟不透明 Token
  → Caddy → loopback HTTP :8430 → RelayStore + 已签任务契约
  → 服务端 Rust worker → HTTPS api.siliconflow.cn
```

客户端生成每安装一份设备私钥，Unix 文件必须为本人所有的普通单链接私有文件。密钥原子写入；Windows 目录应沿用用户私有目录 ACL。私钥不是硅基流动 Key；Token 仅存内存、服务端仅存其 SHA-256。签名绑定方法、路径、原始正文哈希、时间、nonce 和 Token 哈希。60秒时间窗口、事务性 nonce 唯一约束阻止重放；撤销立即使已有会话失效。签名协议是本项目的固定协议，并非声称兼容标准 DPoP。

这保护被动监听与“只有 Token”的盗用。控制设备进程的人仍可取得该设备私钥；匿名注册不能证明唯一真人，换身份可能绕过设备额度，因此全局预算必须保留。服务端操作系统、发布凭据、旧发行 Key 仍需各自保护。

Rust 上游使用内置公共 CA 的 rustls，固定 HTTPS 端点且禁止重定向，没有启用 native-certs 或 key logging。本次假证书代理测试设置 `SSL_CERT_FILE` / `SSLKEYLOGFILE` 后仍被拒绝，解密请求字节数为 0。它不构成对调试器、内存 hook 或已受信公共 CA 失陷的保证。

## 部署

使用 `deploy/sponsored-relay/compose.yaml`，仅在明确的生产机器执行：

1. DNS A/AAAA 指向服务器，开放 80/443；不要暴露 8430。IPv6 只有实际可达时才发布。
2. 在服务端准备与当前 Core 兼容、独立验签的 Linux SDK。将解包目录及文件交给 UID/GID 10001，目录和可执行文件 0700，其余 0600。它只挂载在服务器，不放进公共镜像或客户端包。
3. 设置 `OBC_RELAY_DOMAIN`（纯域名）和 `OBC_RELAY_SDK_DIR`（上述绝对路径），在仓库运行：

```bash
docker compose -f deploy/sponsored-relay/compose.yaml up -d --build
docker compose -f deploy/sponsored-relay/compose.yaml ps
```

Caddy 与 worker 共用网络命名空间；worker 只监听 loopback，Caddy 负责证书申请/续期。只信任来自 loopback 代理的 forwarded header；不要在前面随意添加 CDN/代理而不重新核对真实 IP 信任链。应用不记访问日志、提示词、Token 或上游响应体。SQLite state、Caddy 证书均持久化。

服务器共享 worker 默认 12，可用 `OBC_RELAY_WORKERS` 设置为 2–32，ASGI 固定单 worker；单设备最多 3 路，超载返回 429，不形成无限队列。现有客户端仍是 1前台+2后台。共享配额通过 SQLite `BEGIN IMMEDIATE` 原子预留，进程重启不清空；取消、失败或用量不明不退款。按 UTF-8 字节、固定 prompt 和最大输出保守预留，可能早于供应商实际 Token 计费达到上限。

当前初始保护界限：全局 300 RPM、20,000 请求/滚动24小时、20M Token/滚动24小时、300M Token/滚动31天；单设备 30 RPM、1,000 请求/24小时、1M Token/24小时、10M Token/31天。每 IP 每24小时最多注册5个新身份、全服务每天1000个，合法签名证明最多120个/约2分钟。**这是初始工程上限，不是供应商授权证明或9000用户容量承诺**；生产上线需依据真实授予的额度和服务器压力验证调整 `RelayStore` 配置。共享出口/NAT 会共用注册限制。匿名身份更换/DDoS仍可能影响服务可用性。

设备撤销不需要供应商 Key：

```bash
docker compose -f deploy/sponsored-relay/compose.yaml exec relay \
  python -m openbiliclaw.sponsored_relay --revoke-device DEVICE_PUBLIC_KEY_HEX
```

## 客户端与发布顺序

试部署可在受控客户端配置 `[llm.sponsored].relay_url = "https://实际域名"`。两模型、云端 BGE、本地 Ollama 选择保持同一路由语义；本地 SDK 不作为中继的隐式兜底。手工配置路由不会删除安装包内已有 Key。

生产闭环顺序：部署并用有效公开证书验证 → 双模型/工具/向量/并发/断流/撤销验收 → 将已核对地址写入 `OFFICIAL_RELAY_URL` → 发布不带 Key SDK 的新主应用包和镜像 → 验证安装后初始化及升级 → 在硅基流动撤销旧客户端 Key。仅发布新 SDK、混淆二进制或删 GitHub 旧资产都不能使已流出的 Key 失效。不要在新入口可用前撤销旧 Key。

`stage_runtime_bundle` 和冻结入口为中继发行模式拒绝旧 SDK 混入；本地兼容发行仍严格验签 SDK。中继凭据错误提示测试连接/联系维护者，组件策略过期才提示更新，不把 429 当作 Key 过期。

## 元信息恢复

```bash
python -m openbiliclaw.storage.metadata_repair /path/to/openbiliclaw.db
python -m openbiliclaw.storage.metadata_repair /path/to/openbiliclaw.db \
  --apply --backup /private/path/metadata-before.db
```

同平台同内容 ID 的已有记录只补空字段；存在冲突不任选一个，历史相对日期不移植，评分、业务状态和行为保持原值。备份必须是新路径，使用 SQLite 一致性备份。来源根本未提供的字段不能凭标题、抓取时间或模型猜测；需要后续真实来源再次提供证据，不能通过造零值“消除缺失”。

## 来源与验收

- [OWASP TLS guidance](https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Security_Cheat_Sheet.html)：TLS 机密性/端点认证边界。
- [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)：默认不信任传入 forwarded headers，流式 flush 与取消传播。
- [本次验收](testing/2026-11-06-relay-delivery.md)。公开服务器证书/设备跨公网连接尚未验收，不能用本机测试替代。
