# 可选 HTTPS 部署

默认的 `http://127.0.0.1:8420` 和局域网 HTTP 行为不变。按访问场景选择一个入口：

| 场景 | 推荐入口 | 证书 | 客户端地址 |
|---|---|---|---|
| 有公网域名，PC / 手机 / 远程插件访问 | `docker-compose.https.yml` + Caddy | 自动申请、续期，浏览器直接信任 | `https://obc.example.com` |
| 仅可信局域网或自管网络 | 内置 TLS Proxy / Docker `tls` profile | 本地 CA 或自有证书，客户端需手动信任 | `https://192.168.1.20:8443` |

两种模式都只增加传输入口，不替代 OpenBiliClaw 的密码门禁或扩展设备认证，也不要同时启用。

## 最简公网方案：Caddy 自动 HTTPS（推荐）

仓库提供可叠加到源码和预构建 Compose 的 `docker-compose.https.yml`。Caddy 自动完成
证书申请、HTTPS 重定向、续期及 WebSocket 反代；桌面 Web、移动 Web 和浏览器插件共用同一
域名。后端 `8420` 只绑定宿主机 loopback，公网只开放 `80/443`。Caddy 会先检查后端密码
门禁；`enabled=false` 时仅在容器 loopback 等待，不绑定 `80/443`，避免首次配置产生裸奔窗口。

### 前置条件

- 一个公网 DNS 名称，例如 `obc.example.com`，其 A/AAAA 记录指向这台服务器。只有服务器
  确实能接收公网 IPv6 时才发布 AAAA。
- 防火墙和云安全组放行 TCP `80`、TCP `443`；overlay 同时发布 UDP `443` 供 HTTP/3 使用。
- Docker Compose `2.24.4+`，因为 overlay 使用 `!override` 精确替换后端端口列表。
- 公网暴露前必须开启 Web 密码门禁；远程插件还必须启用独立的 `ext-key` 设备认证。

### 预构建镜像

```bash
mkdir -p ~/openbiliclaw && cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.https.yml

# 只填 DNS 名称，不带 https://、端口或路径
export OPENBILICLAW_DOMAIN=obc.example.com
docker compose -f docker-compose.prebuilt.yml -f docker-compose.https.yml up -d
```

### 源码构建

在仓库根目录执行：

```bash
export OPENBILICLAW_DOMAIN=obc.example.com
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

首次签发证书需要密码门禁已启用、DNS 已生效且公网 `80/443` 能到达该主机。Caddy 的证书
状态与配置目录分别持久化在 `openbiliclaw_caddy_data`、`openbiliclaw_caddy_config` volume，
重建容器不会丢失。

### 开启访问门禁

```bash
# 交互式设置 Web 登录密码
docker exec -it openbiliclaw-backend openbiliclaw set-password

# 为远程插件生成设备密钥并开启交换；完整密钥只显示一次
docker exec -it openbiliclaw-backend openbiliclaw ext-key generate
docker exec -it openbiliclaw-backend openbiliclaw ext-key enable

# CLI 写入的是持久配置；重启后端加载门禁，再重启 Caddy 重新附着共享网络命名空间
docker restart openbiliclaw-backend
docker restart openbiliclaw-caddy
```

如果只需要 PC / 手机 Web，不用运行两条 `ext-key` 命令；Web 密码仍是公网入口的硬前置。
Caddy 在密码启用前会持续打印等待提示，并且不会申请证书或监听公网端口。

然后使用：

- PC Web：`https://obc.example.com/web`
- 手机 Web / PWA：`https://obc.example.com/m/`
- 插件设置：协议选 `HTTPS`，主机填 `obc.example.com`，端口填 `443`，再填上一步生成的设备密钥

桌面 Web 的手机版二维码会保留当前 HTTPS 域名和端口；插件二维码也沿用插件配置的 HTTPS
地址，因此扫码不会再退回私网 IP 或明文 HTTP。

### 状态与排错

```bash
docker compose -f docker-compose.prebuilt.yml -f docker-compose.https.yml ps
docker compose -f docker-compose.prebuilt.yml -f docker-compose.https.yml logs openbiliclaw-caddy
curl https://obc.example.com/api/health
```

源码部署把上面命令的第一个 `-f` 改成 `docker-compose.yml`。签发失败时依次检查 DNS、云安全组、
主机防火墙，以及 `80/443` 是否被其他进程占用。不要同时启动源码 Compose 的 `tls` profile；
公网 Caddy 与 LAN TLS Proxy 是两种互斥入口。

## LAN / self-managed TLS Proxy

> 面向可信局域网或自管网络的轻量 TLS 入口。默认关闭，不是公网生产级反向代理。

只有远程浏览器策略要求 HTTPS、且你愿意在客户端安装本地 CA（或提供自己的证书）时，才需要
启用本组件。公网域名优先使用上面的 Caddy overlay。

### 访问路径与安全边界

```text
HTTP（默认）: 客户端 ───────────────→ FastAPI :8420
HTTPS（可选）: 客户端 → TLS Proxy :8443 → FastAPI :8420（本机/Compose 内网 HTTP）
```

- Web Origin 必须是 `https://`，并与请求 `Host` 的规范化 host+port 精确一致；
  `https://evil.example:8443` 不会因为端口相同而放行。
- 扩展 Origin 只接受结构合法的 `chrome-extension://` 与 `moz-extension://`。
- 无 `Origin` 的 CLI、健康检查等非浏览器调用可继续使用。
- TLS 响应中的 cookie 会补 `Secure`；重复 `Set-Cookie` 不会被合并。
- 证书下载端点只公开 `ca.crt` 与 `ca.crl`；另有不含秘密的 `/healthz`。`ca.key` /
  `srv.key` 会在代理层直接返回 404。
- HTTP/1.1 GET、POST、HEAD 与 WebSocket Upgrade 会转发；chunked **请求体**不支持。
- TLS 不替代密码门禁。局域网暴露时仍建议配置 `openbiliclaw set-password`；远程扩展
  仍需默认关闭的 `ext-key` 设备认证。

### 非 Docker 部署

```bash
# 1. 安装可选证书依赖
uv sync --extra tls
# pip 安装也可使用: pip install "openbiliclaw[tls]"

# 2. 写入持久配置；SAN 必须是客户端实际访问的 IP/hostname
uv run openbiliclaw tls-proxy enable --san 192.168.1.20 --san openbiliclaw.lan

# 3. TLS 代理只跟随 serve-api 启动
uv run openbiliclaw serve-api
```

打开：

- 桌面 Web：`https://192.168.1.20:8443/web`
- 移动 Web：`https://192.168.1.20:8443/m/`
- 扩展后端地址：`https://192.168.1.20:8443`

`openbiliclaw start` 当前不启动 TLS 入口；使用该功能时应运行 `serve-api`。TLS 已启用时，
证书解析、SSL context 或端口绑定失败会让 `serve-api` 以非零状态退出，不会静默退回 HTTP。

### Docker Compose（源码 compose）

首次启动前设置远程客户端会使用的 SAN：

```bash
export OPENBILICLAW_TLS_SAN_NAMES="192.168.1.20,openbiliclaw.lan"
export OPENBILICLAW_TLS_PORT=8443
docker compose --profile tls up -d --build
```

Compose 把 `OPENBILICLAW_TLS_SAN_NAMES` 传为代理容器内的 `SAN_NAMES`，两者都是逗号分隔的
hostname/IP 列表。`OPENBILICLAW_TLS_PORT` 同时控制宿主机映射端口和容器监听端口。
`docker-compose.prebuilt.yml` 当前不包含该 profile；预构建部署请使用成熟外部网关或切换到
源码 `docker-compose.yml`。

查看明确的证书/SAN/绑定错误：

```bash
docker compose logs openbiliclaw-tls-proxy
```

### 证书与 SAN

自动生成必须显式开启（CLI 集成与 Compose profile 已明确开启）。首次生成包含：

- 固定本机 SAN：`localhost`、`127.0.0.1`
- 配置的 `san_names` / `SAN_NAMES`
- 本地 CA、服务器证书和 CRL；RSA 2048，有效期 3650 天

**没有远程 SAN 时，自动证书只适合 localhost。** 服务即使监听 `0.0.0.0:8443`，也不
代表证书可用于局域网 IP；客户端会正确报告 hostname mismatch。

已有 `srv.crt` + `srv.key` 时代理绝不覆盖。若配置新增 SAN，而现有证书不包含它，启动会
明确失败并列出缺失项。重签步骤：

1. 停止 TLS 代理；
2. 备份 `cert_dir`（Docker 为 `openbiliclaw_certs` volume）；
3. 自有证书：签发包含全部配置 SAN 的新 `srv.crt` / `srv.key` 后原位替换；
4. 自动生成证书：把旧的 `ca.crt`、`ca.key`、`ca.crl`、`srv.crt`、`srv.key` **移到备份目录**，
   确认活动目录不再有半套 cert/key 后重启；
5. 在客户端重新信任新 CA。

只存在 cert 或 key 其中一个时，代理会 fail loudly，不会在半残目录里补写另一半。

#### 信任本地 CA

首次可用忽略校验的命令下载 CA（只下载公钥证书）：

```bash
curl --insecure https://192.168.1.20:8443/ca.crt -o openbiliclaw-ca.crt
```

- Windows：导入「受信任的根证书颁发机构」。
- macOS：钥匙串访问 → 系统 → 导入，并明确设为信任。
- Linux/Chrome：按发行版系统 CA 或 NSS 数据库流程导入。

不要复制、下载或分享 `ca.key` / `srv.key`。

### 配置与环境变量

非 Docker `Config` 的显式覆盖范围只有：

| 环境变量 | 对应配置 |
|---|---|
| `OPENBILICLAW_TLS_PROXY_ENABLED` | `[tls_proxy].enabled` |
| `OPENBILICLAW_TLS_PROXY_PORT` | `[tls_proxy].port` |
| `OPENBILICLAW_TLS_PROXY_CERT_DIR` | `[tls_proxy].cert_dir` |
| `OPENBILICLAW_TLS_SAN_NAMES` | `[tls_proxy].san_names`（逗号分隔） |

独立代理容器使用 `LISTEN_HOST`、`LISTEN_PORT`、`BACKEND_HOST`、`BACKEND_PORT`、`CERT_DIR`、
`CERT_FILE`、`KEY_FILE`、`CA_CERT_FILE`、`CRL_FILE`、`AUTO_GEN_CERTS` 与 `SAN_NAMES`。这些是
容器入口参数，不是通用 `Config` 环境变量；不要假设任意 `[tls_proxy]` 字段都能自动映射。

### 端口与客户端切换

`openbiliclaw serve-api --tls-port 9443` 可临时覆盖 TOML 端口，但只有
`[tls_proxy].enabled=true` 时才会启动。Docker 则设置 `OPENBILICLAW_TLS_PORT` 后重建/重启
profile。启用 TLS 不会自动改扩展或书签地址，也不会关闭原始 `:8420` HTTP 映射；需要
TLS-only 策略时应同时调整 Compose 端口发布或主机防火墙。把插件后端配置切到 `https`
后，手机版二维码和 loopback `/api/qr-info` 探测会沿用 HTTPS，不会向 TLS 端口发送明文 HTTP。


## 免费模型连接与抓包边界

本机 UI 默认 HTTP；跨设备应使用本页 HTTPS 或已加密私网。Rust 到硅基流动固定 HTTPS、内置公共 CA 校验、禁止重定向，不启用系统 CA 或 TLS key logging。真实伪造证书代理测试未收到解密请求。HTTPS 不防控制客户端进程的人提取其内存，故共享 Key 的长期方案是 [服务端中继](sponsored-relay.md)，不能把 Rust 混淆或证书绑定称为不可提取保证。
