# 聊一聊添加公开 Web 来源：接手验收

日期：2026-10-02。范围为现有 discovery-only Web 草稿的迁移与聊天 authoring
`capability-increment`，不声称全平台账号接入。

## 代码与运行归属

- 新 worktree：`/Users/white/workspace/OpenBiliClaw/.worktrees/chat-web-source`
- 分支：`feat/chat-web-source`，基于 main `6b910eacc388d6576d6791b0dabd27fc5f728605`。
- 承接旧 `feat/web-discovery-mvp` / `86843d43` worktree 的未提交 Web 文件集；
  旧目录保持原状，新 worktree 经三方迁移保留当前 main 的 GitHub、worker、聊天和配额逻辑。
- 导入验证：`PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python`
  打印的 `openbiliclaw.__file__` 位于上述新 worktree。共享虚拟环境只提供依赖。
- 所有新增持久化验收使用 pytest 临时目录或 `TemporaryDirectory` SQLite、memory、审批台账；
  不读取用户画像，不改生产配置、生产服务、生产订阅或用户已安装的扩展；安装态验收另用临时 Chrome profile 加载本 worktree。
- 复用契约：[platform-source-contract.web.toml](platform-source-contract.web.toml)。
  [旧 Web 验收](platform-source-acceptance.web.md) 只作为历史来源，不能代替本次结果。

## 实现与用户流程

默认口味伙伴与系统管家均支持：用户给出公开 URL 并要求添加 → `probe_web_source`
只读探测 → 展示内容样例 → `create_source` 生成确认卡 → 批准后编译、保存 Web recipe →
后台 producer 抓取 → 统一候选评估与推荐。单纯分享链接不意味着订阅整个站点。

探测只返回一个完整推荐方案；可指定 `strategy` 查看其它已识别模式。长样例先移除，
方案本身超过用户配置的聊天字符预算则明确失败。确认前无订阅写入；非法配置批准后仍失败；
重复相同执行计划保留现有记录及停用状态。无审批能力的 legacy 对话拒绝 Web 写入。

API runtime、独立 OpenClaw runtime、CLI formal discover 共享 Web producer；配额建议与
运行时实时读取启用的 typed recipe。三端聊一聊复用现有确认卡。CLI chat 不提供该交互，
保留 `probe-web` / `discover-web` / `discover --source web` 等独立命令。

## 验证记录

| 验证项 | 适用性 | 状态 | 证据与界限 |
| --- | --- | --- | --- |
| 来源注册契约 | required | PASS | `scripts/audit_platform_source.py --contract docs/platform-source-contract.web.toml --check --json`，exit 0，required_missing=0；仅证明注册点 |
| 新聊天纵向合同 | required | PASS | `tests/test_agent_web_sources.py` 18 passed：真实 parser/compiler + mock HTTP/DNS/LLM，默认 skill → SSE → pending 卡 → approve API → recipe → producer → SQLite candidate；包含拒绝、失败、重试、后台只读、字符预算，以及轮询/SSE 双向竞争与批准结局回放 |
| 聊天、旧入口与来源契约回归 | required | PASS | source tools / registry / context / skills / chat Web / source contract 联合 109 passed；旧同步入口拒绝 Web 创建和启停 |
| Web transport/parser/state 核心回归 | required | PASS | `test_web_public` / `web_recipe` / `web_probe` / `web_adapter_public` / `web_producer` / `web_source_state` 联合 201 passed |
| Runtime / API / CLI 初测 | required | PASS | Web runtime、API、producer、auth CLI、平台注册联合 110 passed |
| 配额与 OpenClaw | required | PASS | OpenClaw adapter + Web API 配额联合 58 passed；GET/POST 建议份额、动态启停、standalone producer |
| 后端扩展回归 | required | PASS | 930 passed + 6 个计时/队列用例在并发大批运行下失败；同样 6 个原样独立复跑全部通过，无修改这些逻辑或断言 |
| Python 全量及失败项复测 | required | PASS | 首轮 `9927 passed / 68 skipped / 4 failed`，耗时 1729.73 秒。三处文案/初始化 roster 断言按 Web 不参与账号初始化的契约修正，相关 3 文件 26 passed；另一个 502 最小复现为既有推荐 transport 测试泄漏环境变量，修复测试隔离后两节点 2 passed，proxy/runtime/saved-sync 联合 164 passed，生产身份逻辑与断言未改。未重跑第二次全量，不宣称全量一次通过 |
| 对话执行幂等回归 | required | PASS | 聊天/SSE/approval/durable/scheduler 联合 71 passed；新增竞争用例在修复前稳定产生 2 卡，修复后 GET worker 与 SSE 两种先后均只有 1 次探测、3 次模型 hop、1 卡，完成/失败回合以及批准后的 `approval_result` 重连回放不重跑工具。补充 approve/reject 先于 final 的两个稳定红测，修复后历史与 SSE 回放都保留唯一结果 |
| CLI 配置与入口 | required | PASS | `openbiliclaw config-show` 与 `probe-web --help` 使用 worktree PYTHONPATH，exit 0 |
| Ruff / MyPy | required | PASS | `ruff check src/ tests/` exit 0；61 个变更 Python 文件格式检查通过；`mypy src/`：305 source files，无问题 |
| 扩展自动化与构建 | required | PASS | `npm ci`、`npm run typecheck`、`npm run build`、`npm test`：1514/1514；证明源码构建，不冒充已安装扩展 |
| 前端 Python 合同 | required | PASS | desktop probe、来源设置与卡片等联合 74 passed |
| 真实公共网络与 producer | required | PASS | 默认系统 DNS + TLS + HTTP，Django Weblog 探测 ready，识别 feed/sitemap；测试库保存 1 条 recipe；有界 Feed 首轮入库 3，第二轮新增 0，pending_eval=3；未替换 resolver/transport |
| 真实配置 LLM 的完整聊天链 | required | PASS | 保留 `openai_compatible` / `deepseek-v4-flash` 配置路线。首轮仅文字确认，收紧角色指令并在限流恢复后复测：真实 probe → create_source → approval_request → 待批准说明；确认前零 recipe。隔离库执行该审批参数后保存 1 条 recipe、真实 producer 首两轮各交付 3 条不同候选（bounded feed backlog）。approve API 由独立纵向测试覆盖 |
| 桌面/移动实际渲染 | required | PASS | Playwright CLI 真实浏览器：`/web` 与 `/m`（390×844）各输入独立公开 URL，展示 probe/create 过程及批准卡，点击批准后显示成功；SQLite recipe 数分别 0→1、1→2，两个 ApprovalRecord 均 executed。LLM、HTTP/DNS、无关壳层 hydration 为 fixture；AgentLoop、SSE、skill选择、approval API、compiler 与 SQLite 用真实代码。截图与完整清单见下方 |
| 安装态扩展实测 | required | PASS | Chrome for Testing 151.0.7922.34 的独立 profile 真实加载当前 worktree MV3 扩展（0.3.225）；插件设置切换到隔离 18472 后端，第三 Feed 批准后 recipe 2→3。初测暴露 GET pending 轮询与 SSE 双跑的重复卡；修复后重启临时服务，第四 Feed 仅生成 1 张确认卡，批准前 3 条、批准后 4 条、该审批 executed、pending=0。实际 popup 在 chrome-extension 页面打开；LLM/HTTP/DNS/壳层为 fixture，无生产操作 |
| 真实 LLM/embedding 评价与准入判断 | required | PASS | 同配置 `deepseek-v4-flash` + `Ollama/nomic-embed-text`；临时合成 Django/Python 兴趣画像；fetched=3 → retained=3 → evaluated=3 → admitted=0。评分 0.28/0.30/0.58 均低于原 0.60 阈值，3 条 rejected_low_score；8 条真实 embedding 缓存。完整等待原 90 秒合并窗，总 187.06 秒；未改阈值、未读取私有画像或写生产缓存。没有成功准入样本 |
| 登录、账号画像、上游写入 | N/A | NOT_RUN | 来源契约明确匿名 discovery-only；相应 exclusion tests 可执行验证 |
| CLI chat 交互 | N/A | NOT_RUN | 现有 CLI chat 无 AgentLoop 确认 UI，本次明确排除；独立 Web CLI 仍支持 |

UI 产物位于 `/tmp/openbiliclaw-web-takeover/ui/`，不包含生产数据，未复制临时数据库入仓库：

- `desktop-pending.png` / `desktop-approved.png`：桌面批准前后截图。
- `mobile-pending.png` / `mobile-approved.png`：移动端批准前后截图。
- `verification.json`：检查结果、边界、截图 SHA-256；`desktop-recipes.json` /
  `final-recipes.json`：临时库 1 条 / 2 条 Web recipe；`approvals.json`：执行终态。
- `extension-installed.png`、`extension-pending.png`、`extension-approved.png`：安装态初测；
  `verification-extension.json` 包含 manifest / service worker / popup SHA-256、真实扩展 ID
  `iagiojfleglkccnlempjfibhljlbeedg`、临时 profile 路径与限制；
  `extension-pending-recipes.json` / `extension-approved-recipes.json`：临时库 2 条 / 3 条。
  初测缺陷与证据保留；`extension-retest-pending.png` / `extension-retest-approved.png`
  为修复后单卡批准前后截图；对应 `extension-retest-*-recipes.json` 记录 3→4，
  `extension-retest-approved-approvals.json` 记录唯一审批 executed。
  manifest SHA-256：`0602f256e1630fe4f9eddbe0adfaddca08a5075104c3f923227e9a1e3d0a5075`。
- `server.py`：独立 fixture app，绑定 `127.0.0.1:18472`；`network-requests.txt` 与
  `http-fixture.jsonl`：浏览器本地 API 和 fixture 公网读取记录。桌面/移动及扩展复测的专用浏览器、
  18472 临时服务与 18473 隔离代理均已关闭。`proxy.jsonl` 的所有允许目标仅为
  `127.0.0.1:18472`，默认 8420 与外部目标均被拒绝；未使用用户 Chrome profile。
  LLM、HTTP/DNS 和无关壳层响应为 fixture，该 UI 证据不代替真实 LLM 或公网验收。

复现自动化（在本 worktree）：

```bash
export PYTHONPATH="$PWD/src"
/Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest
/Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q tests/test_agent_web_sources.py
/Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
/Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
/Users/white/workspace/OpenBiliClaw/.venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.web.toml --check --json
```

## 交付状态

当前 verdict：`complete`，范围为本文声明的匿名 discovery-only Web 来源迁移与聊天新增能力。代码、确定性回归、真实公网/模型以及桌面/移动/安装态扩展证据已完成；全量首轮失败与定向修复复测按上表保留。
未合入 main、未切换生产运行目录、未替换用户已安装扩展、未发布版本。
