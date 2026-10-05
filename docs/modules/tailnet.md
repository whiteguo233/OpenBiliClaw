# 应用内 Tailnet 模块

> Python 生命周期：`src/openbiliclaw/runtime/tailnet_supervisor.py`；Go helper：
> `cmd/openbiliclaw-tailnet/`；配置见 [`[tailnet]`](config.md#tailnet)。

## 定位

应用内 Tailnet 是默认关闭的私网远程入口。电脑端通过独立 Go helper 内嵌 `tsnet`，以普通
用户进程加入用户自己的 tailnet，再把 tailnet 内的 HTTP / WebSocket 请求反向代理到
`127.0.0.1:<本次启动的有效 API 端口>`。它不安装系统级 Tailscale、不启动 `tailscaled`、
不创建全局 VPN，也不要求管理员权限。

`OpenBiliClaw-mobile` 的 **Android / iOS 原生移动 App** 已内嵌自己的 `tsnet` 节点，因此受支持
的远程链路是：

```text
OpenBiliClaw-mobile Android / iOS App（内嵌 tsnet）
  → 用户自己的 tailnet（WireGuard 加密）
  → 电脑端 OpenBiliClaw helper（内嵌 tsnet，端口 = effective server port）
  → http://127.0.0.1:<effective server port>
  → 同一个 FastAPI 应用
```

电脑与手机必须加入同一个 tailnet，并由该 tailnet 的 ACL / grants 允许互访。首版只监听
tailnet 私网，不配置 Tailscale Funnel 或 Serve，也不生成公网 URL。浏览器扩展对远程明文
HTTP endpoint 另有权限与安全限制；本模块首版只支持上述 Android / iOS 原生 App，
`OpenBiliClaw-mobile` 的 Web、Linux、macOS、Windows Flutter 构建不在应用内 tsnet 支持范围。
也不能把 `http://100.x.y.z` 或 MagicDNS HTTP 当成扩展的承诺入口。

## 已实现功能

| 能力 | 状态 | 约束 |
|---|---|---|
| 默认关闭 | ✅ | `[tailnet].enabled=false`；未启用时不创建状态目录、不发现或启动 helper |
| 应用自带节点 | ✅ | 桌面安装包内置当前平台 helper；电脑无需安装或全局开启 Tailscale |
| 源码构建 | ✅ | `openbiliclaw tailnet build-helper` 使用 Go 1.26.6 构建并安装到本机数据目录 |
| 身份持久化 | ✅ | 节点私钥与 tsnet 状态保存在 `data/tailnet/`；disable 后保留，下一次启用复用身份 |
| 首次交互登录 | ✅ | helper 发出 `needs_login` 后 CLI / 桌面入口展示并打开 Tailscale 登录 URL |
| 图形化配置 | ✅ | 桌面 Web 与浏览器插件的「设置 → 通用」可开关、改节点名、查看脱敏状态并提交一次性入网凭据 |
| 非交互注册 | ✅ | 支持 Auth Key，或 OAuth Client Secret + 已授权设备 tag；可由本机设置页私有暂存，也可从父进程环境一次性注入 |
| REST / WebSocket 代理 | ✅ | helper 监听当前启动入口的有效 server port，并固定转发到同机同端口的 `127.0.0.1` |
| 真实来源保护 | ✅ | 丢弃客户端提供的 `Forwarded` / `X-Forwarded-*` / `X-Real-IP`，再由 tsnet peer 重建转发链 |
| 最佳努力启动 | ✅ | helper 缺失或登录失败只降级远程入口，本机 API 继续启动并给出诊断 |
| 私网限定 | ✅ | 无 Funnel、无 Serve、无公网监听或自动端口映射 |
| 状态诊断 | ✅ | 最近一个脱敏 JSONL 事件原子写入 `data/tailnet/status.json`；CLI 分开显示配置端口与最近监听端口，MagicDNS URL 使用最近端口 |
| 最小化 helper 构建 | ✅ | 固定使用 `ts_omit_logtail,ts_omit_webclient`，移除自动诊断日志上传与未使用的 Tailscale 管理 Web UI |
| 系统代理感知 | ✅ | macOS 上启动 helper 时把系统 HTTP/HTTPS 代理物化为子进程环境变量，并保持 `localhost` / `127.0.0.1` / `::1` 在 `NO_PROXY`，避免 TUN / 系统代理下 control plane 被假 IP 劫持而卡在 `starting` |

## 启用

### 桌面安装包

桌面安装包已经带 helper。在运行后端的电脑上打开桌面 Web，或打开连接到该本机后端的浏览器
插件，进入「设置 → 通用 → 应用内 Tailnet 远程访问」：

1. 打开 Tailnet 开关，确认电脑节点名；
2. 选择下面任一种入网方式；
3. 保存后**完整退出并重启** OpenBiliClaw。

设置页会显示 `待重启 / 已暂存 / 等待登录 / 已连接 / 失败` 等脱敏状态；不会回显登录 URL、
凭据或错误详情。若设置页不可用，也可完整退出应用后手工编辑运行目录的配置：

- macOS：`~/OpenBiliClaw/config.toml`
- Windows：`%USERPROFILE%\OpenBiliClaw\config.toml`

```toml
[tailnet]
enabled = true
hostname = "openbiliclaw-host"
```

未填写入网凭据时，重新启动后会自动打开 Tailscale 登录页。桌面包可在「查看运行日志」以及
`data/tailnet/status.json` 查看最近状态；要关闭时把 `enabled` 改回 `false` 并再次完整重启。
如果同目录 `config.local.toml` 已在 `[tailnet]` 中定义相应字段，它会覆盖 base
`config.toml`，应直接修改 local 文件。

macOS 主应用仍保持 **10.15+** 的兼容目标；但 Go 1.26.6 生成的 Tailnet helper 实测最低
`minos=12.0`。启动前会单独检查这一能力：macOS 10.15 / 11 上本机 Web、推荐等应用功能照常，
只有应用内 Tailnet 不可用；这不表示整个 OpenBiliClaw 的最低系统版本升到 12。

### 三种入网方式

| 方式 | 设置页填写 | 适用场景 |
|---|---|---|
| 浏览器登录 | 凭据留空 | 最简单；首次重启后在电脑浏览器确认节点 |
| Auth Key | `tskey-auth-…`，tag 可留空 | 与移动端当前的 Auth Key 入网方式一致；适合无需网页确认的自动注册 |
| OAuth Client Secret | `tskey-client-…` + 至少一个 `tag:name` | 适合为 OpenBiliClaw 创建专用、最小权限的自动注册身份 |

OAuth 方式需要在 Tailscale 管理后台创建专用 OAuth Client，授予 `auth_keys` 写权限，并让它
获准使用填入设置页的设备 tag（例如 `tag:openbiliclaw`）。helper 会把 OAuth Secret 解析成
**持久、预授权、tag-owned** 的电脑节点，而不是默认的临时节点。OAuth Client Secret 本身是
长期高权限凭据：建议专用、最小权限，用完后按需要在 Tailscale 后台撤销；设置页中的“单次”
只表示 OpenBiliClaw 在本机暂存一次，并不改变该 Secret 在 Tailscale 控制面的有效期。

设置页提交的凭据是 API **write-only** 字段，只允许真实 loopback 连接上的桌面 Web 或扩展来源
写入。它原子暂存为 `{data_dir}/tailnet/.bootstrap-credential.json`；POSIX 权限为 `0600`，
目录为 `0700`，不写 `config.toml`，`GET /api/config` 只返回是否已暂存。下一次启动把
bootstrap JSON 刷入 helper stdin 后，**仅当 helper 进入 `ready`（入网成功）才删除该文件**；
若 helper 卡在 `starting`、失败或尚未接受 stdin，文件会保留供下次重试，避免一次性凭据被提前消耗。
禁用 Tailnet 或在设置页勾选清除，也会删除待用凭据，但不会删除已建立的节点身份。

### 源码 / 一句话安装

源码安装先在仓库 checkout 中构建 helper，再用 CLI 开启：

```bash
go version  # 需要 Go 1.26.6
openbiliclaw tailnet build-helper
openbiliclaw tailnet enable --hostname openbiliclaw-host
# 重启 openbiliclaw start / serve-api
openbiliclaw tailnet status
```

`hostname` 是 1–63 字符的单个 DNS label，会转成小写；不能填写带点的完整域名。Tailnet
没有第二个端口配置：helper 跟随当前启动入口最终传给 server 的有效端口，通常是
`[api].port`；`openbiliclaw start --port ...`、`openbiliclaw serve-api --port ...` 或桌面入口
`OPENBILICLAW_PORT` 会让本次监听使用覆盖值。源码 CLI 的 `enable` / `disable` 与桌面包的手工配置都只持久化意图，需完整
重启后改变 helper 生命周期；关闭不会删除节点身份。
`enabled` / `hostname` 的有效优先级是显式环境变量
`OPENBILICLAW_TAILNET_ENABLED` / `OPENBILICLAW_TAILNET_HOSTNAME` >
`config.local.toml` > `config.toml`。源码 CLI 会检测环境或 local 的字段级覆盖：若本次修改会
被更高层遮蔽，它会非零退出并提示修改真实来源。通用配置保存会保留 base 中被覆盖的 Tailnet
原值，不把环境或 local 的有效值固化进 `config.toml`。

由于 helper 的 upstream 有意固定为 loopback，`[api].host` 必须是 `127.0.0.1`、`localhost`
或 `0.0.0.0`；只通过本机与 Tailnet 使用时推荐 `127.0.0.1`。若 API 绑定某个特定网卡 IP，
helper 会因无法连接 `127.0.0.1` 而降级，启动入口给出警告，但本地 API 不会随之退出。

无人值守环境也可在**启动 OpenBiliClaw 的父进程**中提供 Auth Key：

```bash
export OPENBILICLAW_TAILNET_AUTH_KEY='tskey-auth-...'
openbiliclaw start
```

Supervisor 从父环境取出该值后，通过 stdin 的一次性 bootstrap JSON 交给 helper，并从 child
环境删除所有 auth-key 形态变量；helper 参数、JSONL 事件和 `status.json` 都不含该值。环境变量
本身仍受 shell / 服务管理器的秘密管理边界约束，应使用短期、最小权限、可撤销的注册 key。
也可把 `OPENBILICLAW_TAILNET_AUTH_KEY` 设置为 OAuth Client Secret，同时提供逗号分隔的
`OPENBILICLAW_TAILNET_ADVERTISE_TAGS=tag:openbiliclaw`。环境输入优先于设置页待用凭据。
这些入网环境变量与 `OPENBILICLAW_TAILNET_HELPER` 都是 runtime-only 进程控制，不是配置字段，
不参与 TOML 合并，也不会被通用配置保存。

## 生命周期与协议

`TailnetSupervisor` 与 API server 同生共死：启动时发现 helper、创建权限收紧的状态目录、
优先读取环境输入，否则读取设置页暂存凭据，发送 protocol v1 bootstrap，并在 helper 进入
`ready` 后消费删除暂存文件（未 ready 时保留供下次重试），然后持续读取 stdout JSONL。
关闭时先关闭 stdin，让 helper 正常
退出；超时后才 terminate / kill，避免留下孤儿节点进程。主要事件为：

| 事件 | 含义 |
|---|---|
| `starting` | helper 已进入启动流程 |
| `needs_login` | 需要用户打开 `auth_url` 完成首次节点登录 |
| `ready` | 返回 `dns_name`、Tailnet `ips` 与监听 `port` |
| `error` | 远程入口不可用；敏感字段和值在持久化与回调前脱敏 |
| `stopped` | 正常关闭或自检完成 |

Helper 发现顺序为显式 `OPENBILICLAW_TAILNET_HELPER` 覆盖、冻结安装包资源、
`data/bin/`、源码构建目录和 `PATH`。该覆盖变量只适合开发 / 诊断，不是普通用户配置面。
`openbiliclaw tailnet status` 的“配置端口”来自当前有效 `[api].port`，“最近监听端口”来自最近
一次合法 runtime 事件；有 ready 记录时，MagicDNS URL 使用后者。因此用 `start --port` /
`serve-api --port` 覆盖端口后，两行不同是预期结果，status 文件也只是最近事件而不是在线探活。

## 安全边界

- Tailnet ACL / grants 是第一层设备访问控制；仍建议在本机 Web 设置中开启应用密码，尤其是
  多人 tailnet。源码安装也可执行 `openbiliclaw set-password`。Tailnet 开启但应用密码关闭时，
  启动入口会明确提醒。
- helper 只连接固定 loopback upstream，不接受用户提供的目标 URL，不会成为开放代理。
- 外部 `Host` / `Origin` 被保留给 FastAPI 的同源、CSRF 与 DNS-rebinding 检查；伪造的转发头
  被移除并用 tsnet peer 地址重建，所以请求不会因 loopback 反代而获得“本机免登录”。
- Tailscale 协调服务会处理节点身份、Tailnet 地址和连通性元数据；业务 HTTP payload 在
  tailnet 的 WireGuard 链路中加密。流量可能经 DERP 中继，但中继不把它变成 OpenBiliClaw
  运营的云服务。
- 所有受支持构建都从 `cmd/openbiliclaw-tailnet/build-tags.txt` 读取
  `ts_omit_logtail,ts_omit_webclient`：前者从二进制移除 Tailscale 自动诊断日志上传，因此 helper
  不向 `log.tailscale.com` 上传诊断日志；后者移除本功能不用的 Tailscale 管理 Web UI。代价是
  上游支持无法取得这部分自动日志，排障需依赖 OpenBiliClaw 本地日志与脱敏 status。这不取消
  上述协调控制面或可能的 DERP 流量及其元数据处理。
- `data/tailnet/` 包含设备节点私钥，应像本机凭据一样保护。它被刻意排除在 `.obcbackup`
  之外，导入也保留目标机自己的目录，避免克隆同一节点身份到另一台电脑。`data/bin/` 的任意
  大小写变体同样禁止进入导出 / 导入；应用迁移时只从目标机保留 exact 当前平台 helper 文件名，
  且 POSIX 下只接受原本已有执行位的普通非 symlink 文件，再恢复 `0700`，迁移包不能带入或把
  普通文件升级成可执行 helper。目标机保留的 `certs/`、`autostart/`、`tailnet/` 若含任何嵌套
  symlink，迁移会 fail closed，避免跟随链接复制数据目录之外的内容。
- OAuth Client Secret 可反复创建带指定 tag 的节点，权限通常高于单个设备身份；即使本机暂存
  文件已经消费删除，也应把 Tailscale 后台中的原始 Secret 当作长期秘密管理。普通 Auth Key
  同样应采用短期、预授权、最小 tag 权限并及时撤销。

## 公开 Python API

| API | 说明 |
|---|---|
| `TailnetConfig` | 根 `Config.tailnet` 的 typed 配置，包含 `enabled` / `hostname` |
| `normalize_tailnet_hostname()` | 规范化并严格校验单个 DNS label |
| `TailnetSupervisor` | 发现、启动、监控、脱敏记录并关闭一个 helper 进程 |
| `stage_tailnet_bootstrap()` / `clear_tailnet_bootstrap()` | 原子暂存或清除下一次启动使用的 write-only Auth / OAuth 凭据 |
| `normalize_tailnet_bootstrap_credential()` | 只接受 `tskey-auth-…` / `tskey-client-…`，拒绝其它秘密形态 |
| `normalize_tailnet_advertise_tags()` | 规范化、去重并限制 OAuth 设备 tag |
| `read_tailnet_status()` | 只投影 event / DNS / IP / port 等安全字段，不暴露登录 URL 或错误消息 |
| `find_tailnet_helper()` | 按受支持顺序定位当前平台 helper |
| `start_tailnet_if_enabled()` | 配置关闭时无副作用返回 `None`；开启时启动 supervisor |
| `tailnet_runtime()` | 为非标准入口提供与 context 同生命周期的 supervisor |
| `build_tailnet_helper()` | 从仓库 Go module 构建并安装源码形态所需 helper |
| `TailnetSupervisorError` 及子类 | helper 缺失、启动失败和意外退出的诊断边界 |

Go helper 的 flags 和 stdout JSONL 是 Python supervisor 的内部 protocol v1，不是面向第三方的
稳定 CLI API。完整依赖许可由 `python scripts/generate_tailnet_notices.py` 生成到仓库根目录
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)；`--check` 会校验当前依赖图 / 构建标签
与 notice 一致，CI 与桌面构建在陈旧或缺失时失败，notice 也会随桌面包分发。

## 部署边界

- macOS / Windows 桌面安装包：构建流水线使用 Go 1.26.6 生成并随包携带 helper；用户优先在
  桌面 Web / 本机浏览器插件的通用设置中开关和入网，手工 `config.toml` 仍是回退路径。macOS helper
  要求 12+，旧 macOS 只降级此远程入口，不影响仍以 10.15+ 为目标的主应用。
- 源码 / AI 一句话安装：Python 安装不自动编译 Go；用户明确需要 Tailnet 时运行
  `openbiliclaw tailnet build-helper`。
- Docker：首版镜像不内置 helper。容器用户继续使用 LAN、Caddy 公网 HTTPS 或自管 TLS；
  不要仅设置 `[tailnet].enabled=true` 并假定镜像内已有可执行文件。
