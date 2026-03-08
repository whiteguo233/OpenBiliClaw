# CLI 命令参考

> 所有已实现的 `openbiliclaw` CLI 命令。

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
| `start` | 启动 Agent | 🔲 stub |
| `recommend` | 查看推荐 | ✅ |
| `profile` | 查看用户画像 | ✅ |
| `discover` | 手动触发发现 | 🔲 stub |
| `chat` | 苏格拉底式对话 | 🔲 stub |

## 详细说明

### `openbiliclaw config-show`

显示当前加载的配置、已注册的 LLM Provider 和最终生效的默认 Provider。

```bash
$ openbiliclaw config-show
⚙️ 当前配置
  已注册 Provider: openai, deepseek, ollama
  最终默认 Provider: openai
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
B站认证状态
  状态: 已认证
  Cookie 文件: data/bilibili_cookie.json
  用户名: alice
  UID: 10086
```

### `openbiliclaw browser status`

检查 agent-browser 是否已安装。

```bash
$ openbiliclaw browser status
agent-browser 状态
  状态: 已安装
  可执行文件: /usr/local/bin/agent-browser
```

### `openbiliclaw browser open <url>`

通过 agent-browser 打开指定页面。

```bash
$ openbiliclaw browser open https://www.bilibili.com
浏览器已打开
  https://www.bilibili.com
```

### `openbiliclaw browser content <url>`

获取指定页面的可见文本内容。

```bash
$ openbiliclaw browser content https://example.com
页面内容
  - heading "Example Domain" [ref=e1]
  ...
```

### `openbiliclaw profile`

展示当前灵魂画像。若画像尚未初始化，会明确提示后续执行 `openbiliclaw init`。

```bash
$ openbiliclaw profile
🧠 用户画像
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

### `openbiliclaw recommend`

读取推荐缓存，生成朋友式推荐表达，并把已展示条目标记为 `presented=1`。

```bash
$ openbiliclaw recommend
📬 推荐内容
讲透城市与建筑的空间叙事
  UP主: 城市观察局
  话题: 你最近那股想把结构想透的劲头
  推荐理由: 这条会对上你最近那种想把结构想透的劲头，它不是快餐内容，而是会慢慢把结构给你铺开。
  BV号: BV1REC
```

如果当前还没有可推荐内容，会提示先执行：

```bash
openbiliclaw discover
```
