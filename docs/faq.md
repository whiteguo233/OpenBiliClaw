# ❓ 常见问题（FAQ）

> 汇总安装和日常使用中最高频的问题。没找到答案可以在 [GitHub Issues](https://github.com/whiteguo233/OpenBiliClaw/issues) 提问，或加 README 里的用户交流群。

## 安装

### macOS 打开桌面安装包提示「无法验证开发者」或「未经安全验证」？

当前 Release 是 ad-hoc signed、未 notarized 的实验性预发布。先把应用拖进「应用程序」，再右键 / Control-click `OpenBiliClaw.app` →「打开」→ 在弹窗里再点「打开」；也可以到「系统设置 → 隐私与安全性」点击「仍要打开」。

### macOS 提示「OpenBiliClaw.app 已损坏，无法打开」？

通常是下载隔离属性导致。确认安装包来自本项目 [Releases](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) 后运行：

```bash
APP="/Applications/OpenBiliClaw.app"
xattr -dr com.apple.quarantine "$APP"
```

然后再次打开应用。

### Windows 安装时弹出 SmartScreen 警告？

点「更多信息 → 仍要运行」。安装包未购买代码签名证书，属预期现象。

### Firefox 安装 `-firefox.zip` 提示「未通过验证 / could not be verified」？

`-firefox.zip` 是未签名开发包，只用于 `about:debugging` 临时加载。普通 Firefox 用户请优先安装 release 里的已签名 `openbiliclaw-extension-v*-firefox.xpi`（若该版本提供）；临时加载方式见 README 的 Firefox 折叠说明。

### Chrome 应用商店的版本比 GitHub Releases 旧？

正常。商店版受审核排期影响，通常滞后几天到一两周。想第一时间拿到新功能，从 [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) 下载 zip 手动安装即可（缺点是需要手动更新）。

### 想用 Docker 部署后端？

不需要克隆源码：下载一个 compose 文件启动预构建镜像（自带 Ollama embedding sidecar），再打开 `http://127.0.0.1:8420/setup/` 完成初始化：

```bash
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
docker compose -f docker-compose.prebuilt.yml up -d
```

升级到最新版：`docker compose -f docker-compose.prebuilt.yml pull` 再 `up -d`。源码构建、代理与排查见 [Docker 部署指南](docker-deployment.md)。

## 连接与初始化

### 插件显示「后端还没开张」/ 连不上后端？

按顺序排查：

1. 后端在跑吗？桌面包看菜单栏 / 托盘图标；源码安装跑 `openbiliclaw start`。
2. 浏览器访问 `http://127.0.0.1:8420/api/health`，有 JSON 返回说明后端正常。
3. 插件默认连 `127.0.0.1:8420`；如果你改过端口，在插件设置里同步修改。
4. 后端启动后插件会在 1 秒内自动重连，不需要手动刷新。

### 初始化需要哪些前置条件？

三样：① 至少一个已登录且能拉到信号的内容平台（B 站默认勾选，可换成小红书 / 抖音 / YouTube / X / 知乎 / Reddit）；② 一个可用的 LLM provider（自己的 API Key）；③ embedding 服务（桌面包内置，其他安装方式可用 Ollama）。引导初始化会先真实验证 LLM 和 embedding 再开跑，不会硬跑出空画像。

### 不想为 embedding 单独配 API Key？

装一次 [Ollama](https://ollama.com/download)，然后运行 `openbiliclaw setup-embedding`，向导会自动拉取 `bge-m3`（约 568MB，CPU 可跑）并写入配置。桌面安装包已内置，无需额外操作。

### 手机打不开移动端 Web（`/m/`）？

1. 手机和电脑要在同一个局域网。
2. 后端要绑定 `0.0.0.0`：桌面包默认如此；源码安装检查 `config.toml` 的 `[api].host`（`0.0.0.0` = 同时监听可用的 IPv4 / IPv6，`127.0.0.1` = 仅本机）。
3. 用插件顶部手机图标的二维码打开最稳，它会优先展示电脑的 IPv4 局域网地址；没有可用 IPv4 时会回退到 IPv6，并自动生成 `http://[IPv6]:8420/m/` 格式的地址。

### 手机和电脑不在同一局域网，Android / iOS 原生 App 怎么连接？电脑要全局开 Tailscale 吗？

不需要。`OpenBiliClaw-mobile` 的 Android / iOS 原生 App 已内嵌 `tsnet`，电脑端也可以只让
OpenBiliClaw 自己成为一个 tailnet 节点，不安装系统级 Tailscale、不创建全局 VPN。该能力
不包括 `OpenBiliClaw-mobile` 的 Web / Linux / macOS / Windows Flutter 构建。

桌面安装包已内置 helper，但托盘程序首版不提供 `tailnet` CLI。先完整退出应用，编辑
`~/OpenBiliClaw/config.toml`（Windows 为 `%USERPROFILE%\OpenBiliClaw\config.toml`）：

```toml
[tailnet]
enabled = true
hostname = "openbiliclaw-host"
```

然后重新启动；首次会打开 Tailscale 登录页。建议同时在本机 Web 设置中开启「局域网访问
密码」。源码 / 一句话安装使用 CLI：

```bash
# 需要 Go 1.26.6
openbiliclaw tailnet build-helper
openbiliclaw tailnet enable --hostname openbiliclaw-host
openbiliclaw set-password  # 推荐的第二层访问控制
# 完整退出并重启 OpenBiliClaw；首次启动会打开 Tailscale 登录页
openbiliclaw tailnet status
```

让 Android / iOS App 和电脑 helper 登录同一个 tailnet，再在 App 中使用 status 显示的
MagicDNS 名称或 Tailnet IP。默认端口是 `8420`；helper 实际跟随本次 server 的有效端口，通常
为 `[api].port`，但 `start --port` / `serve-api --port` 或桌面包的 `OPENBILICLAW_PORT` 可覆盖。
`tailnet status` 会分开显示“配置端口”和“最近监听端口”，MagicDNS URL 使用后者。默认链路为
`App tsnet → tailnet → 电脑 helper → 127.0.0.1:8420`。它只在 tailnet 内可见，不开启 Funnel /
Serve，也不生成公网地址。

helper upstream 固定为 loopback，所以 `[api].host` 必须是 `127.0.0.1`、`localhost` 或
`0.0.0.0`；推荐只需本机 + Tailnet 的用户设为 `127.0.0.1`。如果配置成某个特定网卡 IP，
Tailnet 会降级但本地 API 仍继续运行。Docker 首版镜像不内置 helper，容器部署继续使用
Caddy HTTPS / 自管 TLS。

macOS 主应用仍兼容 10.15+，但 Go 1.26.6 helper 的实测 `minos` 是 12.0。macOS 10.15 / 11
会在启动前只禁用 Tailnet helper，本机 Web、推荐和其它功能继续可用；无需因此把整个 App
视为 macOS 12+。

### Tailnet 未就绪或反复要求登录？

1. 桌面安装包应能直接找到 helper；从菜单打开运行日志，并检查运行目录的
   `data/tailnet/status.json`。源码安装先确认 `go version` 为 1.26.6，再运行
   `openbiliclaw tailnet build-helper`，并用 `openbiliclaw tailnet status` 查看最近状态。
2. 出现 `needs_login` 时，用电脑浏览器打开日志 / status 中显示的 Tailscale URL。启动窗口
   关闭并不会替代登录。
3. 确认两端属于同一 tailnet，ACL / grants 允许手机节点访问电脑节点的 API 端口。
4. 确认 `[api].host` 是 loopback / localhost / wildcard，而不是单独的 LAN IP。
5. 自动化环境可由启动父进程提供短期 `OPENBILICLAW_TAILNET_AUTH_KEY`。它只经 stdin 交给
   helper，不写配置、argv、status 或日志；不要把 key 写进仓库。
6. 若 `tailnet enable|disable` 提示配置被覆盖，按提示修改
   `OPENBILICLAW_TAILNET_ENABLED` / `OPENBILICLAW_TAILNET_HOSTNAME` 或
   `config.local.toml`；优先级是显式环境 > local > base，CLI 不会把会被遮蔽的 base 修改
   伪装成成功。Auth Key 与 `OPENBILICLAW_TAILNET_HELPER` 只是 runtime-only 控制，不属于配置。

源码 / 一句话安装的 `openbiliclaw tailnet disable` 会保留 `data/tailnet/` 节点身份；桌面包
则完整退出、把 `[tailnet].enabled` 改为 `false` 后重新启动，同样保留身份，所以下次启用通常
不需重新登录。确实要重置身份时，先完整退出 OpenBiliClaw，在 Tailscale 管理台移除旧节点，
再安全删除本机 `data/tailnet/`；这会不可逆地丢失该节点身份。跨机器 `.obcbackup` 刻意不
包含该目录，新电脑应作为新节点登录。

### 浏览器扩展能直接填写 Tailnet 的 `http://100.x` 地址吗？

首版不承诺。应用内 Tailnet 的受支持客户端是已内嵌 tsnet 的 Android / iOS 原生 App；浏览器扩展
仍执行自己的 remote endpoint、host permission、HTTPS 与设备 key 安全规则。不要因为
Tailnet 底层已加密就假设任意 MagicDNS / `100.x` 明文 HTTP 会被扩展接受。扩展跨网络访问
继续使用文档中的 Caddy HTTPS / TLS 路径；局域网连接保持原有方式。

### 应用内 Tailnet 会把诊断日志上传给 Tailscale 吗？

helper 固定用 `ts_omit_logtail,ts_omit_webclient` 构建，自动诊断日志上传和未使用的管理 Web UI
都从二进制移除，因此不会向 `log.tailscale.com` 上传 helper 诊断日志。相应代价是排障时不能
依赖这部分 Tailscale 上游日志支持，应查看 OpenBiliClaw 本地运行日志和脱敏 status。节点登录、
地址分配、连通性等控制面仍会联系用户选择的 Tailscale 协调服务，无法直连时也仍可能经 DERP；
省略 logtail 不等于完全不与 Tailscale 基础设施通信。

## 更新与数据

### 后端设置里没有「立即应用」更新按钮？

「立即应用」只对源码安装（`install_mode="git"`）显示。桌面安装包用户请直接从 [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) 下载新版安装包覆盖安装，数据目录不受影响。

### 点「立即应用」提示更新未开始 / 被拒绝？

后端自动更新有安全守卫：本地有未提交改动（`dirty_worktree`）、remote 不受信任（`untrusted_remote`）、分支无法快进（`branch_not_fast_forwardable`）等情况会拒绝更新，插件会展示具体原因。源码安装用户可进仓库目录手动处理后重试（如 `git status` 清理本地改动）。

### 点「立即应用」后显示「更新后依赖安装失败」？

旧更新器会把仓库里存在 `uv.lock` 错当成系统已安装 `uv`：使用官方 pip/venv fallback 安装时，源码已经快进到新 tag，随后却因找不到 `uv` 停在重启前，所以卡片仍显示旧进程版本。包含修复的新版本会先探测真实可用工具，无 `uv` 时自动改用当前虚拟环境的 pip，并在失败时显示工具与退出码摘要。

已卡住的安装需要**人工重启一次后端**加载磁盘上的新源码；若要一次性修复依赖环境，先停止当前后端，再重跑原来的一句话安装命令（`config.toml`、`data/` 与 Cookie 都会保留）。也可在安装目录手动执行：有 uv 时运行 `uv sync`；pip/venv 安装运行 `.venv/bin/python -m pip install -e .`（Windows：`.\.venv\Scripts\python.exe -m pip install -e .`），然后用同一个 Python 执行 `-m openbiliclaw.cli start`。

### 一直提示「git 远端不在允许列表，更新被阻止」？

老版本（≤0.3.153）的允许列表按**精确字符串**匹配 `origin` 地址，`git clone` 时少写 `.git` 后缀、或用了与列表拼法不一致的 HTTPS/SSH 地址都会被永久拦住——而且被拦住的安装无法通过自动更新拿到修复版本，需要一次手动解锁（进入安装目录执行）：

```bash
git remote -v                      # 先看实际的 origin 地址
git pull --ff-only                 # 手动拉一次最新代码即可解锁
# 或者把 origin 改成官方地址后重试自动更新：
git remote set-url origin https://github.com/whiteguo233/OpenBiliClaw.git
```

新版本起允许列表按规范化形式比较（`.git` 后缀可省、HTTPS/SSH 拼法等价、大小写不敏感），正常克隆不会再触发；通过 GitHub 镜像克隆的安装把镜像地址加入 `config.toml` 的 `[scheduler] auto_update_allowed_remotes` 即可。被拒绝时后端日志会打出实际的 remote 地址和修复命令。

### 我的数据存在哪里？会上传吗？

核心行为、推荐与对话数据存在本机 SQLite，画像、凭据与缓存等辅助文件也在本机数据目录；默认运行根目录为 `~/OpenBiliClaw`（macOS / Linux）或 `%USERPROFILE%\OpenBiliClaw`（Windows），升级和卸载不会动它。插件不会把数据发送到 OpenBiliClaw 开发者运营的服务器；只有你配置了云端 LLM / embedding 时，相关内容才会按你的配置发给对应服务商。详见 [隐私政策](privacy.md)。

### 为什么保存新的数据目录后仍在使用旧目录？

这是安全边界，不是保存失败。运行中的后端已经为当前 active data dir 持有 canonical runtime lock，设置页保存新 `data_dir` 时只把路径写入 `config.toml`，响应会显示 `restart_required=true`；其它配置仍可在后台应用，但数据库、MemoryManager、同次保存的抖音 / X 凭据以及此时导出的迁移数据快照继续使用旧目录。请**完整退出并重新启动** OpenBiliClaw；新进程取得新目录锁后才会切换。期间即使配置 apply status 显示 `applied`，也只表示可热重载部分已生效。

### 怎么把配置、画像和历史迁移到另一台机器？

在旧机器**本机**打开桌面 Web `/web`，进入「设置 → 通用 → 数据迁移」，先导出 `.obcbackup`；把文件安全复制到新机器，再在新机器的同一位置选择导入。导入成功只表示已经完整校验并暂存，配置、数据和桌面偏好都要等重启应用成功后才切换。重启前可在页面查询或取消待导入项，取消不会改动当前数据；如果上传超时 / 断线，页面会用本次 `request_id` 查询状态，`processing` 表示仍在上传或校验。页面最多强制查询 3 次，遇到 `idle/cancelled` 会间隔 500ms 再确认，不会把紧接断线的一次 `idle` 当最终结果；再打开「通用」也会强制重新对账，避免盲目重复导入。

重启应用成功后，每个浏览器会按 `migration_id` 只应用一次包内白名单桌面偏好。迁移状态之后仍可能显示 `applied`，但你后来手动修改的主题、色相或自动续页不会再被同一迁移覆盖；在另一浏览器或新的浏览器配置文件中，会各自完成一次交接。

迁移会替换新机器现有的可移植配置和用户数据，因此导入前会二次确认。成功应用后，旧 `config.toml`、`config.local.toml` 和数据目录按存在情况保留为 `pre-import-*.bak` 回滚副本；目标机自己的数据路径、API 端口、网络 / TLS / 自启动、证书、浏览器 CDP 设置、Bilibili 专用代理和本机浏览器可执行文件路径不被覆盖。来源包会删除整段 `[api.auth]`，导入以目标机现有整段 `api.auth` 为基线，所以目标机的登录开关、密码和 proxy / Origin 策略保留；随后文件 session secret 会轮换，数据库的会话撤销 epoch 会设为来源与目标当前值最大值再加一，扩展设备访问也会关闭并清空 key。因此即使目标机用环境变量固定 session secret，来源 / 目标已有 Web 会话仍会失效，扩展远程设备需重新配对。

请特别注意：`.obcbackup` **没有加密**，可能包含模型 / 来源 API Key、平台 Cookie、画像和浏览历史；它不包含源机 API auth 的密码 / hash、session secret 或设备 key，但仍必须只在可信设备之间传递，用完后从聊天、网盘和下载目录中删除不再需要的副本。日志、旧备份、embedding 缓存、证书、外部 CLI 登录和环境变量值不会导出；`source_omitted_environment_variables` 提示旧机器依赖但未入包的变量名，`target_active_environment_variables` 提示新机器当前仍会覆盖文件配置的变量名。迁移接口只接受后端确认的本机 loopback 调用，浏览器还必须同源；不能从手机 / LAN 页面或浏览器扩展远程触发。

### 配置文件写坏了导致启动失败？

桌面包（v0.3.152+）会自动把坏的 `config.toml` / `config.local.toml` 备份为 `*.invalid`、重建默认配置并打开 `/setup/` 重新初始化；`data/` 不会被删除。源码安装可对照 `config.example.toml` 手动修复。
