# Mobile Web — Spec & Plan

## 目标

在同局域网内通过手机浏览器访问 OpenBiliClaw，查看推荐、画像、对话，体验对齐浏览器插件。

## 决策记录

| 决策点 | 方案 |
|--------|------|
| 技术栈 | Vanilla JS + ES Modules，模块化组件，无构建步骤 |
| 样式 | 复用插件 CSS 设计令牌（CSS Variables），针对移动端重写布局 |
| 路由 | SPA hash routing（`#/recommend`、`#/profile`、`#/chat`） |
| 文件位置 | `src/openbiliclaw/web/` — 随 pip install 分发 |
| 静态服务 | FastAPI `StaticFiles` mount at `/m/` |
| 入口 URL | 默认 `http://<局域网IP>:8420/m/`；公网 Caddy 为 `https://<域名>/m/` |
| 鉴权 | 可选密码门禁：`[api.auth].enabled`；本机免登录，局域网 / 远程设备需密码（详见 [`docs/plans/2026-05-30-web-password-auth-design.md`](plans/2026-05-30-web-password-auth-design.md)）。仍需 `start --host 0.0.0.0` 才能被手机访问 |
| 安全边界 | 默认面向可信局域网；开启 `[api.auth]` 后局域网 / 远程访问需密码。LAN HTTP 仍为明文；公网必须走 HTTPS，并使用 Caddy overlay + Web 密码门禁，不能直接暴露 `8420` |
| PWA | 提供 manifest.json + iOS Web Clip 元数据，支持添加到主屏幕（暂不做 service worker / 离线缓存） |
| 行为采集 | 不做（无 bilibili 页面上下文） |
| 源管理/爬取 | 不做 |
| 设置页 | 不做（配置走 config.toml） |

## 功能范围

### 包含

1. **推荐页**（默认 Tab）
   - 插件同款紧凑头部：`For You / 这几条，你大概会点开` + 首屏「换一批」
   - 推荐列表（封面、标题、UP 主、推荐理由）
   - 来源标识（Bilibili / Xiaohongshu / Douyin / YouTube / Web）
   - 点击跳转原始内容链接（`content_url` 优先，B 站 `bvid` fallback）
   - 移动端点击优先拉起目标平台 App（`js/app-launch.js`：B站 / 小红书 / 抖音 / YouTube / X / 知乎 URL scheme 深链，小红书携带 `xsec_token`）。回落策略保证 `/m/` 当前页永不被跳走：1.6s 内页面未被切走视为拉起失败，优先 `window.open` 新标签页打开网页；弹窗被拦（用户手势已过期，iOS Safari 必拦）则显示页内提示条「打开网页版」按钮由用户新手势打开。系统弹窗（iOS「在 App 中打开?」确认 / 打不开提示）挂起时 `blur` 暂停回落计时、关闭后 `focus` 续 0.9s 再回落，避免拉起成功却误跳当前页。桌面端及无法解析深链的地址（b23.tv / xhslink 短链、Reddit 等）保持新标签页打开网页
   - 点击直达上报（best-effort，不追踪观看时长）
   - "换一批" 按钮（reshuffle）
   - 接近列表底部自动 append 下一批，底部 "加载更多" 保留为手动兜底
   - 推荐池状态显示（当前可换、最近补进、现在在忙）
   - Delight 惊喜推荐 banner（队列浏览 ‹/›），动作与插件对齐为「看看 / 喜欢 / 不感兴趣 / 聊一聊」

2. **画像页**
   - 人格素描段落
   - Core 层：核心特质、需求、MBTI（含可信度）
   - Values 层：价值观
   - Interest 层：兴趣领域树（喜欢/不喜欢）
   - Role 层：生活阶段
   - Surface 层：认知风格、内容口味中文标签、使用场景（含模式）、探索开放度
   - Speculate 层：推测性兴趣（确认/拒绝交互）
   - 认知更新历史（分页加载，保留上下文与来源标签）
   - 活跃洞察 & 意识笔记

3. **对话页**
   - 消息历史
   - 文本输入 & 发送
   - AI 思考中状态
   - 与插件、桌面 Web 共享 `session=popup` 的主聊天历史；普通文字写入 `scope=chat`，消息里的兴趣 / 避雷「多聊聊」分别写入 `scope=probe` / `scope=avoidance_probe`，三类文字轮次在主对话中按时间顺序对齐；聊天页可见且在线时约每 2.5 秒检查一次新 turn，历史未变化不重绘，用户阅读旧消息时保留滚动位置
   - 与插件共享 `session=popup` 的 durable 对话历史；历史读取不限定 scope，同时展示 `hypothesis` 觉察卡、`confusion` 澄清问题和 probe 聊天轮次；惊喜推荐 `delight` 仍保留在推荐卡自己的内聊历史中
   - 确认卡 / 疑惑作为一等 durable turn 留在历史中；「聊聊」只提交 `reply_to_turn_id`，从只读 context preview 构建 context bar/reply quote，服务端失败时保留目标与草稿，不根据 current anchor 猜测关系
   - 「待聊确认」列表、主动打开、假设卡「准 / 不准 / 聊聊 / 稍后」四动作与按需结算轮询；纯数字、UUID、BVID、事件前缀或裸哈希等 opaque evidence 不展示
   - 待聊列表、消息历史与 composer 各自使用有界布局；后台刷新保留读者位置、已展开依据、输入草稿与焦点
   - 聊天回复完成后刷新画像摘要与活动流
   - 底部固定两行输入框，优先保留聊天上下文浏览空间
   - 消息收件箱 overlay（兴趣探测 + 避雷探针 + 惊喜推荐通知；兴趣探测动作对齐插件为「喜欢 / 不喜欢 / 多聊聊」，避雷探针动作为「确实不喜欢 / 不是 / 多聊聊」，惊喜推荐动作对齐插件为「看看 / 喜欢 / 不感兴趣 / 聊一聊」；探针非聊天动作按归一化后的 `type + domain` 键记录独立的 in-flight 状态，关闭再打开 overlay 或其它重渲染仍从该状态恢复整卡禁用、`is-processing` 与 `aria-busy=true`，避免重复提交；只有服务端接受结算或返回终态 no-op 后才写入 terminal handled key 并移除卡片，传输/服务端失败则清除 pending、保留卡片并恢复全部动作供重试；空态提示保持 X 关闭入口可用）

4. **通用**
   - 底部 Tab 导航栏（推荐/画像/对话）
   - 顶部状态栏（连接状态、消息提醒角标）
   - 页面或聊天滚动容器下滑超过阈值后显示「顶部」按钮，一键回到当前可见滚动区顶部；用固定 3×3 命中采样避开卡片操作，最多上移两步，无安全位置则暂时隐藏，不进入顶部状态栏或覆盖底部导航。滚动和 resize 均重新判定，不随卡片数增加扫描量
   - WebSocket 实时更新（池变化、delight、画像更新）
   - 下拉刷新手势（推荐页）
   - PWA manifest（添加到主屏幕，不做 service worker 离线缓存）

### 不包含

- 行为采集（content script）
- Cookie 同步
- 源管理（XHS/抖音/YouTube）
- 设置页
- 观看时长追踪（离开移动端 Web 后无法可靠追踪）
- 离线缓存 / 后台推送型 PWA

## 技术方案

### 目录结构

```
src/openbiliclaw/web/
├── index.html          # SPA 入口
├── manifest.json       # PWA manifest
├── icon-192.png        # PWA 图标
├── icon-512.png
├── css/
│   └── app.css         # 全量样式（复用插件设计令牌）
├── js/
│   ├── app.js          # 入口：路由、Tab 切换、WebSocket
│   ├── api.js          # 后端 API 封装（同插件 popup-api.js）
│   ├── stream.js       # WebSocket 客户端（同插件 popup-stream.js）
│   ├── view-models.js  # 后端响应 → 移动端渲染字段适配
│   ├── app-launch.js   # 移动端深链拉起目标平台 App + 网页回落
│   ├── views/
│   │   ├── recommend.js  # 推荐页渲染 & 交互
│   │   ├── profile.js    # 画像页渲染 & 交互
│   │   └── chat.js       # 对话页渲染 & 交互
│   └── components/
│       ├── tab-bar.js       # 底部导航
│       ├── status-bar.js    # 顶部状态栏
│       ├── card.js          # 推荐卡片
│       ├── delight.js       # 惊喜推荐 banner
│       ├── interest-tree.js # 兴趣树组件
│       ├── mbti.js          # MBTI 展示
│       ├── messages.js      # 消息收件箱 overlay
│       └── pull-refresh.js  # 下拉刷新
```

### 后端改动

```python
# app.py — create_app() 内新增
from fastapi.staticfiles import StaticFiles
from pathlib import Path

web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.is_dir():
    # Hash routing keeps client routes after "#", so StaticFiles only needs
    # to serve /m/ and asset files. /m/recommend is not a supported route.
    app.mount("/m", StaticFiles(directory=web_dir, html=True), name="mobile-web")
```

局域网访问约定：
- `openbiliclaw start` 默认仍绑定 `127.0.0.1`，只允许本机访问。
- 手机访问需要用户显式使用 `openbiliclaw start --host 0.0.0.0`；该 wildcard
  会同时创建 IPv4 `0.0.0.0` 与可用的 IPv6 `[::]` listener。
- 默认无鉴权、面向可信局域网；可选 `[api.auth].enabled`（`openbiliclaw set-password`）为局域网 / 远程设备加密码门禁，本机免登录。LAN HTTP 仍为明文，介意嗅探请上 HTTPS（反代），不要直接暴露公网 / 公共 Wi-Fi / 未受信 VPN。

### 样式策略

从插件 popup.html 提取 CSS Variables 作为设计令牌：

```css
:root {
  --brand: #fb7299;
  --sky: #5aa9ff;
  --success: #22c55e;
  --danger: #ef4444;
  --surface: #ffffff;
  --surface-strong: #f8f9fa;
  --surface-soft: #f1f3f5;
  --text-main: #1a1a2e;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  --shadow-lg: 0 8px 32px rgba(0,0,0,.08);
  --shadow-sm: 0 2px 8px rgba(0,0,0,.04);
}
```

移动端适配：
- viewport meta: `width=device-width, initial-scale=1, viewport-fit=cover`
- 底部 Tab 栏固定 + safe-area-inset-bottom
- 卡片全宽布局（插件是固定宽度侧栏）
- 触摸友好的点击区域（最小 44px）
- 系统字体栈优先

### API 调用

移动端 JS 直接调用现有 `/api/*` endpoints，与插件完全相同：

| 页面 | 接口 |
|------|------|
| 推荐 | `GET /api/recommendations`, `POST /api/recommendations/reshuffle`, `POST /api/recommendations/append`, `POST /api/recommendation-click`, `GET /api/runtime-status` |
| Delight | `GET /api/delight/pending-batch`, `POST /api/delight/respond` |
| 画像 | `GET /api/profile-summary` |
| 对话 | `POST /api/chat/turns`, `GET /api/chat/turns`, `GET /api/chat/turns/{id}`, `GET /api/chat/pending-confirmations`, `POST /api/chat/pending-confirmations/{ref}/open`, `POST /api/chat/cards/{turn_id}/action`；主对话按 `session=popup` 读取全部对话 scope，与插件、桌面 Web 共享历史，三个可见聊天界面都会在打开时和可见期间刷新历史 |
| 消息 | `GET /api/notifications/pending`, `POST /api/notifications/sent` |
| 认知通知 | `GET /api/cognition-updates/pending`, `POST /api/cognition-updates/seen` |
| 活动流 | `GET /api/activity-feed` |
| 兴趣探测 | `GET /api/interest-probes/pending`, `POST /api/interest-probes/respond` |
| 避雷探针 | `GET /api/avoidance-probes/pending`, `POST /api/avoidance-probes/respond` |
| 实时 | `WS /api/runtime-stream` |
| 健康 | `GET /api/health` |

移动端会在 `view-models.js` 中做最小字段适配：
- 推荐池状态读取 `/api/runtime-status` 的 `pool_available_count`、`last_replenished_count`、`recent_pool_topics`，再映射成推荐页三枚 chip 使用的 `pool_size`、`recent_replenish`、`current_topic`。
- 推荐页头部用 `getMobileRecommendationHeaderState()` 生成插件语义一致的标题、首屏「换一批」、三枚池状态 chip 和活动辅助行；移动端把池状态压成横向轻量 pill，并把 `xhs-extension-*` / `dy-plugin-*` / `yt-*` 等内部来源名显示为用户可读短标签；列表接近底部时用 `IntersectionObserver` 自动调用 `append`，同时保留底部「加载更多」作为手动兜底。
- 惊喜推荐沿用插件 compact banner 思路：左侧小缩略图、标签 / 标题 / 理由 / 来源围绕头图形成 featured card，推荐原因带轻量标记，翻页控件与「稍后看」关闭入口放在右上角，动作区保持「看看 / 喜欢 / 稍后再看 / 收藏 / 不感兴趣 / 聊一聊」；「聊一聊」会在当前卡片内展开 composer 和多轮气泡，不切换到对话 tab。结果提示与动作区独立渲染：`state="liked"` 同时显示「好，这类多来点。」和完整动作组，like 使用 `aria-pressed="true"` 且只禁用重复 like，其余动作继续可用；like 请求失败则恢复未选中状态。

Delight UI 投影矩阵：

| `state` | `show_status` | `show_actions` | `like_pressed` | `like_disabled` |
| --- | --- | --- | --- | --- |
| `pending` | 有响应文案时显示 | 是 | 否 | 否 |
| `liked` | 是 | 是 | 是 | 是 |
| `viewed` | 是 | 否 | 否 | 是 |
| `rejected` | 是 | 否 | 否 | 是 |
| `chatted / chatting` | 有响应文案时显示 | 是 | 否 | 否 |

本地点击成功、刷新后 `pending-batch` 返回 liked，以及 `delight.liked` 实时事件都复用这份投影；`handled` 仅作为 `viewed / rejected` 的兼容终态，不参与 liked 动作区可见性。
- MBTI 维度兼容后端对象形态（如 `EI: { pole: "I", strength: 0.8 }`）和旧数组形态，统一映射为 `{ left, right, score }` 后再渲染。
- MBTI 会保留后端 `confidence` 显示为“可信度”；内容口味将 `long/slow` 等 raw 枚举映射为“长视频 / 慢节奏”等中文标签；使用场景会显示 `session_type` 为“模式”。
- 认知更新卡片会保留后端 `context_line` 与 `source_label`，即使前端已做过一次 normalize 后再次渲染，也不回退成泛化上下文。
- 对话 turn 兼容 `response` 和后端当前返回的 `reply` 字段，统一映射成聊天气泡使用的 `response`。
- 移动端主对话与插件读取同一 `session=popup`，不在历史 GET 上限定 `scope`；共享 renderer 展示 `chat/hypothesis/confusion/probe/avoidance_probe`，因此消息里的探针聊天不会因关闭消息 overlay 而消失。普通用户消息仍写入 `scope=chat`，contextual probe 通过 `scope=probe/avoidance_probe` 标识主题上下文；惊喜推荐 `delight` 仍按 `subject_id=bvid` hydrate 在每条候选自己的内聊历史中，pending turn 通过 `/api/chat/turns/{turn_id}` 轮询恢复。
- 封面图会在渲染前归一化：B 站 `http` / protocol-relative 地址升级为 HTTPS，推荐、惊喜推荐和消息封面统一走本地 `/api/image-proxy`，加载失败时保留固定比例 fallback。推荐列表当前批次默认预热 12 张封面，前 12 张使用 eager 加载，追加批次会先等待封面预热/解码或短超时再插入卡片；封面 frame 使用粉蓝渐变骨架占位，真实图片 decode 完成后淡入，减少高速滑动过程中的白屏。

### 静态资源

- `/m/` 由 `StaticFiles` 服务移动 Web SPA。
- `/favicon.ico` 返回 `icon-192.png`，避免浏览器默认请求根路径 favicon 时产生 404。

### WebSocket

复用插件的 `runtime-stream` 协议，移动端关注的事件：
- `refresh.pool_updated` → 更新池子状态 / header，不替换当前推荐列表
- `delight.candidate` → 更新惊喜推荐
- `delight.liked` → 将匹配 bvid 的候选投影为 liked，保留状态与其它动作
- `profile_updated` → 刷新画像
- `interest.probe` → 弹出探测通知
- `activity.added` → 更新活动流

## 实施计划

### Phase 1: 后端 + 骨架（~1h）
1. `src/openbiliclaw/web/` 目录 + index.html 骨架
2. FastAPI StaticFiles mount
3. SPA hash router + Tab 切换
4. CSS 设计令牌 + 移动端基础布局
5. API 封装模块 (api.js)
6. WebSocket 客户端 (stream.js)

### Phase 2: 推荐页（~1.5h）
1. 推荐卡片组件
2. 推荐列表渲染 + 空状态
3. 池状态显示
4. 换一批 / 自动续页 / 加载更多兜底
5. Delight banner + 队列导航
6. 下拉刷新
7. 实时更新（WebSocket）

### Phase 3: 画像页（~1.5h）
1. 人格素描 + Core 层
2. MBTI 组件
3. 兴趣树组件
4. Values / Role / Surface 层
5. Speculate 层（确认/拒绝交互）
6. 认知更新历史（分页）
7. 活跃洞察 & 意识

### Phase 4: 对话页（~1h）
1. 消息历史渲染
2. 输入框 + 发送
3. AI 思考状态
4. 消息收件箱 overlay
5. 兴趣探测 / Delight 通知卡片

### Phase 5: 收尾（~0.5h）
1. PWA manifest + 图标
2. 局域网访问说明 / 安全提示
3. 连接状态指示
4. 顶部消息角标
5. 测试 & 调整

## 手机访问方式

```bash
# 启动（局域网可访问）
openbiliclaw start --host 0.0.0.0

# 手机浏览器打开
http://<电脑局域网IP>:8420/m/

# 仅有 IPv6 时（方括号不可省略）
http://[电脑局域网IPv6]:8420/m/

# 公网域名（Docker Caddy overlay）
https://obc.example.com/m/
```

打开 `/m/` 后可在 iOS Safari 通过「分享 → 添加到主屏幕」保存为桌面图标；Android Chrome / Chromium 浏览器可通过菜单里的「安装应用」或「添加到主屏幕」保存。局域网 HTTP 在部分 Android 浏览器上可能只生成快捷方式；完整 PWA 安装提示对 HTTPS 更稳定。

不想手敲地址时有两个扫码入口：插件 popup / side panel 顶部的「手机版」胶囊按钮（品牌色带文字，点开二维码浮层），以及桌面 Web（`/web`）顶栏的「手机版」入口（点开抽屉，二维码由自包含的 `desktop/assets/js/mobile-qr.js` 生成）。当桌面页通过公网 / 局域网非 loopback 地址打开时，二维码保留当前页面的 scheme、host 和端口，因此 `https://obc.example.com/web` 会生成 `https://obc.example.com:443/m/`，不会替换为后端私网 IP 或退回 HTTP。只有页面仍是 loopback 时，桌面抽屉才调用轻量端点 `GET /api/qr-info` 并读取响应中的 `lan_ip` 字段；插件入口同样只在配置 host 为 loopback 时探测 LAN IP，并始终保留插件配置的 HTTP/HTTPS scheme。两个入口都在**每次打开时重新请求**该端点，端点自身也绕过 `/api/health` 的 30 秒 `lan_ip` TTL 实时探测：局域网地址会随换 Wi-Fi / 插拔网卡改变，任何一层缓存住都会让二维码继续编码手机已经打不开的旧地址。桌面侧仍保留首屏预取值，但只在这次请求失败时兜底使用，避免退化成 loopback 地址。

公网部署和认证步骤见 [`docs/https-deployment.md`](https-deployment.md)。

## 换批和底部续页恢复（2026-09-07）

- 换批和追加直接应用响应 `pool_status`；旧后端缺失时换批保留有界 runtime-status 补读。卡片渲染不等待库存侧通道。
- 请求超时覆盖响应正文，换批失败保留当前卡片、显示错误并解除 loading。
- 用户下滑会主动重新检查已相交的底部哨兵；空批次暂停自动追加，后续库存增加事件解除暂停（重复的相同正数不会重试空页），并仅在底部仍可见且用户此前请求续页时恢复。加载结束重新绑定哨兵，追加使用当前 DOM 插入点。
- 原生 Flutter 客户端实现位于独立 OpenBiliClaw-mobile 仓库，配套修复读取同一库存响应、保留手动重试和冷却到点复查。
