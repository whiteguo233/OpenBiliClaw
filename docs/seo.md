# OpenBiliClaw 主页与 SEO 维护

主页是 `docs/index.html`，通过 GitHub Pages 部署到
<https://whiteguo233.github.io/OpenBiliClaw/>。

本文覆盖首页信息层级、双语内容和搜索引擎维护。HTML 位于 `docs/index.html`，
样式与交互分别位于 `docs/assets/home.css`、`docs/assets/home.js`；meta / OG /
Twitter Card / JSON-LD / `sitemap.xml` / `robots.txt` 随站点一起维护。
本地改稿不代表已经发布或被搜索引擎收录。

> 站点托管在 GitHub Pages 的**子路径**
> `whiteguo233.github.io/OpenBiliClaw/` 下，根目录
> `whiteguo233.github.io/robots.txt` 不归本仓库管。所以本仓库的
> `docs/robots.txt` 只是冗余备份；真正起作用的是把 sitemap
> **手动提交到 Search Console / Bing Webmaster**。

## 首页内容约定

首页按「原项目定位 → 产品导览 → 桌面 / 插件 / 手机真实画面 → 主要功能截图 → 安装 → 项目立场、学习循环与架构」组织。
README 与官网沿用“自进化的跨平台内容发现 Agent、本地运行、只为你一个人构建”的定位；前半部优先让用户看清产品长相和操作，不添加长篇痛点、场景营销段落。
详细安装步骤统一链接到[安装指南](installation.md)。

- 产品导览使用 `docs/media/product-tour.mp4`，由真实操作录屏与仓库既有功能截图剪辑。
  配中英字幕和封面，标为“产品导览”，不能称为全程连续实录。
- 桌面、浏览器插件、手机 Web 预览默认同时可见；分端操作视频放在展开区域，仍可通过
  `#desktop-demo`、`#extension-demo`、`#mobile-demo` 直接访问。视频手动播放、`preload="none"`，
  语言切换只能改字幕，不能重建视频节点或重置进度。启用 JavaScript 时，首次播放前显示真实海报与可键盘操作的播放按钮，点击后使用原生控件；不为冷启动外观提前下载视频。
- 画像、内容风格、兴趣探针与对话使用仓库既有截图，统一轻量脚注明确界面版本可能不同。
  手机画面必须标为手机 Web，不能说是 Flutter 原生客户端或物理手机录制。
  详细来源、录制版本、完整文字稿和成品规格放入[素材说明](media/README.md)，不堆进产品正文。
- 操作视频展示已有真实推荐上的浏览、反馈与保存；录制时没有运行模型生成或画像学习。
  对话截图仅展示已有界面，不代表本次重新验证了模型回复。
- 默认 HTML 直接提供完整中文正文和全部功能图，英文切换同步文字、按钮、图片 alt、标题、描述、
  分享信息与 JSON-LD。没有 JavaScript 时仍可阅读、播放原生视频、展开分端录屏和进入安装指南。
- “本地运行”与“本地存储”不能解释成所有推理都在本机。云端 LLM / embedding 的数据边界保留在
  工作原理和隐私说明；`with-embedding` 包不等于内置可用 LLM。
- 桌面安装包保留默认安装路线，Docker、源码和 AI 助手部署为其他选择。
  固定版本的国内镜像不称为“最新版”，当前发布版本以 GitHub Releases 为准。

改动后在桌面和手机宽度检查：导航不挤压首屏、主图可以阅读、没有横向溢出、主要链接可用，
键盘能操作菜单与语言切换；关闭 JavaScript 后仍能阅读中文介绍并进入安装指南。
资源使用适合 GitHub Pages `/OpenBiliClaw/` 子路径的相对地址。

---

## 本地预览与验收

官网是静态 HTML / CSS / JavaScript，无需构建或启动业务后端。在仓库根目录运行：

```bash
python3 -m http.server 8847 --bind 127.0.0.1 --directory docs
```

上面的轻量服务适合看布局，但不支持视频的 HTTP Range 请求，Chromium 可能无法拖动进度条。
验收视频时，停止该服务，在已安装项目依赖的 Python 环境中改用支持 Range 的静态服务：

```bash
python -c 'import uvicorn; from starlette.staticfiles import StaticFiles; uvicorn.run(StaticFiles(directory="docs", html=True), host="127.0.0.1", port=8847)'
```

打开 `http://127.0.0.1:8847/`；英文可直接用 `?lang=en`，中文用 `?lang=zh`。
页面会记住主动选择的语言；没有语言参数或已保存偏好时默认中文。禁用浏览器存储不应影响切换。

检查 320 / 375 / 390 / 768 / 1024 / 1440 像素宽度下的中英布局，并验证各端预览、功能原图链接、
FAQ 展开、键盘焦点与跳转、语言切换后的 metadata。关闭 JavaScript 时应仍能阅读默认正文、
查看主图并使用安装链接；减少动态效果模式下不使用平滑滚动。验证截图放到忽略的 `output/playwright/`。

媒体还需分别检查桌面和手机 Web 视频的原生播放控件、封面与按需加载、中文字幕默认状态及英文切换后的字幕选择。
产品导览、插件实录和既有桌面 / 手机视频的成品规格与录制范围以[素材说明](media/README.md)为准，
精确文件信息见 [`manifest.json`](media/manifest.json)。手机 GIF 因 10 fps 帧时长量化为 20.5 秒。
画面原文为中文，两种语言都应写明真实录制范围，并提供[逐步文字稿](media/README.md#逐步文字稿)。
反馈已接收不能改写成学习已完成，也不能把录制库描述为正在使用用户完整画像生成推荐。
GIF 在 README 中用于预览，官网优先使用可控制播放的视频；关闭 JavaScript 后仍应能访问视频文件与文字说明。
不要因为录屏已导出就写入网站已发布的日期。母版与录制数据库放到忽略的 `output/live-media/`，不进入公开站点。

文档检查至少包括 `python scripts/release.py --check`（保留 README 版本标记与 JSON-LD 的发布契约）、
`node --check docs/assets/home.js` 和本地链接 / 锚点检查。安装细节的契约断言在
`tests/test_install_contract_docs.py` 与 `tests/test_aggregate_release_workflow.py` 中验证新安装指南，
README 只保留清楚的入口链接。

## 提交到 Google Search Console（必做，10 分钟）

1. 打开 <https://search.google.com/search-console>，登录用 Pages 那个 GitHub 账号。
2. 左上「Add property」→ 选 **URL prefix**，填：
   ```
   https://whiteguo233.github.io/OpenBiliClaw/
   ```
   （末尾斜杠保留。`Domain` 方式需要 DNS，GitHub Pages 子路径不能用。）
3. 验证方式选 **HTML tag**，复制它给的
   `<meta name="google-site-verification" content="...">` 的 `content` 值。
4. 打开 `docs/index.html`，找到这段：
   ```html
   <!-- <meta name="google-site-verification" content="PASTE_VALUE_HERE" /> -->
   ```
   去掉两侧 `<!--` `-->`，把 `PASTE_VALUE_HERE` 替换成第 3 步那个值。
5. 提交、push、等 Pages 重新部署（一般 < 1 分钟），回 GSC 点 **Verify**。
6. 验证通过后，左侧 **Sitemaps** → 填：
   ```
   sitemap.xml
   ```
   （会被拼成 `https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml`），提交。
7. 想立即让 Google 抓首页：顶部搜索框输入
   `https://whiteguo233.github.io/OpenBiliClaw/` →
   **URL Inspection** → **Request Indexing**。

> 验证 meta 一旦提交不能删；删了 GSC 会自动取消验证，sitemap 数据会
> 跟着一起断掉。日后想换验证方式（例如改成 DNS）请先加新方式再去旧的。

## 提交到 Bing Webmaster Tools（推荐，2 分钟）

最快路径：从 GSC 一键导入。

1. 打开 <https://www.bing.com/webmasters>，用 Microsoft 账号登录。
2. **Import from Google Search Console** → 授权 → 选刚才那个 property → 导入。
3. Bing 会沿用 GSC 的验证记录、抓取设置和 sitemap，不需要重复贴 meta。

如果不想用 Google 一键导入，就独立验证：

1. **Add a site** → 填 `https://whiteguo233.github.io/OpenBiliClaw/`。
2. 选 **HTML Meta Tag**，把 `msvalidate.01` 那条 meta 同样在
   `docs/index.html` 取消注释、粘贴值、push。
3. Bing 验证通过后，左侧 **Sitemaps** → 提交
   `https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml`。

## 国内搜索（可选）

百度 / 必应（国内版）/ Yandex 都支持类似流程，并且
`docs/index.html` 已经预留了对应的 meta 占位：

- 百度站长平台：<https://ziyuan.baidu.com/> → 取
  `baidu-site-verification` 的 content 填进对应注释行
- Yandex Webmaster：<https://webmaster.yandex.com/> → 取
  `yandex-verification` 的 content 填进对应注释行

> 注意：百度对 `github.io` 子路径的抓取率较低，是否要做看自己取舍。

---

## 部署后的快速自检清单

部署到 Pages 之后跑一遍这几个 URL，确认收录前提没问题：

- 主页：<https://whiteguo233.github.io/OpenBiliClaw/>
- Sitemap：<https://whiteguo233.github.io/OpenBiliClaw/sitemap.xml>
- 富片段调试：<https://search.google.com/test/rich-results?url=https%3A%2F%2Fwhiteguo233.github.io%2FOpenBiliClaw%2F>
- Twitter / X 卡片预览：<https://cards-dev.twitter.com/validator>（粘 URL）
- Facebook 分享 debugger：<https://developers.facebook.com/tools/debug/?q=https%3A%2F%2Fwhiteguo233.github.io%2FOpenBiliClaw%2F>
- Lighthouse SEO：本地 `chrome://lighthouse` 或 PageSpeed Insights，期望 SEO = 100

## 长期维护

每次主页有较大改动（slogan、核心功能、截图、安装方式变了）就刷新：

- `docs/sitemap.xml` 的 `<lastmod>` 只在实际发布对应页面改动时更新为真实日期；
  未部署的 worktree 改稿不提前标成已发布，也不填未来日期
- `og:image` 如果换图，新图建议 1200×630 PNG（社交分享卡片标准比例），更新
  `og:image:width` / `og:image:height` 与 sitemap 内的 `<image:loc>`
- 标题或描述变了，同时修改 `docs/index.html` 的默认中文 metadata 和
  `docs/assets/home.js` 的中英文翻译项（page title、description、OG / Twitter 文案）；
  语言切换后的 metadata 应与当前正文一致，不能只更新可见标题
- 发布新版本时更新 `<head>` JSON-LD 里 `SoftwareApplication.softwareVersion`。录屏说明中的
  v0.3.224 / `e53f0d3e` 是录制版本，不能跟着网站版本机械替换；只有重录后才更新
- 替换视频时同步 MP4、GIF、封面、中英 WebVTT、`docs/media/manifest.json`，以及 `docs/media/README.md` 的来源与文字稿。
  若补充 `VideoObject` 等结构化数据，时长、缩略图和发布时间必须来自真实成品及实际发布状态
- 改动品牌文案、下载入口或能力描述时，核对 `README.md` / `README_EN.md` 与安装指南的一致性；
  页面内容、截图和 JSON-LD 都只描述已经交付的功能。
