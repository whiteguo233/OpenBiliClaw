# Mobile Web Native Redesign Design

## Background

`/m/` 当前是一个可用的移动 Web MVP，但它不是浏览器插件 side panel 的移动版，也没有达到“手机上的完整 OpenBiliClaw”体验：

- 视觉上偏简单 feed：缺少插件的运行状态层级、活动流、提示栏、消息中心和细腻的内容状态。
- 推荐页只保留了列表、换一批、加载更多和简化 delight banner；插件里的反馈、活动历史、补货进度、delight 处理态没有完整迁移。
- 画像页把后端字段直接摊成卡片，缺少插件里更完整的画像分组、历史状态、空态和加载态。
- 对话页能收发消息，但缺少插件的 placeholder 轮播、发送状态、消息 overlay 和不同上下文下的聊天入口。

用户反馈来自真实手机截图：页面可以访问，也能展示推荐，但“样式和插件差很多，很多功能没了”。这说明下一步不应继续只修资源/字段 bug，而应重新定义移动端的信息架构。

## Goal

把 `/m/` 做成 **移动原生体验**，并在功能工作流上对齐插件 side panel。

目标不是像素级复制插件，而是让用户在手机浏览器里完成插件里的核心任务：

1. 看推荐、换一批、加载更多、打开内容。
2. 看懂当前后端状态：在线、补货、候选池、最近活动。
3. 处理推荐反馈：喜欢、不喜欢、写一句。
4. 处理 delight：查看、不感兴趣、聊一聊、跳过/稍后。
5. 查看完整画像：人格素描、core、价值观、兴趣树、认知更新、洞察、awareness。
6. 和 AI 对话，并能从消息中心处理兴趣探测和 delight 通知。
7. 在手机屏幕上保持清晰、稳定、无控制台资源错误。

## Non-Goals

- 不做行为采集；移动 Web 没有 B 站/XHS/抖音页面上下文。
- 不做 Cookie 同步；这些仍由浏览器插件负责。
- 不做配置设置页；配置仍走插件设置页或 `config.toml`。
- 不引入 React/Vue 等框架；继续使用 Vanilla JS + ES Modules。
- 不把插件 CSS/HTML 原样复制到移动端。插件是窄 side panel，手机 Web 需要独立的信息层级和触控布局。
- 不新增后端业务协议，除非发现现有 API 无法支撑插件已有功能。

## Design Direction

### Product Pattern

采用“移动内容工作台”而不是“插件侧栏复刻”：

- 顶部保留轻量 app bar：品牌、连接状态、消息入口。
- 主内容使用手机 feed 模式：推荐卡片更像内容流，操作入口贴近卡片底部。
- 运行状态用可折叠 activity strip 承接，不占用太多首屏。
- 重要临时任务（delight / interest probe）用 tray 或 bottom sheet，而不是塞进普通列表。
- 底部三 tab 继续保留：推荐 / 画像 / 对话。

### Visual System

继承插件的品牌气质，但移动端重新组织：

- 颜色：继续使用 `#fb7299`、`#5aa9ff`、暖白到浅蓝背景，避免和插件完全断裂。
- 字体：系统中文字体优先；数字和状态文本使用更紧凑的权重。
- 卡片：推荐卡片、delight 卡、画像 section 使用统一 surface，避免卡片套卡片。
- 圆角：移动端主卡控制在中等圆角，避免过度“气泡化”；按钮和 chips 保持轻量。
- 图像：封面可用时优先展示；无法稳定加载的外链封面不渲染，保留无图卡样式。
- 动效：只使用触控反馈、轻量展开/收起和 loading 状态；尊重 `prefers-reduced-motion`。

## Information Architecture

### 1. App Shell

移动端 shell 固定三块：

1. **Top App Bar**
   - `OpenBiliClaw`
   - 连接状态点 + 状态文案（在线 / 离线 / 降级）
   - 消息按钮 + 未读角标

2. **Scrollable Content**
   - 当前 tab 的主内容
   - 保留 safe-area 和浏览器地址栏高度变化容错

3. **Bottom Tab Bar**
   - 推荐
   - 画像
   - 对话

### 2. 推荐 Tab

推荐页按手机内容消费顺序组织：

1. **Activity Strip**
   - 一行显示当前运行态：例如“正在补相关推荐候选 / 刚补进 6 条 / 这池里还有 34 条可换”
   - 支持展开历史，展示 `/api/activity-feed` 最近事件
   - 展开后支持分页加载更多

2. **Pool Summary**
   - 候选池余量
   - 最近补充
   - 近期话题
   - 文案复用插件 helper 的语义，而不是只显示裸数字

3. **Delight Tray**
   - 有 pending delight 时显示
   - 支持多条队列切换
   - 展示 hook、标题、原因、来源、状态
   - 动作：看看 / 不感兴趣 / 聊一聊 / 稍后
   - 处理后短暂保留结果态，不立即消失

4. **Primary Actions**
   - 换一批
   - 加载更多
   - 刷新/补货状态要落在按钮和 activity strip 上

5. **Recommendation Feed**
   - 每张卡展示封面（可用时）、标题、来源、UP/作者、推荐理由、主题标签
   - 整卡点击打开内容
   - 卡片底部动作：
     - 打开
     - 喜欢
     - 不喜欢
     - 写一句
   - 反馈操作不误触发打开内容
   - 提交反馈后展示局部状态，并触发相应后端事件

### 3. 画像 Tab

画像页按“先结论，后证据”组织：

1. **Portrait Summary**
   - 人格素描主段落
   - 如果未初始化，展示明确空态和下一步

2. **Core Section**
   - 核心特质 chips
   - 深层需求
   - MBTI 维度条

3. **Values & Drivers**
   - 价值观
   - 内在驱动力

4. **Interest Section**
   - 喜欢领域
   - 不喜欢领域
   - favorite UP/作者
   - 每个领域显示权重和 specifics

5. **Role & Surface**
   - life stage
   - current phase
   - cognitive style
   - style preferences
   - context
   - exploration openness

6. **Speculative Interests**
   - 确认 / 拒绝
   - 处理后从当前列表移除或显示已处理态

7. **Cognition History / Insight / Awareness**
   - 最近认知更新
   - 加载更多
   - 活跃洞察
   - 最近感知

### 4. 对话 Tab

对话页需要接近插件聊天体验，但采用手机输入布局：

1. **History**
   - 用户消息和 AI 回复气泡
   - pending / processing / error 状态清晰可见
   - 自动滚动到底，保留用户手动滚动时的稳定性

2. **Composer**
   - 底部贴边输入区
   - placeholder 轮播，复用插件的内容提示方向
   - Enter 行为在手机上不作为主要发送路径，发送按钮为主

3. **Contextual Chat**
   - delight 的“聊一聊”进入带 subject 的聊天流程
   - interest probe 的“多聊聊”进入带 domain 的聊天流程

4. **Messages Overlay**
   - 消息入口从 top app bar 打开
   - 包含 interest probe、delight 通知
   - 支持确认、拒绝、查看、忽略、聊一聊

## Data Flow

移动端继续使用现有后端 API：

| Workflow | API |
|---|---|
| health/status | `GET /api/health`, `GET /api/runtime-status` |
| recommendations | `GET /api/recommendations`, `POST /api/recommendations/reshuffle`, `POST /api/recommendations/append` |
| click | `POST /api/recommendation-click` |
| feedback | `POST /api/recommendation-feedback` |
| delight | `GET /api/delight/pending-batch`, `POST /api/delight/respond` |
| profile | `GET /api/profile-summary` |
| cognition | `POST /api/cognition-updates/{id}/seen` |
| activity | `GET /api/activity-feed` |
| chat | `GET /api/chat/turns`, `GET /api/chat/turns/{id}`, `POST /api/chat/turns` |
| messages | `GET /api/notifications/pending`, `POST /api/notifications/sent` |
| interest probe | `POST /api/interest-probes/respond` |
| realtime | `WS /api/runtime-stream` |

Runtime stream events should update mobile state without full reload:

- `refresh.started`
- `refresh.strategy`
- `refresh.pool_updated`
- `delight.candidate`
- `delight.refreshed`
- `activity.added`
- `interest.probe`
- `profile_updated`

## State Model

Mobile Web should keep a dedicated state object, but the state shape should mirror plugin workflows:

- `activeTab`
- `online`
- `runtimeStatus`
- `runtimeEvent`
- `activityFeed`
- `recommendations`
- `activeDelights`
- `delightCurrentIndex`
- `messages`
- `profile`
- `chatTurns`
- `pendingChatPolls`

Shared normalization belongs in `src/openbiliclaw/web/js/view-models.js`. Functions should be ported from `extension/popup/popup-helpers.js` where semantics matter:

- pool status summary
- ready recommendation hint
- activity card state
- recommendation normalization
- delight UI state
- feedback payload
- profile summary normalization
- chat turn normalization

The mobile implementation should not import from `extension/`, because packaged backend installs may not include extension source. Instead, port the minimum helper logic into mobile view-models and keep tests around it.

## Error Handling

### Offline / Backend Unreachable

- Top app bar status becomes offline.
- Recommendation tab shows an offline empty state.
- Chat send is disabled or shows local error.
- Runtime stream reconnects with backoff.

### Degraded Backend

- `/api/health` may be reachable while non-config endpoints return 503.
- Mobile Web should show a degraded banner and keep read-only navigation where possible.
- Do not hide the entire UI behind a generic failure.

### External Images

- Normalize cover URLs before render.
- Filter known 403 hotlink hosts.
- Use `referrerpolicy="no-referrer"` for external images.
- `onerror` removes the image node and preserves card layout.

### Long Text

- Recommendation titles and reasons clamp cleanly.
- Buttons keep fixed height and never resize the action row.
- Profile paragraphs can expand naturally; chips wrap without overflow.

## Mobile Interaction Rules

- Primary tap targets are at least 44px tall.
- Bottom tab stays reachable and respects `safe-area-inset-bottom`.
- Composer stays above the browser bottom controls when possible.
- Pull-to-refresh can remain, but button actions must not depend on it.
- Feedback and delight chat use bottom sheets, not tiny inline controls.
- Avoid visible “how to use” instructional text; states should be self-explanatory.

## Feature Parity Matrix

| Plugin Capability | Mobile Native Target |
|---|---|
| Online/offline badge | Top app bar status |
| Runtime hint footer | Activity strip |
| Activity history | Expandable activity sheet/section |
| Pool status summary | Pool summary strip with semantic copy |
| Recommendation cards | Mobile feed cards |
| Recommendation feedback | Card actions + comment sheet |
| Delight banner queue | Mobile delight tray |
| Delight inbox cards | Messages overlay cards |
| Interest probe inbox | Messages overlay cards |
| Profile summary | Portrait summary section |
| Profile detailed layers | Mobile profile sections |
| Cognition history pagination | “加载更多” within profile |
| Chat placeholder carousel | Mobile composer placeholder rotation |
| Chat polling/status | Message-level pending/error states |
| Runtime stream updates | State patch + local re-render |

## Testing Strategy

### Unit / View-Model Tests

Add/extend `tests/test_mobile_web_view_models.py` for:

- recommendation normalization
- cover URL normalization
- pool status semantic copy
- activity card state
- delight queue state
- feedback payload
- profile summary normalization
- chat turn normalization
- message/probe normalization

### API Route Tests

Keep lightweight backend tests for static serving and favicon:

- `/m/` returns SPA shell
- `/favicon.ico` returns PNG

### Browser E2E

Use Playwright CLI against `http://127.0.0.1:8420/m/` and, for phone parity, also manual phone smoke against `http://<LAN-IP>:8420/m/`.

Required browser checks:

- Recommendation tab renders activity strip, pool strip, delight tray, cards, feedback actions.
- Console has 0 errors/warnings on initial recommendation tab load.
- Profile tab renders MBTI, interests, cognition history and speculation actions.
- Chat tab renders existing turns, sends a new message, and displays `reply`.
- Messages overlay opens and renders pending items.
- 390x844 and 430x932 screenshots have no text overlap.

## Acceptance Criteria

1. Phone Web UI no longer feels like a stripped-down demo; it should feel like a first-class mobile surface.
2. The top-level workflows available in the plugin are present on mobile, except explicitly listed non-goals.
3. Recommendation cards support direct feedback, not just open/scroll.
4. Activity and runtime state are visible without occupying the whole first viewport.
5. Delight and interest probes are actionable from mobile.
6. Profile page exposes the same conceptual layers as the plugin.
7. Chat page supports existing history, new sends, pending/error states and contextual entry points.
8. Browser console is clean on initial mobile recommendation load.
9. No text/button overlap at common iPhone viewport sizes.
10. Backend can still run as a pure local API; mobile access remains opt-in through `--host 0.0.0.0`.

## Rollout Plan

1. **Foundation**
   - Define mobile state model.
   - Port required helper semantics into `view-models.js`.
   - Add view-model tests.

2. **Recommendation Tab**
   - Activity strip.
   - Pool semantic summary.
   - Delight tray.
   - Recommendation feed card actions.
   - Feedback sheet.

3. **Messages Overlay**
   - Interest probe cards.
   - Delight cards.
   - Badge count and ack behavior.

4. **Profile Tab**
   - Rebuild with mobile sections.
   - Add cognition pagination and speculation actions.

5. **Chat Tab**
   - Mobile composer polish.
   - Placeholder carousel.
   - Pending/error states.
   - Contextual chat entry.

6. **Browser E2E + Phone Smoke**
   - Desktop mobile viewport screenshots.
   - Real phone access check.
   - Console/network checks.

## Open Questions

1. 是否需要移动 Web 暴露“暂停后台 LLM / 关闭浏览器后暂停后台”这类 runtime toggle？当前 spec 暂不包含，因为设置页是 non-goal。
2. 是否要为移动端单独持久化 dismissed delight / message overlay 本地状态？当前 spec 复用后端 ack/response，不额外做 localStorage。
3. 是否需要支持 PWA standalone 模式下的特殊布局？当前 spec 只要求浏览器内访问正常。
