# 手机扫码连接

本次功能需要更新后端 / 插件，并使用支持「扫码连接」的新版原生 App。发布前，旧 App 仍可手动填地址；旧手机浏览器继续能打开普通 `/m/` 链接。

在电脑桌面 Web 或浏览器插件顶部打开「手机版」，选择手机能访问的网络：

| 地址选项 | 手机条件 | 扫码结果 |
| --- | --- | --- |
| 局域网 | 与电脑在可互访的局域网 | 相机打开 Mobile Web；App 设置「扫码连接」保存后端并重连 |
| Tailscale · 系统节点 | 两端系统 Tailscale 已加入同一 tailnet | 浏览器 / App 通过系统网络直接访问 |
| Tailscale · 应用内节点 | 电脑 helper 已 ready，App 内置 Tailscale 登录同一 tailnet | App 自动选择内置 Tailscale；手机浏览器需另外连接系统 Tailscale |
| 公网 / 自定义地址 | 已有手机可达的 HTTP(S) 地址，推荐 HTTPS | 保留协议、端口、代理路径前缀；相机打开网页，App 导入后端 |

App 操作：进入连接设置 →「扫码连接」→ 对准电脑二维码。识别成功后自动保存并重连，之后重新打开 App 仍保留地址。相机不可用时可以复制电脑链接，用「粘贴连接链接」导入。首次使用内置 Tailscale 仍需登录；后端若开启密码门禁，连接后仍需输入密码。

新安装默认监听 `0.0.0.0:8420`，包含桌面安装包、一键脚本和 AI 安装。该值表示监听网卡，不是可扫码的访问地址。本机浏览器和健康检查使用 `127.0.0.1` 正常，但不能发给手机。已有配置明确设为仅本机时会保留，并在 LAN 二维码处提示改 `[api].host` 后重启。

每次打开都重新检测 LAN / 系统 Tailscale 地址。没有可用地址时不生成回环二维码，不复用旧 Wi-Fi 地址。公网域名无法从本机自动推断时，在自定义地址填一次已有入口即可生成二维码；功能不创建公网隧道、开放路由器端口或配置 Funnel。

Docker 中的网卡地址通常属于容器。请在自定义地址输入宿主机的可达地址与发布端口，或已有 HTTPS 域名；容器内无法保证自动探测宿主机系统 Tailscale。

## 协议

`GET /api/qr-info` 保留旧 `lan_ip` 字段，响应 `Cache-Control: no-store`，新增：

```json
{
  "lan_ip": "192.168.1.7",
  "api_host": "0.0.0.0",
  "api_port": 19090,
  "tailscale_addresses": [
    {"host": "100.100.1.2", "port": 19090, "source": "system"},
    {"host": "100.100.1.3", "port": 8421, "source": "embedded"}
  ]
}
```

普通链接：`http://192.168.1.7:19090/m/`。

应用内节点：`http://100.100.1.3:8421/m/?obc_connection=tailscale`。

代理路径：`https://obc.example.com/prefix/m/` → App 保存 `https://obc.example.com/prefix`，业务请求走 `/prefix/api/...`。URL 查询和 fragment 不会保存为后端路径。系统 Tailscale 与局域网使用 App 直接连接，应用内节点才选择内嵌连接。

地址发现不读取模型，不注册节点，不返回 Auth Key、密码或登录会话。二维码在本机生成，不提交给第三方服务；QR v5 / v10 支持最多 271 UTF-8 字节，超长地址显示复制入口。扫码只导入连接参数，保持已有鉴权流程。

## 验证

- `tests/test_mobile_connection_info.py`：有效监听参数、系统 / 应用内节点隔离、inactive 状态、LAN 排除 Tailnet、QR v5 / v10 与 Python 编码器逐格比对。
- `tests/js/mobile-network-qr.test.mjs`：保存的回环地址覆盖 LAN 的原始复现、无地址时不绘码、HTTPS / IPv6 / 代理路径、三种网络与监听边界。
- `tests/test_mobile_connection_live.py`：显式指定实际后端后，真实发现 → 共享 JS 地址与 QR 编码 → 生成地址的 HTML / 静态资源 / 业务请求；不注入接口响应。
- App `backend_connection_link_test.dart`、`settings_qr_connection_test.dart`：解析、扫码保存、重连、持久化和连接模式；原有手动配置回归继续保留。

网络是否允许手机访问仍取决于 Wi-Fi 互访、系统防火墙和 tailnet grants。自动化测试不等于真实跨设备网络或手机相机验收；联调结果见[分支验收记录](verification/2026-10-26-mobile-connection.md)与[真实环境验证](verification/2026-10-27-mobile-connection-live.md)。

官方参考：[Tailscale 地址范围](https://tailscale.com/docs/reference/reserved-ip-addresses)、[mobile_scanner](https://pub.dev/packages/mobile_scanner)。
