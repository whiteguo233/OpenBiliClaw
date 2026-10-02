# 产品演示素材指南

README 和官网保留原有的一两句产品定位，紧接着用真实画面让读者看懂产品长什么样、有哪些功能、在不同设备上怎样使用，再进入安装和原有的项目说明。
本页记录素材来源、更新流程和验收要求；每批录屏的版本、范围与文字稿集中放在
[`docs/media/README.md`](media/README.md)。

## 先让读者看见产品

1. **两句定位。** 保留「本地运行、跨平台理解你、只为你一个人构建」及持续理解、主动发现的简短介绍；首屏不先铺长篇痛点、目标人群或场景论证。
2. **一段完整导览。** [`product-tour.mp4`](media/product-tour.mp4) 用约 70 秒、配合文字讲解介绍刷推荐、阅读理由、桌面 / 插件 / 手机使用、画像、口味、兴趣探针与对话。README 用约 20 秒的 [`product-tour.gif`](media/product-tour.gif) 章节摘录引导观看完整版，官网提供视频控件、中英字幕和静态封面。
3. **按真实界面认识功能。** 桌面推荐大图之后并列展示插件和手机，再用画像、认知风格与内容口味、兴趣探针、对话截图补齐功能面貌。每幅图只配一句具体说明，可点开原图阅读；更完整的操作保留独立实录入口。
4. **接回原有正文。** 演示之后进入快速开始，再说明为什么需要个人推荐 Agent、工作闭环、来源与客户端、数据流、隐私、文档和社区。原有产品主张放在正常正文位置，不用保存 / 收藏操作替代整个项目的介绍。

产品导览是**三端真实录屏片段与仓库既有功能截图的剪辑**，不是一次连续完成全部功能的实录。
字幕应区分「真实操作」与「既有功能截图」；约 20 秒 GIF 是章节摘录，不代表约 70 秒全片被等比例加速。
画像、口味、兴趣探针和对话使用已公开截图展示功能，不能暗示录制期间生成了这些结果。
画像描述是可查看、纠正的模型推断；发现与推荐使用结构化画像，具体关系见[画像使用登记表](profile-usage.md)。

信息组织以帮助读者快速认识完整产品为目标，不以 README 行数为目标。保留真实推荐理由和来源标识，
让画面与简短介绍相互补充，不把示意文案排成真实对话或用户证言，也不声称未经验证的推荐质量或节时效果。

## 产品导览与功能截图

| 素材 | 用途与来源 |
|---|---|
| [`media/product-tour.mp4`](media/product-tour.mp4) | 约 70 秒产品导览：三端真实操作片段与既有功能截图，含逐段文字介绍 |
| [`media/product-tour.gif`](media/product-tour.gif) | 约 20 秒章节摘录，供 README 快速预览；不是连续操作录像 |
| [`media/product-tour.zh.vtt`](media/product-tour.zh.vtt) / [English](media/product-tour.en.vtt) | 产品导览中英字幕，按章节标明操作与截图的性质 |
| [`images/product-tour-poster.jpg`](images/product-tour-poster.jpg) | 官网导览的静态封面 |
| [`images/screenshot-profile-portrait.png`](images/screenshot-profile-portrait.png) | 既有灵魂画像截图：人格素描、特质与深层需求 |
| [`images/screenshot-profile-style.png`](images/screenshot-profile-style.png) / [价值偏好](images/screenshot-profile-values.png) | 既有认知风格、内容口味与价值偏好界面 |
| [`images/screenshot-interest-probe.png`](images/screenshot-interest-probe.png) | 既有兴趣探针界面：新方向及确认、否定、继续聊入口 |
| [`images/mobile-chat.png`](images/mobile-chat.png) / [插件对话](images/screenshot-chat.png) | 既有对话界面，不作为本轮模型响应实录 |

全片、章节摘录和各段原始操作视频的时长、体积、镜头范围以[素材说明](media/README.md)与
[`media/manifest.json`](media/manifest.json)为准。旧版截图和本批录屏的界面可能不同，正文和字幕应保留版本说明。

## 当前真实操作素材

2026-10-02 的桌面 / 手机素材基于 v0.3.224、commit `e53f0d3e` 的真实后端和 Web 界面，包含两段操作：
桌面**查看推荐 → 展开理由 → 加入稍后再看 → 打开内容库确认**，以及手机 Web 的**喜欢反馈 → 查看共享内容库**。
同日补录的插件素材来自 commit `baa17514` 构建的原版扩展，连接同一隔离后端，展示**浏览推荐 → 滚动到不同来源 → 打开内容库**。
录制环境只带入六条经过检查的历史推荐及对应公开内容记录，保留原文和时间；个人画像、行为事件、
对话、配置与凭据没有复制，原个人反馈状态已移除。录制不补造个人画像或已初始化状态。

| 素材 | 用途 |
|---|---|
| [`media/recommendation-walkthrough.mp4`](media/recommendation-walkthrough.mp4) | 25.3 秒桌面操作视频；1440 × 960，约 1.5 MB |
| [`media/recommendation-save.gif`](media/recommendation-save.gif) | 第 0.5 秒起截取 24 秒；960 × 640，约 3.3 MB，无加速 / 拼接 |
| [`media/recommendation-walkthrough.zh.vtt`](media/recommendation-walkthrough.zh.vtt) / [English](media/recommendation-walkthrough.en.vtt) | 桌面中英字幕，随官网语言选择 |
| [`media/mobile-feedback.mp4`](media/mobile-feedback.mp4) / [GIF](media/mobile-feedback.gif) | 手机 Web 操作；MP4 20.45 秒 / 约 346 KB，GIF 20.5 秒 / 约 1.8 MB，均为 430 × 932 |
| [`media/mobile-feedback.zh.vtt`](media/mobile-feedback.zh.vtt) / [English](media/mobile-feedback.en.vtt) | 手机 Web 中英字幕 |
| [`media/extension-walkthrough.mp4`](media/extension-walkthrough.mp4) / [GIF](media/extension-walkthrough.gif) | 插件连续实录；MP4 17.85 秒、560 × 1040、约 393 KB；GIF 17.88 秒、360 × 669、约 884 KB |
| [`images/live-extension-recommend.jpg`](images/live-extension-recommend.jpg) / [内容库](images/live-extension-library.jpg) | 本批真实插件界面，均为 560 × 1040 |
| [`images/live-desktop-home.jpg`](images/live-desktop-home.jpg) | 桌面推荐主图 |
| [`images/live-desktop-reason.jpg`](images/live-desktop-reason.jpg) | 推荐理由截图 |
| [`images/live-desktop-library.jpg`](images/live-desktop-library.jpg) | 本地稍后再看保存结果 |
| [`images/live-mobile-recommend.jpg`](images/live-mobile-recommend.jpg) | 手机 Web 推荐界面 |
| [`images/live-mobile-feedback.jpg`](images/live-mobile-feedback.jpg) / [内容库](images/live-mobile-library.jpg) | 手机 Web 的喜欢反馈与共享稍后再看 |
| [`images/live-demo-poster.jpg`](images/live-demo-poster.jpg) | 视频未播放时的静态封面 |

这批素材展示实际本地保存和已被后端接收、落库的喜欢反馈，不包括推荐生成、初始化、实时学习、
画像更新、后续个性化变化或模型回复。不能因出现已有推荐，就把录屏描述为“现场生成了推荐”；
也不能把保存或反馈接收确认描述为“已经完成学习”。手机素材使用浏览器 430 像素宽视口，
展示真实移动 Web，不冒充物理手机录制或 Flutter 原生客户端。
界面和推荐原文为中文，英文字幕解释操作，不冒充英文产品界面。详细来源与逐步文字稿见
[本批素材说明](media/README.md)。

插件录制通过 `npm ci && npm run build` 构建仓库原版扩展，在独立 Chromium 配置中加载真实扩展和
service worker，经插件设置连接隔离后端。为清晰展示界面，直接打开扩展自身的 `popup/popup.html`
录制；它是实际扩展运行环境，不是静态 HTML 或 API fixture，也不冒充包含浏览器外壳的侧栏录屏。
插件素材展示 B 站、GitHub 推荐及已有本地内容库，没有执行新的反馈、平台同步、画像生成或模型回复。

原始录制母版、隔离数据库及临时工作文件留在被 Git 忽略的 `output/live-media/`。
公开目录只提交经过检查的图片、压缩视频、GIF、字幕和说明，不提交数据库或个人运行环境。
最终文件的精确字节数、SHA-256、时长、尺寸与 GIF 帧数见 [`media/manifest.json`](media/manifest.json)；
手机 GIF 以 10 fps 导出为 205 帧，时长从源视频的 20.45 秒量化为 20.5 秒；插件 GIF 以 8 fps
导出为 143 帧，时长从源视频的 17.85 秒量化为 17.88 秒。

## 旧素材与使用边界

历史素材保留于 [`docs/images/`](images/)。需要再次使用时，先检查画面、文案与当前版本的一致性。

| 素材 | 内容与边界 |
|---|---|
| `desktop-home.png`、`desktop-cards.png` | 既有桌面推荐截图；不与新录屏混称同一次运行 |
| `screenshot-recommend.png`、`screenshot-recommend-feedback.png` | 插件推荐页与反馈界面 |
| `desktop-profile.png`、`screenshot-profile-*.png` | 既有画像界面；本轮录制没有导入或录制个人画像 |
| `screenshot-chat.png`、`screenshot-interest-probe.png` | 既有对话与兴趣试探截图；不能当作本轮模型回复 |
| `mobile-recommend.png`、`mobile-profile.png`、`mobile-chat.png` | 手机 Web 截图，不能标成 Flutter 原生 App |
| `hero-demo.png` | 截图合成的流程说明拼图，不是一张完整产品界面 |
| `hero-demo-zh.gif`、`hero-demo-en.gif` | 四张说明画面轮播，每张约 2.5 秒；不是操作实录 |
| `hero-demo.gif` | 英文旧轮播的兼容副本 |
| `social-preview-zh.png`、`social-preview-en.png` | 社交分享卡片，替换时同步 metadata |
| `chrome-web-store/` | 商店宣传素材及其演示数据截图，不能替换官网 / README 的真实产品截图 |

旧 `hero-demo*` GIF 由 [`build_readme_hero_demo.py`](../scripts/build_readme_hero_demo.py) 合成。
其中包含早期说明文案，不能证明操作时长或学习效果。新 `recommendation-save.gif`、`mobile-feedback.gif`
和 `extension-walkthrough.gif` 是实际操作录屏的导出；`product-tour.gif` 则是注明来源的产品导览章节摘录。
不要为尚未交付的其他视频放置播放按钮，或把跳转静态截图的链接命名为「观看演示」。

## 真实录制流程

1. **确定一个完整动作。** 本轮原始实录包括桌面保存、手机喜欢反馈接收、插件浏览与内容库查看。产品导览可按章节编排这些片段和已有截图，但应标明剪辑；下一轮若录反馈后的学习、聊天或初始化，单独记录实际服务、耗时与结果，不沿用本轮的能力描述。
2. **准备隔离环境。** 使用独立数据目录和浏览器配置，启动真实后端。只带入已经检查、可公开展示的真实数据；保留来源与时间，不编造推荐理由或成功状态。
3. **确认边界。** 检查调度、平台自动同步和已有任务。仅关闭 discovery 调度不代表所有显式聊天、反馈或任务恢复都停止；不要让录制触发个人环境中的任务。
4. **通过产品界面操作。** 录下输入、点击、等待与实际返回结果，再在产品提供的列表中确认写入。不要用复制的 API 响应、fixture 或 mocked API 代替真实后端。
5. **检查并导出。** 逐帧检查隐私，保留原始母版在忽略目录，导出公开版本与字幕。先核对成品，再更新官网和 README 引用。

[`capture_chrome_webstore_ui.py`](../scripts/capture_chrome_webstore_ui.py) 使用固定演示数据、临时浏览器 profile，
并替换演示 WebSocket。它的 `capture()` 明确拒绝 `docs_output_dir`：

> demo fixture captures must not replace README or GitHub Pages screenshots; capture those from a real running OpenBiliClaw profile

该脚本的选择器和画面尺寸可以参考，但它的 fixture 服务和模拟连接不能用于真实录屏。
若需要说明尚未实现的设计，应独立标为「设计示意」，放在设计文档中。

允许裁剪空白、遮盖敏感字段、加指针强调和剪去等待；不能改写 UI 状态、推荐理由、响应结果或错误提示。
剪辑影响时间判断时，用字幕说明省略等待或展示稍后的结果。推荐内容属于外部平台，保留必要的来源标识，
不要用他人视频整段替代自己的操作过程。

## 隐私检查

录制前关闭桌面通知、个人标签页和自动填充。录制后逐帧检查账号昵称 / 头像、浏览历史、对话、画像、
API Key、Cookie、密码、二维码、Tailnet 入网链接、设备地址、文件路径和日志。首选在录制环境里
移除不适合公开的数据；必须遮盖时确保每一帧和封面都处理到。公开推荐理由也可能透露个人偏好，
不能只检查账号与凭据。

数据流说明统一为：画像、推荐历史等数据默认保存在本机；使用云端 LLM 或 embedding 服务时，
必要内容会发送给用户选择的服务商。此次本地保存和反馈接收演示不能作为所有功能完全离线的证明。
完整边界见[隐私政策](privacy.md)。

## 输出与验收

- 图片按实际显示尺寸压缩，保留可读的标题与推荐理由，提供真实宽高和描述性 alt。手机 Web 图与原生 App 图明确区分。
- GIF 展示短动作，优先缩短空白等待或缩小录制区域，再考虑降低尺寸和帧率；不要为了体积把正文压得无法阅读。
- 视频使用适合浏览器播放的 H.264 MP4，保留高质量母版。官网按需加载，提供手动播放控件和静态封面，不自动播放声音。
- 中文、英文分别交付 WebVTT；字幕按实际操作时间对齐。GIF 无独立字幕轨，正文中的双语文字稿应覆盖动作与限制。
- 检查官网切换语言后所选字幕、封面、视频链接与文字说明一致；保留用户手动切换字幕的能力。
- 在 GitHub README、官网桌面和手机实际检查画面比例、推荐理由可读性、字幕遮挡、加载体积、键盘操作与减少动态效果设置。
- 时长、分辨率、编码与文件体积从最终成品读取，核验后再写入页面；不把计划数字当作实测数字。

替换素材时同步 README 中英文版、官网中文与英文说明、字幕、文字稿，以及
[主页与 SEO 维护指南](seo.md)中的 metadata / sitemap 引用。文件制作完成不表示官网已经部署；
录制日期、应用版本和网站发布日期分别记录。

后续可另录反馈之后的实际学习与推荐变化、聊天、第一次初始化及安装教程；每段先完成真实操作，再补相应说明和播放入口。
