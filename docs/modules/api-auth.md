# API Auth Module（局域网密码门禁）

## 概述

`src/openbiliclaw/auth_core.py` + `src/openbiliclaw/api/auth.py` 实现局域网 / 远程访问的**可选密码门禁**。可信 loopback 默认免登录；远程 Web 使用密码会话，远程浏览器扩展使用默认关闭的设备密钥认证。

- `auth_core.py`：**纯标准库**实现 —— scrypt 密码哈希、HMAC 无状态签名 token、稳定密码指纹、反向代理 / Origin 解析与归一化。无任何第三方依赖。
- `api/auth.py`：FastAPI 集成 —— `AuthGate`、HTTP 中间件、`/api/auth/*` 路由、cookie / CSRF 处理、登录失败限流。在 `create_app()` 内于 degraded-mode guard 之后注册（更外层、最先执行）。
- 配置见 [`[api.auth]`](config.md#apiauth)（`ApiAuthConfig`）。撤销纪元 `auth_epoch` 与密码指纹存 SQLite `auth_state` 表，不在 config。
- 完整设计与对抗式 review 记录见 [`docs/plans/2026-05-30-web-password-auth-design.md`](../plans/2026-05-30-web-password-auth-design.md)。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 总开关 | ✅ | `[api.auth].enabled=false` 时中间件直接放行；`true` 且无密码视为配置错误（blocking）。 |
| 信任模型 | ✅ | `trust_loopback=true`（默认）下，loopback 且无 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` 头的请求免登录；带转发头的 loopback fail-closed（要求登录），防同机反代绕过。 |
| 应用内 Tailnet 反代边界 | ✅ | Go helper 丢弃客户端自带的全部转发头，再从 tsnet peer 重建 `X-Forwarded-For`，同时保留外部 Host / Origin。FastAPI 收到的是“loopback 代理 + 远端来源”，不会继承本机免登录；Tailnet ACL 是外层设备门，仍建议开启应用密码。 |
| 反代真实 IP 解析 | ✅ | `resolve_client_ip()`：仅当直接对端命中 `trusted_proxies` 才采信 `X-Forwarded-For`，并**从右向左**穿越受信代理链取第一个非受信 IP；任何缺失 / 畸形 / 伪造 loopback 都 fail-closed，绝不 500 / fail-open。 |
| 无状态 token | ✅ | `sign_token()` / `verify_token()`：`b64url(payload).b64url(HMAC_SHA256)`，payload 含 `v/iat/ep`（限时还含 `exp`）。校验常量时间比对 + 过期检查 + `ep >= 当前 auth_epoch`。 |
| 记住登录 | ✅ | `session_ttl_hours=0`（默认）签发无 `exp` token + 超长 cookie `Max-Age`，关浏览器 / 重启后端都不失效。 |
| HttpOnly cookie 凭据 | ✅ | 默认下发 `obc_session`（`HttpOnly; Path=/; SameSite=Lax`，host-only，`Secure` 仅当对外协议为 HTTPS），同源 fetch / `<img>` / WebSocket 自动携带；前端永不持有 token。 |
| 跨源 Bearer 逃生通道 | ✅ | 仅当 Origin 命中 `allowed_bearer_origins` 且 `ttl>0`，登录才在 body 返回 token（`sessionStorage`）；同源 / 缺 Origin 一律 cookie-only（后端不变量）。 |
| CSRF 防护 | ✅ | cookie 鉴权的非安全方法（POST/PUT/PATCH/DELETE）强制 `Origin==Host`（`same_origin()`）+ 头 `X-OBC-Auth: 1`；普通安全方法读取豁免，但十个 state-changing GET `/next-task` claim routes（B 站 / XHS / 抖音 / YouTube / X / 知乎 / Reddit / Linux.do / V2EX / 微博）与 `/api/recommendations` 在精确路径集合中强制 `X-OBC-Auth`，因为领取会 claim+lock / serve 会 bootstrap 写推荐历史；Bearer / 可信本机豁免；WebSocket 握手按 `same_origin` 校验。`tests/test_api_auth.py` 对「已注册的 claim 路由集合」与 `_CSRF_GET_EXACT` 做集合相等断言，新增来源漏登记会直接失败。 |
| 撤销纪元 | ✅ | `auth_epoch` 存 SQLite `auth_state` 单行，跨进程事务原子自增、验签实时读。改密 / `--logout-all` / `--rotate-secret` / `POST /api/auth/logout?all=true` 都通过它撤销所有设备。 |
| 改密即撤销（全通道） | ✅ | `password_fingerprint`（`HMAC(session_secret,"pw:"+明文)` 或 `"ph:"+hash`）在启动 / 重载时比对，变化即 `auth_epoch += 1`；scrypt 随机盐不会造成误撤销，永不过期登录跨重启不被误撤销。 |
| 登录失败限流 | ✅ | 进程内按真实客户端 IP 计数，15 分钟内失败 ≥5 次锁 15 分钟，`POST /api/auth/login` 返回 429。可信本机不计入。 |
| WebSocket 门禁 | ✅ | http 中间件不覆盖 ws scope，故 `/api/runtime-stream` 在 `accept()` 前用 `authorize_websocket()` 校验（CSWSH：Origin==Host 或允许的 Bearer origin + token）；连接建立后**每次发送前 + 15s 看门狗**重读 `auth_epoch`，撤销即关闭已建立连接。 |
| 撤销 / 损坏 fail-closed | ✅ | 启动指纹 reconcile 失败 → `AuthGate.reconcile_ok=False`，所有非本机 token 鉴权 fail-closed 直至下次成功；`auth_epoch` 行损坏（非整数）→ `get_auth_epoch` 抛错而非视作 0，中间件 fail-closed，避免复活旧 token。 |
| 秘密不外泄 | ✅ | `session_secret` / `password_hash` 永不经 `GET /api/config` 返回（即便 `reveal_keys=true`）。 |
| env-managed 写保护 | ✅ | `load_config` 给 env 优先级，故 `save_config` 若把内存 Config 整段 `[api.auth]` 写回，会把 env 值烤成陈旧字面量。**保护下沉到 `save_config`**：凡有 `OPENBILICLAW_API_AUTH_*` 在场，被覆盖字段改用磁盘原值渲染、磁盘无值则省略整行——覆盖启动 secret 生成 / `PUT /api/config` / cookie 同步等所有写路径，而非仅 admin / CLI。 |
| config.local 遮蔽检测 | ✅ | `config.local.toml` 合并盖在 `config.toml` 之上（local 胜），写 `config.toml` 的改动会被它悄悄盖回。`/api/auth/admin` 在 `_save` 后重载有效合并配置校验改动确已生效，被遮蔽则回滚并 `409 shadowed`；CLI `set-password` 写盘前用 `config_local_auth_keys()` 检测并拒绝。 |
| 撤销判定（指纹漂移） | ✅ | `revoke_and_set_fingerprint` 在事务内 CAS 比对指纹：除 enabled 开关 / 显式改密的 `force_bump` 外，新指纹 ≠ 已存即 bump，堵住「后台 `set-password` 改盘上 hash → admin 无密码热发布却不撤销」窗口；首次写入（无既存指纹）不 bump。 |
| 扩展 origin 独立路径 | ✅ | `is_extension_origin()` 识别 `chrome-extension://` / `moz-extension://`，`pick_token()` 和登录端点对扩展 origin 走独立分支，不依赖 `allowed_bearer_origins`。 |
| 设备密钥摘要 | ✅ | `extension_access_keys` 只保存 `key_id:sha256(secret)`；完整高熵密钥只由 CLI 显示一次。总开关 `extension_access_enabled=false` 默认关闭。 |
| 短会话交换 | ✅ | 限流的 `POST /api/auth/extension-token` 验证设备密钥后签发 `1..168h` 会话；错误不泄露 key ID 是否存在。 |
| 传输边界 | ✅ | 普通扩展 HTTP 使用 `Authorization: Bearer`，URL 不含 token；仅 WebSocket 和图片代理允许扩展 Origin 携带短会话 query token。popup saved/config 请求的单个 Abort deadline 覆盖初次短会话交换、401 强制换票与受保护请求，认证 fetch 接收同一 AbortSignal。 |
| 撤销 | ✅ | `ext-key revoke <key-id>` 删除摘要并 bump 全局 `auth_epoch`，所有 Web / 扩展会话立即失效；失败时回滚配置。 |
| 远程 endpoint 权限 | ✅ | 扩展按 `scheme://host/*` 请求跨浏览器可用的最小 host 权限；权限 API 不能可移植地限定端口，实际请求仍固定配置端口。公网 host 强制 HTTPS，HTTPS 自动派生 WSS。 |

真实 LAN 浏览器链路在物理网卡地址上验证。手动接入其他 Docker bridge / 外部反向代理时仍
必须显式配置 `trusted_proxies`，后端不会自动信任默认网关。官方 `docker-compose.https.yml`
不放宽该列表：Caddy 与后端共享 network namespace，Uvicorn 通过
`FORWARDED_ALLOW_IPS=127.0.0.1` 只信任 loopback hop，并先把 scope client/scheme 归一化为
公网客户端与 HTTPS；auth gate 因此继续按远程客户端执行密码门禁，登录 cookie 带 `Secure`。
该路径已通过真实 Compose、Caddy TLS、浏览器登录、扩展设备令牌和 WSS 请求验证；公网 ACME
签发仍需在具有真实 DNS 与入站 `80/443` 的部署主机上完成。

应用内 Tailnet 是第三条、默认关闭的私网传输边缘：`tsnet` helper 只在用户自己的 tailnet
监听与 `[api].port` 相同的端口，再固定转发到 `127.0.0.1:<port>`。它不启用 Funnel / Serve，
也不把端口暴露到公网。helper 不信任入站 `Forwarded`、`X-Forwarded-*` 或 `X-Real-IP`，全部
清除后用实际 tsnet peer 重建 XFF；因此 AuthGate 的 loopback fast path 必定 fail-closed 为远程
请求。不要为了“让 Tailnet 免登录”关闭该行为。多人 tailnet 或 ACL 较宽时应在本机 Web
设置中开启应用密码做第二层控制；源码安装也可执行 `openbiliclaw set-password`。移动 App 按
普通远程 Web/API 客户端登录。

浏览器扩展仍受自己的 remote endpoint、HTTPS 与设备 key 契约约束。首版 Tailnet 入口只服务
已内嵌 tsnet 的 `OpenBiliClaw-mobile` Android / iOS 原生 App，不包括其 Web / Linux / macOS /
Windows Flutter 构建；不能由“Tailnet 内 HTTP 已加密”推导出扩展一定接受
`http://100.x` / MagicDNS endpoint。完整链路见[应用内 Tailnet 模块](tailnet.md)。

## 端点

`/api/auth/{status,login,logout}` 由 `register_auth_routes()` 注册；`/api/auth/admin` 在 `create_app()` 内定义（需 `_CONFIG_SAVE_LOCK` + 配置快照回滚）。门禁挡所有其他 `/api/*`（含 `/api/runtime-stream` WS 与 `/api/image-proxy`）；`/api/health`、`/api/qr-info`、`/api/auth/admin` 与静态壳（`/`、`/m`、`/web`、`favicon`）保持公开。`/api/qr-info` 只返回手机版二维码需要的 `lan_ip`，不读取画像或探测 embedding，避免扫码入口继承 readiness probe 延迟。`admin` 在白名单内由 handler 自行强制可信本机，从而对任何非本机调用方（远程或跨源 loopback）统一返回 `403 local_only`，而非泄露是否带 token 的 `401`。

| 方法 & 路径 | 鉴权 | 请求 | 响应 |
|------------|------|------|------|
| `GET /api/auth/status` | 公开 | — | `{enabled, authenticated, trust_loopback, env_managed, can_manage}`；SPA 启动先调，据此决定是否显示登录页；`can_manage`=调用方为可信本机且非 env 管理（插件据此显示开关） |
| `POST /api/auth/admin` | **仅可信本机** | `{enabled, password?, session_ttl_hours?}` | 本机（浏览器插件 / 本机 UI / CLI）开关门禁 + 设/改密码，**热生效免重启**。开启需带密码(否则 400)。写入顺序为**先持久化 config.toml（快照可回滚）→ 原子撤销（`revoke_and_set_fingerprint`：bump epoch + 写指纹同一 `BEGIN IMMEDIATE` 事务）→ 再发布到运行期门禁**；任一步失败即回滚并 `503`，绝不留下「新密码已撤销旧会话却未持久化」的半状态，两步之间崩溃由启动指纹 reconcile 自愈。远程会话（即便已登录）→`403 local_only`；任一 `OPENBILICLAW_API_AUTH_*` env 覆盖在场→`409 env_managed`；改动被 `config.local.toml` 遮蔽（写后重载校验失败）→回滚并 `409 shadowed`。供扩展弹窗的「局域网访问密码」开关用 |
| `POST /api/auth/login` | 公开（限流） | `{password}` | Web 专用；扩展 Origin 返回 `403 origin_forbidden`。同源设置 HttpOnly cookie，允许列表内跨源可返回限时 Bearer |
| `POST /api/auth/extension-token` | 公开（限流） | `{key}` | 仅在 auth 与扩展设备访问都开启时，用设备密钥换短会话；关闭 `403`，无效密钥 `401`，锁定 `429` |
| `POST /api/auth/logout` | 公开·幂等 | — | `{ok:true}` + 清 `obc_session`；仅清本机 cookie、不改服务端状态（让失效 token 也能清 cookie） |
| `POST /api/auth/logout?all=true` | 需已登录 + CSRF | — | `{ok:true}`；`auth_epoch += 1`，所有设备立即失效 |

> 因鉴权 cookie 无效返回的 401 也附带 `Set-Cookie` 清除 `obc_session`，让失效凭证自动脱落。中间件直接返回的 401/403 会手动补 CORS 头，避免跨源前端读不到。

## 公开 API

```python
from openbiliclaw.auth_core import (
    hash_password, verify_password, password_fingerprint,
    sign_token, verify_token, token_expires_at,
    resolve_client_ip, is_trusted_local,
    effective_scheme_host, same_origin, origin_allowed_for_bearer,
    is_extension_origin, generate_extension_access_key,
    verify_extension_access_key, extension_access_key_ids,
)
from openbiliclaw.api.auth import (
    AuthGate, make_auth_middleware, register_auth_routes, authorize_websocket,
    ensure_session_secret, reconcile_password_fingerprint,
)
```

- `auth_core` 函数无副作用、不依赖 FastAPI，便于单元测试。
- `is_extension_origin()` 只识别浏览器 Origin；身份来自设备密钥而非可伪造的 ID。设备密钥生成、摘要验证与 ID 列举均为纯函数。
- `api/auth.py` 的 `AuthGate` 持有运行期门禁状态（config + DB 句柄），中间件 / 路由都通过它取 token、验签、读写 `auth_epoch`。
- `ensure_session_secret()` 在首次启用且 `session_secret` 为空时生成并写回 config；`reconcile_password_fingerprint()` 实现「改密即撤销」的指纹比对与按需 bump。

## 数据库

新增 SQLite 表 `auth_state(key TEXT PRIMARY KEY, value TEXT)`（位于 `data/openbiliclaw.db`），至少两行：

- `('auth_epoch', <int>)` —— 单调撤销计数；缺失视为 `0` 并惰性插入，读取 / DB 异常时 fail-closed。
- `('password_fingerprint', <str>)` —— 稳定密码指纹，用于跨通道检测改密。

这不是「会话表」（无逐会话记录），只是全局计数 + 指纹，整体仍近无状态。
