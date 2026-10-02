# OpenBiliClaw 主页与 SEO 维护

主页是 `docs/index.html`，通过 GitHub Pages 部署到
<https://whiteguo233.github.io/OpenBiliClaw/>。

本文覆盖首页信息层级、双语内容和搜索引擎维护。HTML 位于 `docs/index.html`，
主体样式与交互保留在 HTML 内联，媒体增补位于 `docs/assets/home.css`、`docs/assets/home.js`；meta / OG /
Twitter Card / JSON-LD / `sitemap.xml` / `robots.txt` 随站点一起维护。
本地改稿不代表已经发布或被搜索引擎收录。

> 站点托管在 GitHub Pages 的**子路径**
> `whiteguo233.github.io/OpenBiliClaw/` 下，根目录
> `whiteguo233.github.io/robots.txt` 不归本仓库管。所以本仓库的
> `docs/robots.txt` 只是冗余备份；真正起作用的是把 sitemap
> **手动提交到 Search Console / Bing Webmaster**。

## 首页与媒体维护约定

主页保留原版深灰、粉色与青色品牌、Hero 截图、卖点、学习闭环、平台卡片、五种使用入口、安装代码和 Star 叙事。
主体样式和中英翻译仍在 `docs/index.html` 内联维护；`docs/assets/home.css` 与 `docs/assets/home.js`
只承载新增录屏样式和播放器增强，不是另一套主页实现。

- Hero 只增加“看真实操作”入口，跳到原 `#screens` 产品区。
- 产品区补充桌面 25 秒、插件 18 秒、手机 Web 20 秒实录，保留原推荐、画像、价值、内容风格和聊天介绍，
  补兴趣探针与内容库截图。70 秒剪辑导览默认折叠，是次级入口，不能称为全程连续实录。
- 截图直接占满原有展示区域，可点击查看完整原图。功能图包含仓库既有截图；手机截图和视频是手机 Web，
  不能说是物理手机录制或 Flutter 客户端。
- 视频保留原生控件、`preload="none"`，无自动播放。启用 JavaScript 时，首次播放前用真实海报和可键盘操作的
  播放按钮提供入口，开始播放后回到原生控件，不重建媒体节点。中英切换只改字幕，不重置播放进度。
  关闭 JavaScript 后仍可阅读默认中文、播放原生视频和展开导览。
- 原版内容仅定点修正已知准确性边界：本地存储不等于所有模型调用都在本机，with-embedding 包不包含可用 LLM，
  v0.3.221 国内下载入口为历史版本。不要借素材更新重写定位、精简卖点或重新设计首页。

媒体来源、录制范围、版本、文字稿与文件规格见[素材说明](media/README.md)及
[`manifest.json`](media/manifest.json)。独立录制环境演示已有推荐上的浏览、反馈和本地保存，没有录制现场生成或画像学习。

本地用支持 HTTP Range 的静态服务检查视频播放和拖动：

```bash
python -c 'import uvicorn; from starlette.staticfiles import StaticFiles; uvicorn.run(StaticFiles(directory="docs", html=True), host="127.0.0.1", port=8847)'
```

默认正文是中文；`?lang=zh` / `?lang=en` 可固定预览语言。未指定参数时保留原版的已保存语言与浏览器语言选择。
必要验收包括中英首屏、手机宽度无溢出、产品区图片和视频、键盘播放、字幕切换、`#mobile-demo` 与 `#demo` 深链。
截图保存在忽略的 `output/playwright/`。检查内联 JavaScript 与 `assets/home.js` 的语法；静态内容契约见 `tests/test_docs_index.py`。

---

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
  `docs/index.html` 内联 `translations` 的中英文翻译项（page title、description、OG / Twitter 文案）；
  语言切换后的 metadata 应与当前正文一致，不能只更新可见标题
- 发布新版本时更新 `<head>` JSON-LD 里 `SoftwareApplication.softwareVersion`。录屏说明中的
  v0.3.224 / `e53f0d3e` 是录制版本，不能跟着网站版本机械替换；只有重录后才更新
- 替换视频时同步 MP4、GIF、封面、中英 WebVTT、`docs/media/manifest.json`，以及 `docs/media/README.md` 的来源与文字稿。
  若补充 `VideoObject` 等结构化数据，时长、缩略图和发布时间必须来自真实成品及实际发布状态
- 改动品牌文案、下载入口或能力描述时，核对 `README.md` / `README_EN.md` 与安装指南的一致性；
  页面内容、截图和 JSON-LD 都只描述已经交付的功能。
