# 插件设置布局与推荐头图延迟验收（2026-10-23）

## 范围与复现

分支 `fix/extension-layout-cover-latency`，基于上次四项修复的验收分支 `d4234371`。主工作区、日常服务和用户数据未修改。性能样本从现有 SQLite 只读选取图片 URL，在独立临时缓存、API 与图片服务中执行；未使用生产登录凭据，也未触发评论或私信。验收机器为 macOS；Windows 打包程序和用户那台机器的网络尚未实测。

### 插件弹窗

400×600 Chromium 渲染真实 popup HTML/CSS，设置分类区域 clientWidth=360、scrollWidth=590；固定六列而实际有七个分类，日志另起一行，后续分类被挤出可视区域。新增浏览器回归修复前在 320/375/400 宽度触发溢出断言；600 宽度另有一次 1 秒点击稳定性等待超时，后续改为 5 秒，不能把该超时算作布局复现。

修复为四列（≤360px 时三列）的响应式网格，取消窄屏强制 92px 列宽。400 宽度下 clientWidth 与 scrollWidth 均为 360；七个面板逐一显示检查，无输入框或按钮横向溢出。320、375、400、480、600 五种宽度的分类可见与点击检查通过。

此处使用真实浏览器渲染静态插件 DOM/CSS，脚本启动依赖被移除；插件实际 MV3 安装、浏览器原生工具栏和 Windows 字体不是本次浏览器测试覆盖项。补充执行插件正式构建与设置相关脚本测试。

| 修复前 | 修复后 |
| --- | --- |
| ![修复前](assets/2026-10-23-popup-cover-latency/popup-before.png) | ![修复后](assets/2026-10-23-popup-cover-latency/popup-after.png) |

### 头图延迟

先按生产 `ImageFetchCoordinator` 路径请求真实 CDN 样本：成功冷加载约 0.8–1 秒，磁盘原图命中约 16–20ms。两个旧小红书签名链接返回 403（API 映射为 502）；它们是失效链接，不用来计算加速比例，也未声称修复。

定位到两处重复工作：

1. 已缓存的原图每次响应仍执行 Pillow 解码、640px 缩放和 JPEG 压缩。对同一真实样本连续两次处理，第二次仍需约 172–692ms；回归测试证明同一原图被打开两次。
2. 主 API 每张图创建并销毁 HTTPX AsyncClient，重复初始化客户端和丢弃 keep-alive 连接。三次转发的回归测试修复前创建三个客户端，修复后一个，应用 shutdown 时确认关闭。

实现：API 内部转发复用 app-owned 连接池，内部 hop 不继承系统代理；API 进程内模式和独立 image_service 共享 `render_cover_for_display`。缩图缓存用原字节 SHA-256 与类型作为键，只保留显示副本，LRU 上限 128 项 / 16 MiB。内容变化自动失效，不保留原图缓冲，不改变原图磁盘缓存、URL 白名单、公网地址固定、TLS、媒体类型与大小校验。首次缩图仍需执行，超大条目不纳入内存缓存，异常仍回退原字节。

## 真实请求前后对比

六张相同真实 B 站封面，API HTTP → 独立图片服务 → 磁盘原图 → 显示副本，均返回 HTTP 200。源图大小约 67–772KB，响应约 25–77KB。测量客户端强制发起 HTTP 请求；浏览器验证添加独立 probe 参数，避免浏览器缓存掩盖后端耗时。

| 链路 | 修复前第二轮整批 | 修复后第二轮整批 |
| --- | ---: | ---: |
| 直接图片服务 | 601ms | 176ms |
| 经主 API 转发 | 2038ms | 255ms |

经主 API 的这一组热缓存样本约缩短 87%。修复后首次启动图片服务、尚未生成显示副本的一轮仍需 1573ms；不能把热缓存收益外推成首次 CDN 下载或冷进程的收益。机器同时运行其他应用，绝对耗时受负载影响，不是独立性能实验室的 SLA。

真实 Chromium 再次请求并解码六张图：两轮整批 284ms / 238ms，全部 naturalWidth=640，解码成功。这里测的是图片请求与解码，不包含推荐算法耗时，也不是用户 Windows 环境的端到端时间。

另实际停止独立图片服务，再次从主 API 请求封面：回退进程内抓取与缩图成功，HTTP 200，约 1810ms，返回 76653 字节 JPEG；没有等满原来的 30 秒连接预算。

结构化计时：[timings.json](assets/2026-10-23-popup-cover-latency/timings.json)。

## 验证与交付

- 图片 API、原图缓存、协调器、网络安全边界、缩图缓存：120 passed。
- 缩图及浏览器布局专项：9 passed（4 项缩图 + 5 种屏宽）。
- 转发池关闭生命周期补充回归：1 passed。
- 插件设置、滚动、后端配置脚本：44 passed。
- 插件 build（TypeScript 与 bundle、22 项 manifest 资产检查）通过。
- Ruff 全部通过；MyPy 检查 361 个源文件通过（修正 Pillow ImageFile/Image 类型标注后重跑）。

复现命令（使用已安装项目依赖的虚拟环境）：

```bash
PYTHONPATH=src pytest tests/test_api_image_proxy.py tests/test_image_render_cache.py tests/test_image_fetch.py tests/test_image_cache.py tests/test_image_network_boundary.py -q
PYTHONPATH=src pytest tests/test_popup_settings_layout_e2e.py -q
ruff check src/ tests/
mypy src/
npm --prefix extension run build
cd extension
node --test --experimental-strip-types tests/popup-settings.test.ts tests/popup-scroll.test.ts tests/popup-backend-config.test.ts
```

测试服务、专用浏览器及临时图片目录在验收后清理。此分支包含之前四项修复，但尚未合入 main，也未发布安装包或插件新版。
