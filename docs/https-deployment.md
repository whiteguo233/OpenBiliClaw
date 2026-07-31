# HTTPS 部署指南

> 本文档说明如何在 OpenBiliClaw 各类部署方式中外挂 HTTPS/TLS，使远程设备的浏览器扩展（extension）和 Web UI 能通过 HTTPS 安全连接后端。

## 为什么需要

浏览器扩展对公网 IP 的 HTTP 后端会强制拒绝（`https_required`），见 `extension/src/shared/backend-endpoint.ts:249`。如果你从**本机以外**的设备连接后端，extension 必须使用 HTTPS。

本机访问（`http://127.0.0.1:8420`）不受此限制。

## 适用场景

| 你的部署方式 | 推荐方案 | 说明 |
|-------------|---------|------|
| Docker Compose（项目内置） | **方案 A**：启用 `tls` profile | 最小配置，一行命令 |
| Docker（自管，非项目 compose） | **方案 B**：自建反代容器 | nginx/Caddy/任意 |
| pip install / 直接运行 | **方案 B**：自建反代 | nginx/Caddy 或裸跑代理脚本 + systemd |

---

## 方案 A：Docker Compose profile（推荐 Docker 用户）

项目 `docker-compose.yml` 内置了可选组件 `openbiliclaw-tls-proxy`，通过 Docker Compose profiles 控制启用。

```bash
# 普通启动（无 HTTPS）——和以前一样
docker compose up -d

# 启动并启用 TLS 代理 —— 新增
docker compose --profile tls up -d
```

代理监听 `:2119`，后端 `:8420` 不变。客户端访问：
- 网页：`https://<host>:2119/web`
- Extension 后端地址：`https://<host>:2119`

### 证书管理

代理启动时：
1. 检测 `openbiliclaw_certs` 卷中是否存在 `srv.crt` + `srv.key`
2. 存在 → 直接使用
3. 不存在 + 环境变量 `AUTO_GEN_CERTS=1`（默认）→ 自动生成自签 CA + 服务器证书

**首次使用需在每台客户端信任 CA**：

从 `https://<host>:2119/ca.crt` 下载 CA 证书，导入系统信任库。

Windows：
```
双击 ca.crt → 本地计算机 → 将证书放入「受信任的根证书颁发机构」→ 完成
```

Linux（Chrome/Chromium）：
```bash
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n obc-ca -i ca.crt
```

### 使用自己的证书

```bash
# 将你的证书文件放入卷（只需一次）
docker run --rm \
  -v openbiliclaw_certs:/dst \
  -v /你/证书/目录:/src:ro \
  busybox cp /src/srv.crt /src/srv.key /src/ca.crt /src/ca.crl /dst/

# 然后正常启动
docker compose --profile tls up -d
```

---

## 方案 B：自建反代（非 Docker 用户 / 自管部署）

### B-1：直接运行代理脚本（零依赖，仅需 Python 3.11+）

`docker/openbiliclaw_tls_proxy.py` 可以脱离 Docker 直接运行：

```bash
pip install cryptography         # 唯一额外依赖
export BACKEND_HOST=127.0.0.1    # 后端地址
export BACKEND_PORT=8420
export CERT_DIR=/etc/obc-certs   # 证书存放目录
export AUTO_GEN_CERTS=1          # 首次运行自动生成证书
python docker/openbiliclaw_tls_proxy.py
```

配合 systemd（`/etc/systemd/system/openbiliclaw-tls-proxy.service`）：

```ini
[Unit]
Description=OpenBiliClaw TLS Proxy
After=network.target

[Service]
Type=simple
User=nobody
Environment="BACKEND_HOST=127.0.0.1"
Environment="BACKEND_PORT=8420"
Environment="CERT_DIR=/etc/obc-certs"
Environment="AUTO_GEN_CERTS=1"
ExecStart=/usr/bin/python3 /opt/OpenBiliClaw/docker/openbiliclaw_tls_proxy.py
Restart=always

[Install]
WantedBy=multi-user.target
```

### B-2：nginx

```nginx
server {
    listen 2119 ssl;
    ssl_certificate     /etc/obc-certs/srv.crt;
    ssl_certificate_key /etc/obc-certs/srv.key;

    location /ca.crt {
        alias /etc/obc-certs/ca.crt;
    }
    location /ca.crl {
        alias /etc/obc-certs/ca.crl;
    }

    location / {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header Host $http_host;
        proxy_set_header Origin http://$http_host;  # 关键：scheme 改写
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;      # WebSocket
        proxy_set_header Connection "upgrade";
    }
}
```

> 注意 `proxy_set_header Origin http://$http_host` —— 后端根据 Origin 判断同源，必须改 scheme 为 `http` 否则网页登录报 `origin_forbidden`。细节见 `~/.openbiliclaw-caddy/README.md`（该目录可能已清理）。

### B-3：Caddy

```caddy
:2119 {
    tls /etc/obc-certs/srv.crt /etc/obc-certs/srv.key

    handle /ca.crl {
        root * /etc/obc-certs
        file_server
    }

    handle /ca.crt {
        root * /etc/obc-certs
        file_server
    }

    handle {
        reverse_proxy http://127.0.0.1:8420 {
            header_up Host {http.request.host}
            header_up Origin http://{http.request.host}
        }
    }
}
```

---

## 常见问题

### Q: 同台机器能同时用 HTTP 和 HTTPS 吗？

能。后端 `:8420` 不受影响，本机 extension 继续用 `http://127.0.0.1:8420`。

### Q: 代理占用了 2119，能改端口吗？

设置 `LISTEN_PORT` 环境变量并修改 compose 的端口映射。例如 8443：

```yaml
environment:
  LISTEN_PORT: "8443"
ports:
  - "8443:8443"
```

### Q: 证书过期怎么办？

代理-生成的有效期 3650 天。若需重签：

```bash
docker volume rm openbiliclaw_certs           # 删旧
docker compose --profile tls up -d             # 重启生成新证书
# 重新下载 ca.crt 并导入每台客户端
```

若使用的自己的证书，在卷中用新文件覆盖旧文件后重启代理即可。
