# HTTPS 部署指南

> 使远程设备的浏览器扩展和 Web UI 能通过 HTTPS 连接后端。

## 为什么需要

浏览器扩展对公网 IP 的 HTTP 后端强制拒绝（`https_required`）。本机 `http://127.0.0.1:8420` 不受限制。

## Docker 部署

项目 `docker-compose.yml` 内置可选代理容器，通过 profile 启用：

```bash
docker compose --profile tls up -d    # 代理容器 + 后端一起启动
```

代理监听 `:2119`，后端 `:8420` 不变：
- 网页：`https://<host>:2119/web`
- Extension：`https://<host>:2119`

## 非 Docker 部署

```bash
pip install "openbiliclaw[tls]"        # 安装 TLS 依赖（一次）
openbiliclaw tls-proxy enable          # 写入 config.toml，之后 serve-api 自动启动代理
openbiliclaw serve-api                 # 启动 API（代理线程自动跟随）
```

> `tls-proxy status` 查看，`tls-proxy disable` 关闭。代理随 `serve-api` 退出自动停止。

## 证书

代理启动时自动检测 `cert_dir`（默认当前目录）：
- 已有 `srv.crt` + `srv.key` → 直接使用
- 不存在 → 自动生成自签 CA + 服务器证书（RSA 2048，SAN: sushe/localhost/127.0.0.1/当前IP，3650 天）

### 客户端信任 CA

从 `https://<host>:2119/ca.crt` 下载，导入系统信任库：

- **Windows**：双击 `ca.crt` → 本地计算机 → 「受信任的根证书颁发机构」
- **Linux（Chrome）**：`certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n obc-ca -i ca.crt`

### 使用自己的证书

`cert_dir` 目录下放好 `srv.crt` / `srv.key`（以及可选 `ca.crt` / `ca.crl`），代理启动时自动检测到便跳过生成。

Docker 用户：

```bash
docker run --rm -v openbiliclaw_certs:/dst -v /你/证书/目录:/src:ro \
  busybox cp /src/srv.crt /src/srv.key /dst/
```

非 Docker 用户在 `config.toml` 中设置 `[tls_proxy].cert_dir` 指向证书目录。

## 常见问题

### 本机能同时用 HTTP 吗？

能。`http://127.0.0.1:8420` 不受影响。

### 改 HTTPS 端口？

修改 `config.toml` 中的 `[tls_proxy].port`（默认 2119），重启。Docker 用户同步改 compose 端口映射。

### 证书过期？

代理生成的有效期 3650 天。重签：删除 `cert_dir` 下的证书文件，重启自动生成新证书，重新下载 `ca.crt` 到客户端。
