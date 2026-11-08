# 手机 App 从图片扫码真实验收（2026-10-30）

按用户要求仅用模拟器，新增扫码页右下角「选择图片」，并实际从图库选择二维码截图。局域网、系统 Tailscale IPv4、既有 Tailscale 对端三条链路均完成原生解码、自动保存、真实推荐与保存后连接检查。没有操作实体手机。

## 输入与环境

- 后端分支：`codex/mobile-network-qr`；App 变更在独立 `codex/mobile-qr-image-import` worktree，基于 App 扫码分支 `17984e9`。两仓库均未合入 main 或发布安装包。
- 正常 CLI 使用约 216.8 MiB 的真实 SQLite 隔离快照，监听 `0.0.0.0:18422`，临时图片服务端口 `18423`。前两项访问该服务；既有对端访问原来的 `18420` 服务。未替换 HTTP 或推荐提供者，未覆盖日常配置或数据库。
- 二维码由实际 `/api/qr-info` 地址和既有对端地址，经 Web / 插件共用 `src/openbiliclaw/web/desktop/assets/js/mobile-qr.js` 编码。将二维码在模拟器原生图库显示后截取整屏，再把这些真实截图导入独立测试相册。不是给测试回调直接传 URL。
- Android API 31 AOSP ATD、x86_64、720×1280，独立临时 AVD。ATD 没有图库，本轮安装 [Fossify Gallery 1.14.1 官方发布](https://github.com/FossifyOrg/Gallery/releases/tag/1.14.1) 作为 `ACTION_GET_CONTENT` 的图片处理器，只选择媒体访问。官方 x86_64 APK 的 SHA256 与发布元数据一致：`55f4d504182c8e0c26a1af8c362635b05b7653a051b55e2d59e8c2f50dad2d60`。该工具仅用于验收环境，不是产品依赖。

## 实际链路与结果

```text
真实地址 → Web/插件共享 QR 编码器 → 模拟器图库显示 → 截图
  → 真实 SettingsView → URL 右侧扫码图标 → BackendScanView
  → 右下角「选择图片」→ Android 图库中选择实际截图
  → mobile_scanner 原生本地解码 → 既有地址校验 / 保存 / 重连
  → 真实推荐 / ping → 重新读取磁盘设置 → 新客户端 ping
```

`OBC_LIVE_QR_GALLERY=true` 使用生产扫码页面，没有替换图片选择结果、原生解码器、扫描回调、HTTP、存储或 Tailnet 服务。Flutter 测试点击产品入口，宿主根据实时 Android UI 树点击相册和唯一的测试图片。每项从不可达旧地址开始，检查目标协议、主机、端口和连接模式；结束后还原原有 App 连接设置。

| 截图地址 | 识别 / 保存 | 推荐 | ping / 重新读取保存设置后 ping |
| --- | --- | --- | --- |
| 当前局域网 IPv4 | 通过 | HTTP 200，20 条 | 均通过 |
| 当前系统 Tailscale IPv4，深色界面 | 通过，普通直接连接 | HTTP 200，20 条 | 均通过 |
| 既有 Tailscale 对端 IPv4 | 通过，普通直接连接 | HTTP 200，20 条 | 均通过 |

一次完整设备矩阵运行约 **3 分 5 秒**，driver 退出码 **0**。日志依次包含三个标签的 `saved; real recommendations 20; liveness/ping PASS`，最终 `All tests passed`。公开截图只保留不含真实地址 / QR 内容的扫码页，位于配套 App 的 `docs/images/connection-qr-image-picker-android.png`。

## 验证与问题定位

- App 新选图 widget 回归 **9 passed**：成功、取消、无效图片 / 地址、相机拒绝、拒绝后取消不再次请求权限、读取错误重试和生命周期 / 旧相机回调。它们使用原生插件边界替身，与上面的真实设备验收分开记录。
- 原有解析、扫码设置、手动 URL、轻量连接回归 **45 passed**；本轮合计 54 项相关单元 / widget 回归，没有声称重跑完整 App 套件。
- 完整 `flutter analyze --no-pub` 无问题；最后验收等待调整后，改动文件的 `dart analyze` 再次无问题；最终 Android x64 debug 构建通过，iOS `Info.plist` 语法检查通过。本轮未完成新的 iOS 原生构建。
- 后端显式真实 HTTP 测试 `tests/test_mobile_connection_live.py`：**1 passed，14.29 秒**。覆盖实际地址发现、共享 QR 模块、手机 HTML / 静态资源和 API；该结果单独不等于浏览器实际渲染或原生 UI 通过。
- ATD 默认 `debug.hwui.drawing_enabled=0`，导致原生图库不绘制。本轮独立 AVD 启用绘制后实际选图界面可见。此前把黑屏完全归因于系统 PhotoPicker 不准确。
- 原验收等待使用 Flutter `pump()`，原生图库在前台时 Flutter 暂停出帧，因此超时。改用 `Stopwatch` 和真实事件循环等待，保留实际 OS 选图及原生解码，未注入结果绕过失败。

## 验收范围

系统 Tailscale 与既有对端节点位于同一台 Mac，Android 模拟器使用宿主 NAT / 系统 VPN 路由。此次通过的是实际图库截图识别和上述可达地址的真实网络请求，没有验证蜂窝网络、Android 系统 Tailscale 首次登录或 App 内嵌 Tailnet 新身份入网。

没有可用的公网后端 URL、Serve 或 Funnel，本轮没有创建公网暴露，不能把 `100.x` 私网地址记为公网通过。「Vibe」是否指本项目 Web 尚未明确，未提供的外部二维码不在验收内。

本次图片入口不等于实体摄像头光学扫描通过。前轮软件相机裁切问题保留在 [2026-10-28 记录](2026-10-28-mobile-qr-e2e.md)，没有宣称修复。`mobile_scanner` 不支持 iOS 模拟器图片分析，本轮不宣称 iOS 原生解码或手机浏览器实际卡片渲染通过。

## 环境恢复

系统 Tailscale 已恢复为最初的 `Stopped`；停止临时后端前，真实 `/api/qr-info` 中系统 Tailscale 地址数已回到 0。临时 `18422` / `18423` 服务和本轮唤醒断言已停止，原有 `18420` / `18421` 服务仍在。独立测试 AVD 已删除；原 ATD 的分辨率 / 密度覆盖已清除，恢复物理 1080×1920 / 420 dpi，屏幕超时恢复原值后停止模拟器。没有删除已有节点身份或用户 AVD 数据。
