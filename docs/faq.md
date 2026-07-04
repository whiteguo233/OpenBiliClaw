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
2. 后端要绑定 `0.0.0.0`：桌面包默认如此；源码安装检查 `config.toml` 的 `[api].host`（`0.0.0.0` = 局域网可达，`127.0.0.1` = 仅本机）。
3. 用插件顶部手机图标的二维码打开最稳，它会优先展示电脑的局域网 IP。

## 更新与数据

### 后端设置里没有「立即应用」更新按钮？

「立即应用」只对源码安装（`install_mode="git"`）显示。桌面安装包用户请直接从 [Latest Release](https://github.com/whiteguo233/OpenBiliClaw/releases/latest) 下载新版安装包覆盖安装，数据目录不受影响。

### 点「立即应用」提示更新未开始 / 被拒绝？

后端自动更新有安全守卫：本地有未提交改动（`dirty_worktree`）、remote 不受信任（`untrusted_remote`）、分支无法快进（`branch_not_fast_forwardable`）等情况会拒绝更新，插件会展示具体原因。源码安装用户可进仓库目录手动处理后重试（如 `git status` 清理本地改动）。

### 我的数据存在哪里？会上传吗？

所有数据存在本机的一个 SQLite 文件里，数据目录为 `~/OpenBiliClaw`（macOS / Linux）或 `%USERPROFILE%\OpenBiliClaw`（Windows），升级和卸载不会动它。插件不会把数据发送到 OpenBiliClaw 开发者运营的服务器；只有你配置了云端 LLM / embedding 时，相关内容才会按你的配置发给对应服务商。详见 [隐私政策](privacy.md)。

### 配置文件写坏了导致启动失败？

桌面包（v0.3.152+）会自动把坏的 `config.toml` / `config.local.toml` 备份为 `*.invalid`、重建默认配置并打开 `/setup/` 重新初始化；`data/` 不会被删除。源码安装可对照 `config.example.toml` 手动修复。
