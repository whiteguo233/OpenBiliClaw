# 真实操作演示 / Recorded product walkthrough

本目录收录 OpenBiliClaw 的两段真实界面操作录屏、GIF 和字幕。桌面演示展开已有推荐的理由、
加入「稍后再看」，再打开内容库确认保存；手机 Web 演示提交「喜欢」反馈并查看同一个内容库。
官网提供播放入口；GitHub README 使用 GIF 或视频封面链接。

## 素材清单

| 文件 | 内容与用途 |
|---|---|
| [recommendation-walkthrough.mp4](recommendation-walkthrough.mp4) | 桌面操作录屏，25.3 秒，1440 × 960，约 1.5 MB |
| [recommendation-save.gif](recommendation-save.gif) | 从桌面录屏第 0.5 秒起截取 24 秒，960 × 640，约 3.3 MB；无加速或拼接 |
| [recommendation-walkthrough.zh.vtt](recommendation-walkthrough.zh.vtt) | 中文字幕 |
| [recommendation-walkthrough.en.vtt](recommendation-walkthrough.en.vtt) | Desktop English captions |
| [mobile-feedback.mp4](mobile-feedback.mp4) | 手机 Web 操作录屏，20.45 秒，430 × 932，约 346 KB |
| [mobile-feedback.gif](mobile-feedback.gif) | 手机 Web 操作 GIF，20.5 秒，205 帧，430 × 932，约 1.8 MB |
| [mobile-feedback.zh.vtt](mobile-feedback.zh.vtt) | 手机 Web 中文字幕 |
| [mobile-feedback.en.vtt](mobile-feedback.en.vtt) | Mobile Web English captions |
| [live-demo-poster.jpg](../images/live-demo-poster.jpg) | 视频静态封面 |
| [live-desktop-home.jpg](../images/live-desktop-home.jpg) | 桌面推荐首页 |
| [live-desktop-reason.jpg](../images/live-desktop-reason.jpg) | 展开的推荐理由 |
| [live-desktop-library.jpg](../images/live-desktop-library.jpg) | 内容库中的稍后再看列表 |
| [live-mobile-recommend.jpg](../images/live-mobile-recommend.jpg) | 同一后端的手机 Web 推荐页 |
| [live-mobile-feedback.jpg](../images/live-mobile-feedback.jpg) | 手机 Web 中实际提交喜欢反馈的界面 |
| [live-mobile-library.jpg](../images/live-mobile-library.jpg) | 手机 Web 中共享的稍后再看列表 |

画面中的产品界面和内容原文为中文。官网可随页面语言选择中文或英文字幕；英文字幕不表示界面已被翻译。
GIF 没有可切换的字幕轨，下方文字稿提供两种语言的动作说明。体积为便于阅读的约数。
手机 GIF 以 10 fps 导出，20.45 秒源视频量化为 20.5 秒；精确字节数、SHA-256、时长和帧数见[素材清单](manifest.json)。
手机素材来自浏览器的 430 像素宽视口，运行真实移动 Web；没有使用物理手机录制，也不是 Flutter 原生客户端。

## 录制来源与范围

- **录制日期**：2026-10-02。
- **应用版本**：v0.3.224；代码基线 `e53f0d3e`。这是录制时的版本，不代表当前最新发布。
- **运行环境**：独立数据目录和浏览器环境，运行该版本的真实 OpenBiliClaw 后端及其桌面 / 手机 Web 页面。
- **内容来源**：隔离数据库只带入六条经过检查、可公开展示的历史推荐及对应内容记录，保留推荐原文与原始时间；移除原记录里的个人反馈状态。
- **没有带入的数据**：个人画像、行为事件、对话、配置、Cookie、API Key 或其他凭据。
- **桌面实际动作**：通过产品界面将内容保存到隔离数据库的「稍后再看」，再打开真实内容库确认。没有同步到外部平台。
- **手机 Web 实际动作**：点击一条推荐的「喜欢」，然后打开共享内容库。`POST /api/feedback` 返回 HTTP 200；隔离数据库中的推荐记录 `7323` 实际写入 `feedback_type=like`。这证明反馈已接收并保存，不代表学习已经完成。

这次演示展示**查看推荐理由、本地保存与接收喜欢反馈**。推荐来自先前实际运行产生的记录，
录制库没有带入个人画像，也未为了画面补造画像或已初始化状态。录制期间没有重新生成推荐，
没有展示初始化、即时学习、画像更新、后续个性化变化或模型回复。不复制或伪造 API 响应，不使用 fixture / mocked API，
也不把剪辑后的画面当作生成速度或学习效果的证明。

原始录制母版、临时截图、隔离数据库和录制工作文件只保存在被 Git 忽略的 `output/live-media/`；
不随公开素材提交。公开素材仍可能显示公开内容的标题、作者、来源和推荐说明，替换前必须重新做逐帧隐私检查。

## 逐步文字稿

### 桌面：推荐理由与稍后再看

| 步骤 | 中文 | English |
|---|---|---|
| 1 | 打开桌面推荐页，查看已经存在的内容卡片。 | Open the desktop recommendations page and browse existing content cards. |
| 2 | 展开一条推荐的理由，阅读它为什么被推荐。 | Expand a recommendation's explanation and read why it was selected. |
| 3 | 点击卡片上的「稍后再看」，等待应用确认保存。 | Select Watch later on the card and wait for the app to confirm the save. |
| 4 | 打开内容库，切换到「稍后再看」。 | Open the Content library and select Watch later. |
| 5 | 在列表中找到刚才保存的内容，确认本地保存完成。 | Find the same item in the list to confirm it was saved locally. |

### 手机 Web：喜欢反馈与共享内容库

| 步骤 | 中文 | English |
|---|---|---|
| 1 | 在 430 像素宽的浏览器视口打开手机 Web，连接同一后端。 | Open the mobile web app in a 430-pixel-wide browser viewport, connected to the same backend. |
| 2 | 给一条已有推荐点「喜欢」，等待后端接收并记录反馈。 | Like an existing recommendation and wait for the backend to receive and record the feedback. |
| 3 | 打开内容库，查看「稍后再看」。 | Open the Content library and select Watch later. |
| 4 | 找到桌面刚保存的内容，确认两个界面共享本地保存结果。 | Find the item saved on desktop and confirm that both interfaces share the local saved list. |

字幕的具体出现时间以各自 `.vtt` 文件为准。手机 Web 不独立运行推荐后端；浏览器视口演示也不能作为
物理手机性能、触控表现或 Flutter 客户端的测试结论。

### English provenance note

Recorded on October 2, 2026, using OpenBiliClaw v0.3.224 at commit `e53f0d3e`. The recording runs the real backend
and product UI against an isolated database containing six reviewed historical recommendations and their public
content records. Original recommendation text and timestamps are retained; personal feedback state is removed.
No personal profile, activity events, conversations, configuration, cookies, or API keys were copied.

The desktop walkthrough performs a real local Watch later save and verifies it in the Content library. The mobile Web
walkthrough records a Like through the real feedback endpoint, then opens the shared saved list. The mobile recording uses
a 430-pixel-wide browser viewport, not a physical phone or the Flutter app. Neither walkthrough demonstrates
recommendation generation, live learning, profile updates, subsequent personalization changes, or model replies.
No personal profile or initialized state was invented for the recording. No API responses are copied or fabricated,
and no fixture or mocked API is used. The interface and source content are in Chinese; English captions explain the actions.
Raw recordings and the isolated database remain in the ignored `output/live-media/` directory.

## 替换素材时

先核对[产品演示素材指南](../media-guide.md)中的真实性、隐私和压缩要求。更新视频或 GIF 时，同时更新
本页的版本、日期、内容范围、文字稿、两套字幕和 [manifest.json](manifest.json)；截图与封面也应来自同一轮经过检查的真实运行。
不要只替换文件而保留旧的来源说明。网页引用、字幕语言切换和静态封面按[主页维护指南](../seo.md)验证。
