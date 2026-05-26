# Phase: Image Proxy — Specification

**Created:** 2026-05-21
**Updated:** 2026-05-21
**Ambiguity score:** 0.10 (gate: ≤ 0.20)
**Requirements:** 6 locked

## Goal

推荐卡片、惊喜推荐和消息内封面图从直连各平台 CDN 改为经本地后端代理加载，覆盖 mobile web (`/m/`) 和浏览器扩展 side panel，解决跨域防盗链导致的图片加载失败问题（当前小红书 mobile web 图片被主动屏蔽，其他平台和 extension 仍依赖直连 + `no-referrer`，稳定性不足）。

## Background

当前 mobile web `getCoverImageAttrs()`（`src/openbiliclaw/web/js/view-models.js`）直接返回原始 CDN URL + `referrerpolicy="no-referrer"`；`normalizeCoverUrl()` 会直接屏蔽 `.xhscdn.com`，导致小红书封面完全不展示。加载失败时部分模板用 `onerror="this.remove()"` 静默移除 `<img>`，会让卡片布局塌缩或信息缺失。

浏览器扩展 side panel 也在 `extension/popup/popup.js` 中直接设置 `image.src = item.cover_url` 并设置 `image.referrerPolicy = "no-referrer"`。扩展已有 fallback 容器，但请求本身仍直连 CDN。

后端 FastAPI（`src/openbiliclaw/api/app.py`）目前无图片代理端点。`httpx>=0.27` 已在 `pyproject.toml` 依赖中，可直接用于服务端请求。

## Requirements

1. **代理端点**: 后端提供 HTTP 图片代理，服务端拉取远程图片并以 `StreamingResponse` 返回给前端。
   - Current: 后端无图片代理端点，前端直连各平台 CDN。
   - Target: `GET /api/image-proxy?url=<encoded_url>` 接受 URL-encoded 的图片地址；mobile web 使用同 origin 相对路径，extension 使用当前配置的 backend origin 拼接绝对 URL。
   - Acceptance: 对白名单内的 B 站图片 URL 发起 `GET /api/image-proxy?url=...`，返回 200 + `Content-Type: image/*` + 图片二进制数据 + `Cache-Control: public, max-age=86400`。

2. **URL 与域名白名单**: 代理只允许明确合法的 HTTP(S) 图片 URL，拒绝其他域名或非 HTTP(S) scheme。
   - Current: 无域名校验逻辑。
   - Target: 白名单后缀列表至少包含 `hdslb.com`、`xhscdn.com`、`pstatp.com`、`douyinpic.com`、`douyinvod.com`、`ytimg.com`、`ggpht.com`。匹配必须按域名边界判断：`host == suffix or host.endswith("." + suffix)`，不能让 `evilhdslb.com` 通过。
   - Target: URL 必须有 `http` 或 `https` scheme、非空 hostname，且不得包含 username/password userinfo。
   - Acceptance: 白名单内子域名返回 200；`example.com` 返回 403；`ftp://i.hdslb.com/a.jpg`、`not-a-url`、`https://user:pass@i.hdslb.com/a.jpg` 返回 400。

3. **Redirect 安全**: 代理不能因上游跳转绕过白名单。
   - Current: 无此逻辑。
   - Target: 禁用 `httpx` 自动跳转；最多手动跟随 3 次 `301/302/303/307/308`，每一次 `Location` 解析后的 URL 都必须重新通过 URL 与域名白名单校验。
   - Acceptance: 白名单 URL 跳转到 `https://example.com/a.jpg` 返回 403；跳转循环或超过 3 次返回 502。

4. **安全限制**: 代理对响应状态、内容类型、响应体大小和超时做校验，防止滥用。
   - Current: 无此逻辑。
   - Target: 只转发 `Content-Type` 为 `image/*` 的 2xx 响应（大小写不敏感，允许 `; charset=` 等参数）；否则返回 400（非图片）或 502（非 2xx/上游异常）。响应体实际读取超过 10MB 时返回 413；请求总超时 10 秒。
   - Target: 上游读取必须用 `httpx.AsyncClient` 的 `stream=True`，但为了在无 `Content-Length` 或伪造长度时仍能返回干净的 413，响应先写入 `tempfile.SpooledTemporaryFile(max_size=1MB)`，最多读取 10MB，验证通过后再从 spool 以 `StreamingResponse` 返回；不能把超过 10MB 的响应读入内存。
   - Acceptance: 返回 `text/html` 的 URL 代理返回 400；`Content-Length > 10MB` 立即返回 413；缺失/伪造 `Content-Length` 但实际超过 10MB 时也返回 413；10 秒无响应返回 504。

5. **前端走代理并保留占位符**: mobile web 和 extension 的封面图加载改为通过后端代理，不再直连 CDN；失败时显示占位符而非移除布局区域。
   - Current: mobile web `getCoverImageAttrs()` 返回 `{ src: originalUrl, referrerPolicy: "no-referrer" }`；`normalizeCoverUrl()` 屏蔽 `.xhscdn.com`；extension 直接设置 `image.src = cover_url`。
   - Target: mobile web `getCoverImageAttrs()` 返回 `{ src: "/api/image-proxy?url=<encoded>" }`；extension 通过当前配置的 backend origin 生成 `http://host:port/api/image-proxy?url=<encoded>`；所有封面 `<img>` 不再设置 `referrerpolicy`。
   - Target: 卡片/消息使用 wrapper 容器承载图片和 fallback，加载失败时给 wrapper 加 `is-fallback` / `is-error` 并移除 `<img>`，由 wrapper 显示灰色背景 + 文字/图标，占位区域保持固定比例。不要依赖 `<img>::after`。
   - Acceptance: mobile web `<img>` src 以 `/api/image-proxy?url=` 开头；extension `<img>` src 以当前 backend origin + `/api/image-proxy?url=` 开头；小红书来源推荐项不再被 `normalizeCoverUrl()` 屏蔽；给必定 403 的代理 URL 时，卡片仍保持 16:9 占位区域。

6. **测试与文档**: 后端、mobile web、extension 和文档必须一起更新。
   - Current: 无代理端点测试；mobile web 测试仍断言 xhscdn 为空；extension 测试未覆盖 proxy URL。
   - Target: 后端单测覆盖 URL 校验、白名单、redirect、内容类型、`Content-Length` 超限、实际字节超限、超时和成功响应；mobile web 单测覆盖 proxy attrs 和小红书 URL；extension helper/static 测试覆盖 proxy URL 与移除 `referrerPolicy`；文档按仓库规则更新。
   - Acceptance: `pytest tests/test_api_image_proxy.py tests/test_mobile_web_view_models.py -v` 通过；extension 对应测试通过；`docs/changelog.md`、`docs/modules/runtime.md`、`docs/modules/extension.md` 和架构/README 相关说明已同步。

## Boundaries

**In scope:**
- `GET /api/image-proxy` 端点。
- URL/scheme/userinfo/域名边界校验。
- 手动 redirect 校验。
- Content-Type / Content-Length / 实际字节数 / 超时校验。
- mobile web `getCoverImageAttrs` / `normalizeCoverUrl` 改走代理。
- mobile web `recommend.js` / `chat.js` 图片模板改为 wrapper fallback。
- extension `popup-helpers.js` / `popup.js` 封面图改走代理，并复用已有 fallback 容器。
- 后端、mobile web、extension 测试。
- docs/changelog、runtime/extension 模块文档、架构/README 相关说明。

**Out of scope:**
- 持久磁盘缓存 — 单用户场景，浏览器 `Cache-Control` 缓存足够；spool 只用于单次响应校验，不作为缓存。
- 图片裁剪/压缩/WebP 转换 — 增加复杂度，当前目标是稳定加载。
- 代理端点鉴权 — 本地服务，同 origin / configured local backend 访问；外部暴露部署仍由用户自行加反向代理鉴权。
- 新增平台 CDN 自动发现机制 — 白名单手动维护，新增平台时更新。

## Constraints

- 上游请求必须使用 `httpx.AsyncClient.send(..., stream=True)` 或等价的 `client.stream()`，不能用普通 `client.get()` 后读取 `resp.content`。
- 为了稳定返回 413，后端必须在发送下游响应头前完成上游内容类型和大小校验；允许使用 `SpooledTemporaryFile(max_size=1MB)` 作为有界临时 spool。
- 最大响应体为 10MB；无论上游是否提供可信 `Content-Length`，实际读取超过 10MB 都必须拒绝。
- 白名单使用域名边界匹配：`host == suffix or host.endswith("." + suffix)`。禁止裸 `hostname.endswith("hdslb.com")` 这类会放过 `evilhdslb.com` 的写法。
- 不使用 `follow_redirects=True`；所有 redirect 都必须手动解析并重新校验。
- 代理请求不发送 `Referer` 头；可以设置普通浏览器 `User-Agent` 和 `Accept: image/*` 提高 CDN 兼容性。
- 代理响应必须设置 `Cache-Control: public, max-age=86400` 和 `X-Content-Type-Options: nosniff`。

## Acceptance Criteria

- [ ] `GET /api/image-proxy?url=<bilibili_cover>` 返回 200 + 图片数据。
- [ ] `GET /api/image-proxy?url=<xhscdn_cover>` 返回 200 + 图片数据（小红书不再被屏蔽）。
- [ ] 非白名单域名返回 403。
- [ ] 非 HTTP(S)、缺 hostname、含 userinfo 或 malformed URL 返回 400。
- [ ] 白名单 URL redirect 到非白名单域名返回 403。
- [ ] redirect 超过 3 次或循环返回 502。
- [ ] 非 `image/*` 内容返回 400。
- [ ] 上游非 2xx 返回 502。
- [ ] `Content-Length > 10MB` 返回 413。
- [ ] 缺失/伪造 `Content-Length` 且实际超过 10MB 返回 413。
- [ ] 超时返回 504。
- [ ] mobile web `<img>` src 以 `/api/image-proxy?url=` 开头。
- [ ] extension `<img>` src 以当前 backend origin + `/api/image-proxy?url=` 开头。
- [ ] mobile web 小红书来源推荐项显示封面图。
- [ ] 图片加载失败时卡片/消息显示占位符，布局不塌缩。
- [ ] 后端代理端点有单元测试覆盖（白名单/拒绝/redirect/超时/大小）。
- [ ] mobile web `getCoverImageAttrs` 有单元测试覆盖。
- [ ] extension helper/static 测试覆盖 proxy URL 和不再设置 `referrerPolicy`。
- [ ] 文档和架构说明已同步。

## Ambiguity Report

| Dimension          | Score | Min  | Status | Notes |
|--------------------|-------|------|--------|-------|
| Goal Clarity       | 0.92  | 0.75 | ✓      | 明确覆盖 mobile web + extension |
| Boundary Clarity   | 0.90  | 0.70 | ✓      | in/out-of-scope 包含代码、测试、文档 |
| Constraint Clarity | 0.88  | 0.65 | ✓      | redirect、大小限制、spool 策略已锁定 |
| Acceptance Criteria| 0.86  | 0.70 | ✓      | 安全、前端、extension、文档均有 pass/fail |
| **Ambiguity**      | 0.10  | ≤0.20| ✓      | |

## Interview Log

| Round | Perspective | Question summary | Decision locked |
|-------|-------------|-----------------|-----------------|
| 0     | Researcher  | 各平台图片加载失败的根因分析 | 小红书 mobile web 被主动屏蔽、其他平台依赖 no-referrer 不稳定 |
| 0     | Researcher  | 后端是否已有代理能力 | 无代理端点，httpx 已在依赖中 |
| 1     | Researcher  | 白名单范围：只限已知平台还是开放？ | 白名单，只允许已知平台 CDN 域名 |
| 1     | Researcher  | 代理失败时前端表现：占位符还是静默移除？ | 占位符，保持布局稳定 |
| 2     | Reviewer    | 是否覆盖 extension side panel？ | 覆盖 mobile web + extension，避免同一问题残留 |
| 2     | Reviewer    | 如何兼顾流式与 413？ | 上游流式读取 + 有界 spool，发送下游响应前完成大小校验 |
| 2     | Reviewer    | redirect 是否允许？ | 允许最多 3 次手动 redirect，每跳重新校验 |

---

*Phase: image-proxy*
*Spec updated: 2026-05-21*
*Next step: update PLAN and implement with tests first*
