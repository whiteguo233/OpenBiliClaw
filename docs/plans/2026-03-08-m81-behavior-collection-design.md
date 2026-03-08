# M81 Behavior Collection Design

## Background

`extension/` 当前已经有基础骨架：

- `extension/src/content/collector.ts` 已能采集基础 `click` 和 `search`
- `extension/src/background/service-worker.ts` 已能做最小缓冲并上报 `POST /api/events`
- `8.2` 已完成，后端现在提供 `GET /api/health`、`POST /api/events`、`GET /api/recommendations`

缺口在于插件侧行为采集还很浅，离 `8.1` 的目标还有明显距离：没有视频行为、没有导航快照、没有高频事件降噪，也没有对评论/点赞/投币/收藏等显式反馈动作的识别。

## Goal

完成 `8.1 行为采集` 的真实可运行版本，打通：

- 内容脚本采集多类 B 站行为
- service worker 进行事件缓冲、节流、批量发送、失败回填
- 后端可收到结构化事件

## Non-Goals

- 不在本轮重做 popup UI
- 不引入完整前端测试框架
- 不追求所有 B 站页面模板上的 100% 精准按钮识别
- 不在插件侧直接做画像分析或推荐逻辑

## Recommended Approach

采用“分层采集 + 有节制上报”：

- `collector.ts` 负责页面感知、DOM 快照、行为监听、事件标准化
- `service-worker.ts` 负责缓冲、去重、批量发送和失败回填
- 统一事件结构继续沿用：
  - `type`
  - `url`
  - `title`
  - `timestamp`
  - `context`
  - `metadata`

这比“内容脚本直接请求后端”更稳，也更符合 manifest v3 下 service worker 的职责。

## Event Scope

本轮一次补齐这些事件：

- `click`
- `search`
- `view`
- `pause`
- `seek`
- `scroll`
- `hover`
- `comment`
- `like`
- `coin`
- `favorite`
- `snapshot`

其中：

- `view/pause/seek` 通过 `video` 元素事件检测
- `snapshot` 在首次加载和 URL 变化时发送
- `comment/like/coin/favorite` 先检测用户意图点击，不依赖 B 站内部 JS 状态

## Noise Control

高频事件必须降噪，否则会让插件和后端都失真：

- `scroll`
  - 仅在滚动停止后上报
  - 带当前位置和滚动比例
  - 同页最小上报间隔
- `hover`
  - 仅对视频卡片、搜索卡片、推荐卡片
  - 停留超过阈值才上报
- `snapshot`
  - 只在首屏和 URL 变化时触发

## Navigation Detection

由于 B 站存在 SPA 导航，本轮在内容脚本中包装：

- `history.pushState`
- `history.replaceState`
- `popstate`

统一在 URL 变化时：

- 重新判断页面类型
- 发送一条 `snapshot`
- 重新挂载页面相关监听（尤其是视频元素）

## Service Worker Responsibilities

`service-worker.ts` 本轮应承担：

- 接收内容脚本事件
- 按事件类型做最小去重/节流
- 缓冲区到达阈值时批量发送
- 定时 flush
- 发送失败时回填缓冲区

可以新增极轻量的 helper，例如：

- `shouldBufferEvent(event)`
- `buildDedupeKey(event)`
- `enqueueEvent(event)`

## Testing Strategy

仓库里目前没有 extension 测试基建，因此本轮测试采用两层：

1. 代码层：把关键逻辑抽成可测试的小函数
   - 页面类型识别
   - DOM 快照组装
   - 去重 key
   - 高频事件节流判断
2. 文档层：补充手动联调步骤
   - 启动 `openbiliclaw start`
   - 安装插件
   - 在 B 站首页、视频页、搜索页验证事件是否进入 `/api/events`

本轮不强行引入 Vitest/Jest，避免把 `8.1` 变成“先搭前端测试基础设施”。

## Files

- Modify: `extension/src/content/collector.ts`
- Modify: `extension/src/background/service-worker.ts`
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`
- Create: `docs/modules/extension.md`

## Acceptance

- 在首页、视频页、搜索页能稳定采集核心行为
- URL 变化时会发送页面快照
- service worker 会缓冲并批量发送事件到 `/api/events`
- 后端 `events` 表能收到这些结构化事件
- 文档说明清楚当前采集范围与手动联调方法
