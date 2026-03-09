# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。
>
> 当前 CLI 已统一使用 Rich 输出：
> - 页面标题采用统一标题面板
> - 状态反馈统一为成功 / 警告 / 失败 / 开发中几类状态块
> - 推荐列表使用卡片式展示
> - 用户画像使用分区块展示

## 全局选项

```bash
openbiliclaw [--log-level DEBUG|INFO|WARNING|ERROR] <命令>
```

## 命令一览

| 命令 | 说明 | 状态 |
|------|------|------|
| `config-show` | 显示当前配置和可用 Provider | ✅ |
| `health-check` | 检查 LLM Provider 可用性 | ✅ |
| `auth login` | 设置并验证 B 站 Cookie | ✅ |
| `auth status` | 查看认证状态 | ✅ |
| `browser status` | 检查 agent-browser 安装 | ✅ |
| `browser open <url>` | 通过浏览器打开页面 | ✅ |
| `browser content <url>` | 获取页面文本内容 | ✅ |
| `start` | 启动本地 API 服务 | ✅ |
| `init` | 首次初始化 | ✅ |
| `recommend` | 查看推荐 | ✅ |
| `feedback <id> <like\|dislike\|comment>` | 对推荐提交反馈 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `discover` | 手动触发发现 | ✅ |
| `chat` | 苏格拉底式对话 | ✅ |

## 详细说明

### `openbiliclaw config-show`

显示当前加载的配置、已注册的 LLM Provider 和最终生效的默认 Provider。

```bash
$ openbiliclaw config-show
当前配置概览
配置项
Provider 概览
```

### `openbiliclaw health-check`

逐个检查已注册 Provider 的连通性。

```bash
$ openbiliclaw health-check
Provider 健康检查
  openai (default): 可用
  deepseek: 可用
  ollama: 不可用
    原因: connection refused
```

### `openbiliclaw auth login`

交互式或非交互式设置 B 站 Cookie。验证通过后才保存。

```bash
# 交互式
$ openbiliclaw auth login
请输入 B 站 Cookie: SESSDATA=abc; bili_jct=xyz
登录成功
  用户名: alice
  UID: 10086

# 非交互式
$ openbiliclaw auth login --cookie "SESSDATA=abc; bili_jct=xyz"
```

### `openbiliclaw auth status`

检查当前保存的 Cookie 是否有效。

```bash
$ openbiliclaw auth status
认证概览
认证信息
  状态: 已认证
  用户名: alice
  UID: 10086
```

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
浏览器集成状态
浏览器信息
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
目标地址
  URL: https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
╭─ 页面内容 ─╮
│ Example Domain ... │
╰──────────────╯
```

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
用户画像概览
人格描述
这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人……

核心特质
  理性、谨慎、自驱

价值观
  成长、真实

当前阶段
  稳定积累阶段

深层需求
  被理解、持续成长
```

### `openbiliclaw init`

首次运行编排命令。会顺序执行：

1. 校验 B 站认证
2. 拉取历史
3. 写入事件层并分析偏好
4. 生成初始画像
5. 自动跑一次内容发现

```bash
$ openbiliclaw init
初始化 OpenBiliClaw
1/4 拉取历史
2/4 分析偏好
3/4 生成画像
4/4 发现内容
初始化完成
初始化摘要
  历史条数: 200
  画像状态: 已生成
  发现内容数: 30
```

如果 discover 阶段失败，但历史和画像阶段成功，命令会提示“部分完成”，并建议稍后手动执行：

```bash
openbiliclaw discover
```

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
本轮推荐
推荐 1
  标题: 讲透城市与建筑的空间叙事
  UP 主: 城市观察局
  话题标签: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```

### `openbiliclaw feedback <id> <like|dislike|comment>`

为一条已展示的推荐记录写入结构化反馈，可附带备注；`comment` 必须带 `--note`。

```bash
$ openbiliclaw feedback 7 dislike --note "太浅了"
反馈已记录
反馈详情
  推荐ID: 7
  反馈: dislike
  备注: 太浅了

$ openbiliclaw feedback 7 comment --note "方向对，但我想看更深一点。"
```

### `openbiliclaw discover`

读取当前画像并执行一次真实内容发现，结果会写入 `content_cache`，并展示本次发现摘要与前几条预览。

```bash
$ openbiliclaw discover
本次内容发现
发现摘要
  发现条数: 12
  缓存状态: 已写入 content_cache

发现 1
  标题: 讲透城市空间与叙事结构
  UP 主: 城市观察局
  来源策略: search
  相关性分数: 0.83
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw chat`

进入持续对话模式，复用 `SocraticDialogue` 的多轮历史。输入 `exit`、`quit` 或空行可结束。聊天内容会先记录为 `dialogue` 事件，并以受控方式积累到长期理解候选中，不会因为一句话立刻改写画像。

```bash
$ openbiliclaw chat
苏格拉底式对话
你：我最近总在刷讲结构的视频。
阿花：我听见你在说，你现在在意的可能不只是内容本身，而是想把事情看得更透一点。
你：exit
阿花：对话结束。
```

如果画像尚未初始化，会提示先执行：

```bash
openbiliclaw init
```

### `openbiliclaw start`

启动本地后端 API 服务，默认监听 `127.0.0.1:8420`，供浏览器插件或本地调试调用。

```bash
$ openbiliclaw start
启动 OpenBiliClaw
API 服务
  正在启动本地后端，默认监听 127.0.0.1:8420。
```

当前 `start` 会启动这些接口：

- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

### Stub 命令的输出约定

当前仍是 stub 的命令会统一使用“开发中”占位态输出，避免与真实错误混淆，并会附带建议的下一步命令。

- 更新 `recommendations` 表中的 `feedback_type` / `feedback_note` / `feedback_at`
- 写入一条 `event_type="feedback"` 的事件，供后续记忆系统使用
