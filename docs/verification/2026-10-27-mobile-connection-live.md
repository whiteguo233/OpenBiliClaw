# 手机扫码连接：真实环境验证（2026-10-27）

本记录补充 [上一轮验证](2026-10-26-mobile-connection.md)。本轮 HTTP 请求访问实际运行的后端，桌面使用真实页面，不使用合成业务接口或 Chrome storage stub。原生与手机网页的设备测试入口位于配套 App PR。

## 环境与隔离

- 保留现有服务：它以显式 `--host 127.0.0.1 --port 18420` 启动，只能本机访问。这与新安装默认监听地址是两回事。
- 独立分支运行正常 CLI / `create_app()`，无组件注入。SQLite backup 复制约 217 MiB 的真实业务数据库，同时复制画像和图片缓存，继续使用已有真实服务配置；测试数据与原库分离，关闭自动调度和更新。
- 测试 API 使用端口 18422，`lsof` 实际确认 IPv4 `*:18422` 与 IPv6 `*:18422` 均监听。
- 系统 Tailscale 测试前为 Stopped，测试期间连接原有账户。另一个已在线的应用内 tsnet 节点转发原有服务；没有复制节点身份、创建新身份或启用公网 Funnel。
- 配置、原始响应、节点身份、真实二维码与详细日志只保存在本机临时目录，不提交仓库。

测试后已将系统 Tailscale 恢复为原来的 Stopped；随后真实 `/api/qr-info` 立即不再返回系统节点地址，LAN 与实际监听端口保留，验证没有使用 daemon 的历史 IP 或 health 缓存。

## 真实请求结果

每条成功路径均请求 `/m/`、`/api/ping`、`/api/health`、`/api/recommendations`，实际返回 HTTP 200，健康状态为 `ok`，推荐响应有 20 条实际数据。共 16 次成功请求，不把响应正文或个人内容写入报告。

| 路径 | 结果 | 范围 |
| --- | --- | --- |
| LAN IPv4 → 分支 API | PASS | 本机通过 LAN 地址请求；真机另记 |
| 系统 Tailscale IPv4 → 分支 API | PASS | 当前系统节点地址及实际端口 |
| 系统节点 → 已在线内置节点 IPv4 | PASS | 两个独立节点的实际 tailnet 传输 |
| 系统节点 → 已在线内置节点 IPv6 | PASS | 实际 IPv6 tailnet 请求 |
| 系统 Tailscale IPv6 → 本机分支 API | TIMEOUT | 本机自连超时，不能把地址发现当成可达性证明 |
| 公网 | 未验证 | 当前无 Serve / Funnel 配置，也没有可用公网后端 URL |

两个 tailnet 节点运行在同一台电脑上；本轮验证了真实节点身份和传输，没有验证手机切换蜂窝网络后的跨物理网络连接。

额外的可重复执行验收：

```bash
PYTHONPATH=src OPENBILICLAW_LIVE_BASE_URL=http://127.0.0.1:18422 \
  python -m pytest -q tests/test_mobile_connection_live.py
```

实际结果 **1 passed，35.21 秒**：真实 `/api/qr-info` → Node 执行仓库共享连接模块与桌面 QR 编码器 → 沿其生成的 LAN / 系统 Tailscale IPv4 URL 请求实际手机 HTML、JS / CSS、ping、health、推荐库存。没有 HTTP mock，也没有 FastAPI TestClient / 组件注入；此入口验证请求与资源交付，不代替浏览器渲染。未提供环境变量时会跳过，不能算通过。旧内置节点的 GET 推荐接口可能记录展示；主验收使用隔离快照。

## 真实桌面与二维码

桌面从 loopback 页面打开「手机版」，实际 `/api/qr-info` 返回 LAN、系统 Tailscale IPv4 / IPv6 和有效监听参数。二维码分别使用真实 LAN / Tailscale IPv4 与 18422 端口，没有绘制 `127.0.0.1`。从真实 DOM 导出 QR SVG 后栅格化为 PNG，作为原生测试输入；不是用测试常量重新编码。

手机网页入口及 CSS、JS、共享模块的真实请求均为 200。浏览器自动化在手机页 DOM / 截图采集时失去响应，HTTP 成功不单独等同于手机浏览器完整渲染通过；设备 WebView 验证另记。

## 设备测试与边界

配套 App 新增显式选择运行的 `backend_qr_live_test.dart`：原生 PNG 解码 → 真实 `SettingsView` 导入 → 持久化 → 推荐轮询 → 轻量连通性请求 → 新客户端恢复后的 ping。它使用实际 HTTP、存储和 Tailnet 服务，完成后还原连接设置，不执行 Tailnet logout。

`mobile_web_connection_live_test.dart` 使用设备 WebView 加载同一二维码 URL，等待实际 API 结果渲染成推荐卡片，不注入应用状态或替换网络响应。

实体 iPhone（iOS 18.5）签名构建、安装完成；无线调试 75 秒内未发现 Dart VM，Flutter 提示需允许本地网络访问。已请求用户完成系统权限，未经设备结果不能记为真机 PASS。相机实拍需要用户拿起手机对准实际二维码，也不由图片解码测试代替。

本轮设备运行明确保留为未完成：macOS 已对真实 LAN PNG 原生解码成功，但前台窗口未激活，UI 阶段未完成；后续 transport-only 构建、Android 原生 / iOS WebView 运行因本机构建资源压力停止，没有测试通过结论。并行构建一度造成严重内存与 swap 压力，已停止本次构建、编译器及模拟器；不以环境中断推导产品功能失败或通过。新增三个 Dart 测试 / driver 文件最终分析无问题。

轻量回归：共享 JS 12 passed；Python 连接发现 / QR 编码 10 passed，显式真实测试未传地址的一轮为 1 skipped（与上面的真实运行分别记录）。Ruff 与改动检查通过。

## 真实请求发现的 App 连接误判

配套 Flutter HTTP 测试首轮失败：`checkHealth()` 在 5 秒内未确认成功。同址连续测量 `/api/ping` 为 21 ms / 9 ms，`/api/health` 冷探测 15091 ms、缓存命中 10 ms。说明后端可达时，模型就绪探测仍可能超过 App 的连接检测超时。

App 分支修复 `ApiClient.checkHealth()` 优先请求 provider-independent `/api/ping`；仅旧后端 404 时回退 `/api/health`，不延长超时或掩盖认证 / 服务错误。回归先得到 2 failed / 3 passed，覆盖冷依赖超时与旧后端前缀；修复及最终真实请求结果在配套 App 验证记录中维护。

修复后相关 API / 设置 / 扫码回归 **30 passed**，同址 Flutter 真实 HTTP 脚本复跑 **1 passed**；脚本的存储隔离替身不算实体设备持久化验收。App 最终完整 `flutter analyze` 无问题；本轮修复后原生重建 / UI / 相机步骤未完成，不能沿用早期构建结果作为最终原生验证。

设备测试最终结果见配套 App 的 `docs/backend-connection-qr.md`；未发布或替换现有正式安装包。
