# TikTok / Instagram 扩展默认站点权限（2026-10-21）

范围：`capability-increment`，仅调整扩展权限声明和设置保存流程。用户要求两者的插件权限默认开启；来源 `enabled`、周期同步、账号登录和站内写入策略不变。基线 `7038dae9`，实现位于独立分支 `fix/default-tiktok-instagram-permissions`。这不是两个来源的全量重新验收。

## 行为与证据

- Chrome / Firefox：TikTok 已在固定权限中；将 Instagram 的 `https://*.instagram.com/*` 从可选权限移入 `host_permissions`。
- Safari：补齐 `*://*.tiktok.com/*` 和 `https://*.instagram.com/*` 的固定权限声明。仅此权限变更不意味着 Instagram 的 Safari executor、页面桥或账号路径已完成接入和真机验收。
- Instagram 设置保存直接更新配置，不再请求/撤销站点权限，也不再等待授权弹窗。安装、更新和手动禁止访问由浏览器处理；dispatcher 和登录心跳仍检查实际站点权限。
- 没有增加所有网站的默认权限；远程后端仍按具体地址申请可选权限。两个来源仍为实验性、默认关闭。

实际保存 handler 的回归先在旧代码失败：启用来源触发 `request`，关闭来源触发 `remove`。修改后两种状态均成功保存，权限 API 调用均为零，保存锁和脏字段正常恢复。

| Gate | 适用性 | 结果 / 证据 |
| --- | --- | --- |
| 隔离与环境 | required | PASS；独立 worktree，`PYTHONPATH="$PWD/src"`，Python import 指向该 worktree；不加载生产配置或数据目录 |
| 清单与实际保存行为 | required | PASS；`npm test`，1821 passed，0 skipped；含两种 Instagram 来源状态及三个目标的权限边界 |
| 类型与构建资源 | required | PASS；`npm run build`、`npm run build:firefox`、`npm run build:safari`；内含 TS 检查及 manifest 资产预检 |
| Python 相邻契约与文档 | required | PASS；所列 pytest 51 passed |
| 来源注册审计 | required | PASS；两份现有 contract 的 audit `--check --json` 均 exit 0、MISSING=0；MANUAL 各 14 项，不能当作全来源验收通过 |
| 打包内权限 | required | PASS；Chrome / Firefox zip 中的实际 manifest 含两者固定权限；Safari 原生 Xcode 构建、DMG 打包及只读挂载后 appex 内实际 manifest 同样通过。未新增默认 `<all_urls>` 或通配所有站点权限 |
| 文档 | required | PASS；首页中英文、README CN/EN、模块、安装、Docker、隐私、商店审核说明及 changelog 同步；`git diff --check` 通过 |
| 上游 / 模型 / 画像链路 | N/A | 本次没有改变上游 transport、账号解析、事件、推荐或模型配置；不发真实平台请求，不操作真实账号。既有验收边界见两个来源台账 |
| 四端 | required | PASS（本次范围）；popup 保存 handler 执行验证，desktop/mobile/CLI 的 API 和来源启用契约未变，无后端代码改动 |
| 生产安装态升级 | N/A（本次未请求） | NOT_RUN；本次不修改用户已安装扩展的站点授权，不将构建/打包等同于浏览器升级确认通过 |
| 商店发布 / version / tag | N/A | 当前请求为代码修改及延续的 commit / merge main / push / 清理；未要求新版本或商店提交。本地包沿用 0.3.229，仅供验证 |

## 命令

以下命令从任务 worktree 执行，全部 exit 0；`npm` 命令 cwd 为 `extension/`，其余命令 cwd 为仓库根目录。

```bash
npm test
npm run build
npm run build:firefox
npm run build:safari
npm run package:only
npm run package:firefox:only
npm run package:safari:only
node --check extension/popup/popup.js
node --check extension/popup/popup-helpers.js
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/pytest tests/test_docs_index.py tests/test_install_contract_docs.py tests/test_instagram_contract.py tests/test_tiktok_contract.py tests/test_desktop_web_instagram_settings.py tests/test_desktop_web_tiktok_settings.py -q --tb=short
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.instagram.toml --check --json
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python scripts/audit_platform_source.py --contract docs/platform-source-contract.tiktok.toml --check --json
```

构建 manifest 的 SHA-256：

- Chrome：`615a4741d642b1e254f51be33e9ed8bf42c17387aa23ce852f584a8f2e211e24`
- Firefox：`70f3391ddcea2a7c5de86ec0db92b8558f6321036bef8127cda673a960f25967`
- Safari：`b3a097b4c1b21f69833881f82e1ac47b3f4a666b6a71c024e0a911c5d89a7286`

验证包 SHA-256：

- Chrome zip：`7a5ed69e5b33f99945ce5c50b44c9625fdb19af03dbfb6e1ea80cbfc9225f26c`
- Firefox zip：`adc3d30118195a2d1d774da30a205a04feff3c641852737e80cb6dd84ca53dbb`

- Safari DMG（本地验证包，未公证/未发布）：`e7dbf5e44250e46447e9ca20bd6ddc3c0850be75b6f28f03a48220883cbdcc94`

本次代码、构建和打包范围 verdict：`complete`。生产浏览器安装态和商店发布均未执行，不能从此结论推出已安装扩展自动获得了新权限。TikTok / Instagram 原有账号、Firefox 登录态、手机真机及原生收藏等限制保持原台账结论。
