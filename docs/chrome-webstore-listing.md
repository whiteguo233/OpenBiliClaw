# Chrome Web Store 商店页文案与素材

> 用途：维护 Chrome Web Store Developer Dashboard 的商店详情页。
> 更新插件能力、安装路径、隐私政策、后端部署方式或截图时，同步更新本文件。

## 提交入口

- Chrome Web Store item: <https://chromewebstore.google.com/detail/openbiliclaw/cdfjfkdjjhdaccbldipkjhpibnfbiamg>
- Developer Dashboard: <https://chrome.google.com/webstore/devconsole/> -> `Store listing`
- 项目主页 / Website URL: <https://whiteguo233.github.io/OpenBiliClaw/>
- 支持 / Support URL: <https://github.com/whiteguo233/OpenBiliClaw/issues>
- 隐私政策: <https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md>

## Short Description

```text
需本地后端的十二来源内容发现 AI Agent：跨平台推荐、私有画像与可反馈侧边栏
```

## Detailed Description

将下面的纯文本完整复制到 Chrome Web Store 的 `Detailed description` 字段。

```text
OpenBiliClaw 是一个需要本地后端运行的、本地优先、私有、开源的个性化内容发现 Agent。它把你授权范围内的 B站、小红书、抖音、YouTube、X、知乎、Reddit、Linux.do、Bangumi、V2EX、微博与 Instagram 内容汇合成跨来源推荐、可查看和纠正的个人画像，以及能继续反馈调教的浏览器侧边栏。数据默认保存在你的本机。

项目主页：
https://whiteguo233.github.io/OpenBiliClaw/

GitHub 源码 / Issue / Releases：
https://github.com/whiteguo233/OpenBiliClaw

安装和使用：
1. 安装这个浏览器插件。
2. 部署并启动本地后端。普通用户可从 GitHub Releases 下载 macOS .dmg / Windows .exe；需要源码部署或深度定制时，可按 README / AI 部署说明操作。
   Releases: https://github.com/whiteguo233/OpenBiliClaw/releases
   AI 部署说明: https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md
3. 后端启动后，在电脑上打开：
   http://127.0.0.1:8420/web
4. 在同一个浏览器登录你准备授权给 OpenBiliClaw 使用的平台；YouTube 等公开内容发现路径不一定需要登录。是否启用某个平台由你在设置页决定。
5. 打开 OpenBiliClaw 插件侧边栏，确认本地后端连接，按引导初始化画像，然后查看推荐、点喜欢 / 少来点 / 不感兴趣，或直接对话校准。

支持的平台：
- B站
- 小红书
- 抖音
- YouTube
- X（Twitter）
- 知乎
- Reddit
- Linux.do
- Bangumi
- V2EX
- 微博（公开 discovery 匿名；初始化时可通过已登录浏览器只读导入个人收藏、关注和互动）
- Instagram（实验性、默认关闭；公开 topic / creator discovery，初始化时可通过已登录浏览器只读导入近期点赞、收藏和关注）

这个插件能做什么：
- 在支持的平台页面识别你授权范围内的内容与互动信号，或执行本地后端下发的来源任务。
- 把跨平台候选统一筛选，在侧边栏、PC Web 和移动 Web 中展示推荐及推荐理由。
- 展示可查看、可纠正的私有画像，并通过喜欢、少来点、不感兴趣和聊天反馈继续调整推荐。
- 在配置页分别展示“来源是否启用”和“接入状态”；“凭据已就绪”“状态待验证”“无需登录”含义不同，不会把仅保存在本地的令牌冒充成实时登录成功。

重要说明：
- 插件不是独立云服务；需要本地后端运行后才有完整体验。
- 默认连接 127.0.0.1 / localhost。连接局域网或远程后端时，需要你显式配置地址并授予对应站点权限；公网后端要求 HTTPS 和设备认证。
- 推荐、画像、反馈和运行数据默认保存在你的本机 SQLite 数据库里，不会发送到 OpenBiliClaw 开发者服务器。
- 你自行配置的 LLM / embedding 服务可能接收完成相应功能所需的内容；可以使用本机 Ollama，也可以使用你自己的 API Key。具体数据边界见隐私政策。
- 来源接入状态默认只读取本地后端保存的凭据、插件心跳和任务历史，不为了刷新配置页而访问外部平台，降低多余请求与封控风险。
- 小红书 discover 搜索在后台标签页执行。隐藏标签不挂载结果列表时，同页 MAIN world 桥只从页面自身的搜索响应归一化最多 20 条公开卡片字段（链接、标题、作者、封面、发布时间与互动数）和既有内容访问 token，不修改请求、不转发原始响应、不读取 Cookie 值或搜索结果正文；结果仅用于本地 discover 任务并送往用户配置的后端，DOM 仍作兜底。
- 抖音初始化任务需要当前账号的公开 `sec_uid` 才能读取该账号的发布、收藏、点赞和关注分页。页面公开的 `#RENDER_DATA` 只作为显式登录候选；插件会在抖音页面内调用同源只读 `/aweme/v1/web/user/profile/self/` 做最终确认，冲突时以后者为准，未确认的候选不会缓存或用于分页。常驻 fetch / XHR tap 不从被动请求 URL 提取或记录 `sec_user_id`；只有用户触发 bootstrap 后，页面消息桥才传递已确认的公开 `sec_uid`、请求关联字段和解析后的任务条目，不传递 Cookie、token 或未裁剪的原始响应；结果仅送用户配置的本地后端。
- 插件申请 `https://linux.do/*` host permission，用于普通 Linux.do 页面上的统一行为 adapter，以及扩展自己创建的隔离任务 tab。任务 tab 只执行同源只读 GET：公开 search / hot / feed / creator / related discovery 不要求登录，个人 bookmarks / likes / read history 则先由 `/session/current.json` 正面确认当前账号。插件只把 `_t` 是否存在转换为登录布尔；`_t` 值、其他 Cookie、CSRF 数据、原始 JSON/HTML 和挑战页正文都不会上传。任务只回传归一化 topic 字段、scope 计数或结构化错误，不会发帖、点赞、收藏、关注、编辑或执行任何站内状态变更。自动化测试已覆盖任务协议、分页、资源上限、超时和 tab 隔离；2026-08-09 又以已登录 Chrome unpacked extension 完成 bootstrap、五路 discovery、候选入池和无敏感字段回传的真实只读 E2E。Firefox 已完成构建与测试，但尚未做同等实号 E2E。
- 插件在 `bgm.tv` / `bangumi.tv` 上申请的 host permission 仅用于账号身份识别：读取页面公开的用户 uid 与用户名，实现零配置识别你的 Bangumi 账号；在这两个站点上不读取 Cookie、不采集浏览行为，也不上传任何个人令牌。Bangumi 内容本身由本地后端通过官方匿名只读 API 获取。
- 插件在 `*.v2ex.com` 上申请的 host permission 仅用于只读 Topic / Node 阅读事件，以及你主动触发的四类初始化或增量任务：本人主题、本人公开回复、收藏主题和收藏 Node。插件只检查 A2 Cookie 是否存在并向你配置的后端发送登录布尔值，不访问、存储或发送 Cookie 值；任务只返回有界的公开渲染字段，不返回页面 HTML、请求头、CSRF / once、私信或浏览器完整历史。V2EX 公开发现由本地后端通过官方只读 API / Feed 完成；OpenBiliClaw 不向 V2EX 发帖、回复、感谢、收藏、取消收藏或关注 Node。
- 插件在 `*.instagram.com` 上申请的 host permission 仅用于扩展创建的隔离只读任务 tab。公开任务读取有界 topic/creator 内容，登录态初始化任务在同源 current-account 响应确认数字账号 ID 后读取 liked/saved/following；`sessionid` 只转成布尔 readiness。Cookie value、Authorization/CSRF、请求头、原始响应与挑战页都不会上传；任务不输入 Instagram Search，也不执行点赞、收藏、关注、评论或消息写入。该来源默认关闭，技术隔离不替代 Meta 的自动采集许可。
- 「个人通讯」采集范围除侧边栏聊天消息外，还包含你在受支持平台上**成功提交**的评论正文与 B 站弹幕正文（经网络层在提交成功后采集，仅送本机后端，用于更准确地构建兴趣画像）。

> **发版待办（商店后台隐私披露表单）**：Chrome Web Store 与 Firefox AMO 的数据用途申报中，「个人通讯 / Personal communications」条目需更新描述，覆盖新增的用户提交评论与弹幕正文采集（Firefox manifest 已声明 `personalCommunications`，无需改动权限，仅需同步商店后台文案）。

隐私政策：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md

英文说明：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/README_EN.md
```

## 截图上传顺序

以下文件均为 1280×800，使用固定脱敏数据和当前真实 UI 生成。Developer Dashboard 中删除旧图后，按下面顺序上传：

1. `01-seven-platform-recommendations.png` — 十一来源推荐主视觉，推荐卡和惊喜位都有本地脱敏头图（文件名为兼容既有上传顺序而保留）
2. `02-three-surfaces.png` — PC、插件、手机三端推荐体验
3. `03-truthful-status-local-data.png` — 诚实接入状态与本地数据

仓库路径：`docs/images/chrome-web-store/`。

需要重做截图时：

```bash
.venv/bin/python scripts/build_chrome_webstore_demo_covers.py
cd extension && npm run build && cd ..
PYTHONPATH=src .venv/bin/python scripts/capture_chrome_webstore_ui.py \
  --output-dir docs/images/chrome-web-store/source
.venv/bin/python scripts/build_chrome_webstore_assets.py
```

该脚本使用脱敏的演示夹具，只用于 Chrome Web Store 素材。README 与 GitHub Pages
首页引用的 `docs/images/` 截图必须来自真实运行中的 OpenBiliClaw，不得用该脚本覆盖。

`build_chrome_webstore_demo_covers.py` 会确定性生成 8 张 640×360 本地插画封面，分别供七条演示推荐和一个惊喜推荐使用；演示条数不代表来源总数，它们也不是任何平台或创作者的真实媒体。捕获脚本只连接临时 `127.0.0.1` 脱敏演示服务，封面也经真实 UI 的本机 `/api/image-proxy` 链路加载，并拦截所有非本机请求；不得用真实 `config.toml`、数据库、Cookie、账号名或画像文本生成商店素材。

## Metadata API bridge

`.github/workflows/update-chrome-webstore-listing.yml` 是独立的手动文案维护入口，默认 `mode=probe`，只交换短期 OAuth access token 并读取 v1.1 draft；它只输出字段名、文案长度和 SHA-256，不输出 token、secret 或 draft 原文。只有 probe 同时发现 `summary` / `description` 和足够的 listing identity 字段后，`mode=apply` 才可能继续；若当前 submission 正在审核，还必须显式启用 `replace_pending`，写入后必须精确回读一致，最后才允许 `publish`。

Chrome Web Store API v1.1 已弃用，官方只支持到 2026-10-15；而且其公开 `Item` resource 没有承诺商店文案字段，因此 probe 返回“不支持 writable listing metadata”是安全的预期停止结果，不得为绕过它而猜测 Dashboard 私有接口。该 bridge 不构建或上传 ZIP、不移动 release tag，也不上传截图；三张 PNG 仍需在 Developer Dashboard 手动替换。

本地只读探测命令（凭据必须来自环境变量）：

```bash
cd extension
npm run webstore:metadata -- \
  --listing ../docs/chrome-webstore-listing.md \
  --mode probe
```

## 提交前检查

- `Short description` 与 `Detailed description` 已粘贴，十二类来源名称完整，并单独解释 Linux.do / V2EX / 微博 / Instagram 任务权限理由、只读边界、Cookie 不回传及公开发现 / 个人初始化的能力边界。
- 3 张截图已按上面的文件名顺序上传，尺寸均为 1280×800。
- `Website URL` 使用项目主页：`https://whiteguo233.github.io/OpenBiliClaw/`。
- `Support URL` 使用 GitHub Issues：`https://github.com/whiteguo233/OpenBiliClaw/issues`。
- `Privacy policy URL` 使用 `docs/privacy.md` 的 GitHub 链接。
- 后端默认端口、插件权限、安装方式或支持平台变化时，本文件和截图必须同步更新。
- Metadata workflow 的 probe 必须先成功，apply 才可撤审、写文案和重新提审；probe 失败时不得继续。
