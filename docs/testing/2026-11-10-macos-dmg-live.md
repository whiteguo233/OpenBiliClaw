# macOS DMG 安装与真实请求验收（2026-11-10）

验收包从拖拽引导修复 `21794e52` 和仅增加验收步骤的提交 `a9ffe6b1` 构建。隔离 macOS 运行器检查最终 DMG 的签名、公证、Gatekeeper 及实际挂载卷的 Finder 布局，再复制应用到 `/Applications` 并通过 Launch Services 打开。首次启动使用隔离配置及回环监听。测试使用真实的云端向量探测、免费模型 SSE 请求和持久化会话，重启后再次读取同一回复。[Actions 运行](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42821783221)

| 环境 | 安装与正常启动 | 真实请求 |
| --- | --- | --- |
| Apple Silicon，macOS 15.8.2 | 完整公证 DMG 与应用通过校验。挂载后只有两个可见图标，左右位置和背景引用符合预期。安装后 5.15 秒开始响应。 | 向量探测就绪。31.61 秒收到 11 个 SSE 增量和完整回复，重启后读回相同对话。 |
| Intel，macOS 15.7.9 | 完整公证 DMG 与应用通过校验。挂载布局与 Apple Silicon 一致。安装后 9.34 秒开始响应。 | 向量探测就绪。12.14 秒收到 7 个 SSE 增量和完整回复，重启后读回相同对话。 |
| Intel，macOS 26.5.1，本机同一份新公证包 | 与运行器 DMG 摘要一致。镜像和隔离安装副本均通过签名、公证与 Gatekeeper。直接启动安装副本的默认菜单栏入口 185 秒内未监听。采样停在 PyObjC 的 `ffi_closure_alloc`。 | 前台对照向量就绪，2.97 秒收到 10 个 SSE 增量和完整回复，重启后读回对话。 |
| Intel，macOS 26.5.1，本机 v0.3.235 布局预览包 | 从真实挂载卷复制应用到临时目录，应用签名、公证票据和 Gatekeeper 校验通过。默认启动 185 秒内未监听。 | 前台模式第一次流式请求在上游首包超时。隔离环境复测向量就绪，3.30 秒返回 10 个 SSE 增量及正常终态，重启后读回对话。 |

本机已有应用保留在原处。Finder 实机截图受锁屏限制。运行器的布局结论基于签名 DMG 的只读挂载和实际 Finder 元数据，不把挂载检查当成手动拖拽录像。测试产物保存在 Actions，正式发布仍待发版。

[Apple Silicon 证据](assets/macos-dmg-live-2026-11-10/arm64.json) · [Apple Silicon 公证测试包](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42821783221/artifacts/13767119694) · [Intel 证据](assets/macos-dmg-live-2026-11-10/x64.json) · [Intel 公证测试包](https://github.com/whiteguo233/OpenBiliClaw/actions/runs/42821783221/artifacts/13769981340) · [本机证据](assets/macos-dmg-live-2026-11-10/local-intel.json)
