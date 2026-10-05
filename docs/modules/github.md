# GitHub 来源

> GitHub 是 OpenBiliClaw 的只读正式内容来源。首版内容单元为公开 repository：通过 GitHub 官方 REST API 完成画像初始化、关键词发现、候选评估和三端推荐，不读取浏览器 Cookie，也不执行 star、watch、follow 或任何仓库写操作。

本文描述实现契约，不代表真实验收已经全部通过；自动化、真站 E2E 与阻塞项以
[`platform-source-acceptance.github.md`](../platform-source-acceptance.github.md) 为准。

## 能力边界

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Repository 搜索 | 支持 | `GET /search/repositories`，查询由统一 KeywordPlanner 和 inspiration 关键词轨共同提供 |
| 排名发现 | 支持 | 使用公开仓库搜索并按 stars 排序；这是产品查询，不冒充 GitHub 官方 Trending |
| 最新发现 | 支持 | 使用 `created:>=...` 限定近 30 天创建的公开仓库，并按 GitHub REST 支持的最近更新时间排序；repository `created_at` 仍作为内容发布时间 |
| 画像初始化 | 支持 | 读取指定公开账号的 starred repositories，转换为 canonical `favorite` 事件 |
| 匿名访问 | 支持 | 公开发现和公开 starred collection 不要求凭据 |
| 可选 PAT | 支持 | PAT 只用于提高配额和通过 `GET /user` 校验当前账号；即使提供 PAT，私有仓库仍会被拒绝 |
| 增量轮询 | 不支持 | 画像信号只在 init 或显式 `fetch-github` 时刷新，不在后台轮询 GitHub 账号 |
| 浏览器扩展任务 | 不需要 | 所有请求由后端官方 API client 发出；扩展 popup 只提供设置、状态和推荐消费面 |
| 原生保存 | 不支持 | 本地收藏继续可用，但不会映射为 GitHub Star |
| 图片封面 | 不使用 | Repository 使用无封面文字卡；owner avatar 不是内容封面 |

## 数据与身份

```text
KeywordPlanner / inspiration axis
                 │ claim("github")
                 ▼
       GitHubDiscoveryProducer
       search / ranked / latest
                 │
                 ▼
       GitHub official REST API
                 │
                 ▼
     github_repository_to_content()
                 │
                 ▼
discovery_candidates → shared LLM eval/admission → content_cache
                 │
                 ▼
       PC / mobile / extension text card

explicit username or verified PAT identity
                 │
                 ▼
public starred repositories → favorite events → guided init/profile
```

Repository 的 durable identity 是 `github:repository:<numeric-id>`；GitHub `node_id` 作为全局 provenance 保留，链接始终使用 API 返回的公开 `html_url`。账号身份以数值 `id` / `node_id` 为准，而不是可改名的 login：PAT 的 `/user` 返回是 verified identity；显式用户名经 `/users/{username}` 只能证明公开账号存在，不证明账号所有权。二者 durable ID 冲突时，个人 bootstrap 会停止，公开 discovery 仍可匿名运行。

只接收 `private=false` 的仓库。服务端拥有公开范围约束，调用方不能通过查询文本或 PAT 取消它；上游意外返回的私有行会在 normalizer 再次被丢弃。CLI smoke、正式 producer 与 inspiration backend 共用 `github_public_repository_query()`：先去掉控制字符和 `is:private` / `visibility:private` / `private:true`，再追加 `in:name,description,readme is:public fork:false`。清洗后为空的 planner 词直接结算失败，不向 GitHub 发无效或越界查询。

## 指标和时间

- `favorite_count` ← `stargazers_count`。
- view、like、comment、share、danmaku 没有可靠的 repository 级等价字段，保持不可用。
- forks 是代码谱系，不是分享；open issues 不是评论；watchers 也不冒充浏览量。
- `created_at` 是 canonical `published_at`；`updated_at` / `pushed_at` 只作为 provenance，`discovered_at` 不替代发布时间。
- starred bootstrap 的行为时间使用 star media type 返回的 `starred_at`。

`topics`、language、license、forks、open issues 和 watchers 会作为 source metadata 沿候选 / 推荐 DTO 保留，但当前三端前端尚未消费 `source_metadata`。现有可见卡片只显示 owner/name、description 与 stars 映射的收藏计数；不得把“后端保留字段”写成“前端已展示字段”。

## 配置

```toml
[sources.github]
enabled = false
username = ""
# 可选 Personal Access Token；视同密码保管，GET /api/config 永不回显。
access_token = ""
token_env = "OPENBILICLAW_GITHUB_TOKEN"
source_modes = ["search", "ranked", "latest"]
daily_search_budget = 120
daily_ranked_budget = 60
daily_latest_budget = 60
request_interval_seconds = 6
min_interval_minutes = 10
bootstrap_limit = 300
bootstrap_max_pages = 10

[scheduler.pool_source_shares]
github = 1
```

令牌解析只接受 `OPENBILICLAW_GITHUB_TOKEN`（优先）或显式 `access_token`；`token_env` 是固定兼容字段，改成其它名称不会扩大环境读取范围，也不会隐式读取开发机上的 `GITHUB_TOKEN` / `GH_TOKEN`。配置页省略令牌或回传掩码表示保持不变，显式空字符串表示清除。无令牌时来源仍可公开发现；已保存令牌即使来源开关关闭，也会继续在状态面展示其存在和最近验证结论。

## CLI

```bash
# 只读检查公开 starred repositories；默认不写 memory、不调用 LLM
openbiliclaw fetch-github --username octocat

# 使用已配置令牌校验当前账号，再读取其公开 starred repositories
openbiliclaw fetch-github

# 三个无本地写入的 API 分支 smoke
openbiliclaw discover-github "local AI agent" --limit 10
openbiliclaw discover-github-ranked --limit 10
openbiliclaw discover-github-latest --limit 10

# 正式候选生产：写 discovery_candidates，后续走共享 eval/admission
openbiliclaw discover --source github --force

# 把 GitHub 作为初始化画像来源
openbiliclaw init --no-bilibili --yes-github --github-username octocat
```

`fetch-github` 默认是隔离的只读 smoke。只有显式 `--write-memory` 才写 canonical 事件，`--rebuild-profile` 还会触发真实 LLM；这两个开关都只影响本地状态，不会修改 GitHub。

## 上游终态

- `200` 且预期 object/list envelope：成功；`200 []` 或 `total_count=0, incomplete_results=false` 是明确空结果。
- `304`：内容未变化，不是空结果。
- `401`：令牌被拒；可匿名能力移除 Authorization 后仍可继续公开路径，但不得把坏令牌标成 verified。
- `403` / `429`：按响应头和错误体区分限流、权限和其它禁止；限流结果不会伪装为空。
- `422`：无效查询或过期页码；producer 对 stale page 只允许一次有界 reset。
- `503` / 网络错误：瞬时失败，认证结论为 indeterminate。
- `incomplete_results=true`、页数/条目上限或后页失败：保留已接受的公开行并标记 degraded / partial，不推进完整性或缺失推断。
- Star 分页在首个完整页之前超时会失败；已经取得至少一个完整页后超时则保留此前事件并返回 `partial_timeout`，不把部分结果冒充完整范围。

搜索 API 最多暴露 1,000 条结果。分页只根据 RFC `Link` 的 `next` / exhaustion 判断终止，不以“本页少于 limit”猜测完整。

GitHub 的 `Retry-After` 冷却持久化为来源级状态，由正式 producer 与 inspiration provider 共用；任一路径触发限流，另一条路径都会 fail-closed 跳过，避免并行撞限额。`/api/sources/status` 只聚合当前配置 `source_modes` 的最近运行，关闭的分支不会把来源误报为未验证。正式 discovery 遇到 401 时还会保存只与当前 PAT 指纹匹配的拒绝标记：`/api/sources/status` 和 `/api/init-status` 随即把 GitHub 的 profile / bootstrap 轴标为 unavailable，但匿名公开 discovery 轴仍独立；轮换或清除 PAT 后旧标记不再匹配，不会永久污染新凭据。初始化中 GitHub 的身份、令牌或采集失败按来源隔离：混合来源继续使用其它有效信号并把 GitHub 记为部分失败；只有 GitHub 是唯一画像来源且没有事件时才进入对应硬失败。

## PAT 获取与安全

PAT 是可选项；只使用公开用户名时不需要创建。需要提高 GitHub API 配额或自动识别当前账号时，可在 GitHub 的 token settings 创建最小权限 token。OpenBiliClaw 的首版只读公开数据，不要求 repository write 权限，也不会因 token 拥有更高权限而读取私有仓库。

稳定入口以 GitHub 官方文档为准：

- <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens>
- <https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api>

不要把 PAT 写进命令历史、日志、截图或提交到仓库。推荐通过本机 `config.toml`（已 gitignore）或 `OPENBILICLAW_GITHUB_TOKEN` 提供。

## 已知限制

- 首版只把 repository 建模为候选；Issue、Pull Request、Release 不在本契约中。
- GitHub 没有被宣称为官方 Trending/feed 来源；ranked/latest 是可解释的公开搜索分支。
- 账号 starred 信号是 init-and-on-demand，不提供后台增量同步。
- 文本卡不显示 owner avatar，不经过图片代理。

冻结契约和逐门验收记录分别见 `docs/platform-source-contract.github.toml`、`docs/platform-source-acceptance.github.md`。
