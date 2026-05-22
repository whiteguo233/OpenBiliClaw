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
| 入口 URL | `http://<局域网IP>:8420/m/` |
| 鉴权 | 不做鉴权；默认本机访问，手机访问需用户显式 `start --host 0.0.0.0` |
| 安全边界 | 仅面向可信局域网，不建议暴露公网 / 公共 Wi-Fi / 未受信 VPN |
| PWA | 提供 manifest.json，支持添加到主屏幕（暂不做离线缓存） |
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
   - 与插件共享 `session=popup&scope=chat` 的主聊天历史
   - 聊天回复完成后刷新画像摘要与活动流
   - 底部固定两行输入框，优先保留聊天上下文浏览空间
   - 消息收件箱 overlay（兴趣探测 + 惊喜推荐通知；兴趣探测动作对齐插件为「喜欢 / 不喜欢 / 多聊聊」，惊喜推荐动作对齐插件为「看看 / 喜欢 / 不感兴趣 / 聊一聊」）

4. **通用**
   - 底部 Tab 导航栏（推荐/画像/对话）
   - 顶部状态栏（连接状态、消息提醒角标）
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
- 手机访问需要用户显式使用 `openbiliclaw start --host 0.0.0.0`。
- 该模式不做鉴权，适用于可信局域网；不要暴露到公网、公共 Wi-Fi 或未受信 VPN。

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
| 对话 | `POST /api/chat/turns`, `GET /api/chat/turns`, `GET /api/chat/turns/{id}`；主聊天使用 `session=popup&scope=chat` 与插件共享历史 |
| 消息 | `GET /api/notifications/pending`, `POST /api/notifications/sent` |
| 认知通知 | `GET /api/cognition-updates/pending`, `POST /api/cognition-updates/seen` |
| 活动流 | `GET /api/activity-feed` |
| 兴趣探测 | `POST /api/interest-probes/respond` |
| 实时 | `WS /api/runtime-stream` |
| 健康 | `GET /api/health` |

移动端会在 `view-models.js` 中做最小字段适配：
- 推荐池状态读取 `/api/runtime-status` 的 `pool_available_count`、`last_replenished_count`、`recent_pool_topics`，再映射成推荐页三枚 chip 使用的 `pool_size`、`recent_replenish`、`current_topic`。
- 推荐页头部用 `getMobileRecommendationHeaderState()` 生成插件语义一致的标题、首屏「换一批」、三枚池状态 chip 和活动辅助行；移动端把池状态压成横向轻量 pill，并把 `xhs-extension-*` / `dy-plugin-*` / `yt-*` 等内部来源名显示为用户可读短标签；列表接近底部时用 `IntersectionObserver` 自动调用 `append`，同时保留底部「加载更多」作为手动兜底。
- 惊喜推荐沿用插件 compact banner 思路：左侧小缩略图、标签 / 标题 / 理由 / 来源围绕头图形成 featured card，推荐原因带轻量标记，翻页控件与「稍后看」关闭入口放在右上角，动作区仍保持「看看 / 喜欢 / 不感兴趣 / 聊一聊」；「聊一聊」会在当前卡片内展开 composer 和多轮气泡，不切换到对话 tab。
- MBTI 维度兼容后端对象形态（如 `EI: { pole: "I", strength: 0.8 }`）和旧数组形态，统一映射为 `{ left, right, score }` 后再渲染。
- MBTI 会保留后端 `confidence` 显示为“可信度”；内容口味将 `long/slow` 等 raw 枚举映射为“长视频 / 慢节奏”等中文标签；使用场景会显示 `session_type` 为“模式”。
- 认知更新卡片会保留后端 `context_line` 与 `source_label`，即使前端已做过一次 normalize 后再次渲染，也不回退成泛化上下文。
- 对话 turn 兼容 `response` 和后端当前返回的 `reply` 字段，统一映射成聊天气泡使用的 `response`。
- 移动端主聊天与插件读取同一 `session=popup&scope=chat`；contextual delight/probe 聊天通过 `scope=delight/probe` 标识主题上下文。惊喜推荐内联聊天也复用 `session=popup&scope=delight`，按 `subject_id=bvid` hydrate 每条候选自己的 `turns` 历史，pending turn 通过 `/api/chat/turns/{turn_id}` 轮询恢复。
- 封面图会在渲染前归一化：B 站 `http` / protocol-relative 地址升级为 HTTPS，推荐、惊喜推荐和消息封面统一走本地 `/api/image-proxy`，加载失败时保留固定比例 fallback。推荐列表首屏封面使用 eager/high priority，后续若干封面通过 `Image()` best-effort 预热，减少滑动过程中的白屏。

### 静态资源

- `/m/` 由 `StaticFiles` 服务移动 Web SPA。
- `/favicon.ico` 返回 `icon-192.png`，避免浏览器默认请求根路径 favicon 时产生 404。

### WebSocket

复用插件的 `runtime-stream` 协议，移动端关注的事件：
- `refresh.pool_updated` → 刷新推荐列表
- `delight.candidate` → 更新惊喜推荐
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
```
