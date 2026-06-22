# Chrome Web Store 商店页文案

> 用途：维护 Chrome Web Store Developer Dashboard 里的商店详情页文案。
> 更新插件发布包、安装路径、隐私政策、后端部署方式或项目定位时，同步更新本文件。

## 提交入口

- Chrome Web Store item: <https://chromewebstore.google.com/detail/openbiliclaw/cdfjfkdjjhdaccbldipkjhpibnfbiamg>
- Developer Dashboard: Store listing -> Detailed description
- 项目主页 / Website URL: <https://whiteguo233.github.io/OpenBiliClaw/>
- 支持 / GitHub 项目页: <https://github.com/whiteguo233/OpenBiliClaw>
- 隐私政策: <https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md>

## Short Description

```text
跨平台内容发现 AI Agent - 行为采集、画像与智能推荐
```

## Detailed Description

将下面的纯文本完整复制到 Chrome Web Store 的 `Detailed description` 字段。

```text
OpenBiliClaw 是一个本地优先、私有、开源的个性化内容发现 Agent。它通过浏览器插件采集你授权范围内的 B 站、小红书、抖音、YouTube 等平台浏览 / 互动信号，交给你本机运行的 OpenBiliClaw 后端，生成个人画像、推荐理由和可反馈的跨平台内容流。

项目主页：
https://whiteguo233.github.io/OpenBiliClaw/

GitHub 源码 / Issue / Releases：
https://github.com/whiteguo233/OpenBiliClaw

安装和使用：
1. 安装这个浏览器插件。
2. 部署本地后端。普通用户建议在 GitHub Releases 下载 macOS .dmg / Windows .exe 桌面安装包；想改源码或深度定制的用户，可以按 README / agent-install 文档让 AI 编程助手部署。
   Releases: https://github.com/whiteguo233/OpenBiliClaw/releases
   AI 部署说明: https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docs/agent-install.md
3. 启动后端后，在电脑上打开：
   http://127.0.0.1:8420/web
4. 在同一个浏览器登录你要使用的平台，至少先登录 B 站；需要更多信号时再登录小红书 / 抖音 / YouTube。
5. 打开 OpenBiliClaw 插件侧边栏，检查后端地址是否为 127.0.0.1:8420，按引导初始化画像，然后查看推荐、点喜欢 / 不感兴趣，或直接对话调教。

这个插件能做什么：
- 在支持的平台页面识别内容与互动信号。
- 把 Cookie / 任务结果同步给你本机的 OpenBiliClaw 后端，用于抓取已授权账号能访问的内容。
- 在侧边栏展示推荐、画像、学习状态和本地后端设置。
- 通过喜欢、不感兴趣、聊天反馈持续调整你的个人推荐。

重要说明：
- 插件不是独立云服务；需要本机 OpenBiliClaw 后端运行后才有完整体验。
- Chrome Web Store 版本默认只连接本机后端（127.0.0.1 / localhost），不会把数据发送到 OpenBiliClaw 开发者服务器。
- LLM / embedding 服务由你自己配置，可以使用本机 Ollama 或你自己的 API Key。
- 数据默认保存在你本机 SQLite 数据库里。

隐私政策：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/privacy.md

英文说明：
https://github.com/whiteguo233/OpenBiliClaw/blob/main/README_EN.md
```

## 提交前检查

- `Detailed description` 已包含项目主页、GitHub 项目页、Releases、AI 部署说明和隐私政策。
- `Website URL` 使用项目主页：`https://whiteguo233.github.io/OpenBiliClaw/`。
- `Support URL` 可使用 GitHub Issues：`https://github.com/whiteguo233/OpenBiliClaw/issues`。
- `Privacy policy URL` 使用 `docs/privacy.md` 的 GitHub 链接。
- 如果 README 快速开始、桌面安装包、后端默认端口或插件权限边界变化，本文件必须同步更新。
