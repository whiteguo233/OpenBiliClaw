# 哔哩轻小说代理对照与验证域修复（2026-11-06）

范围：**discovery-only repair**，沿用 [dynamic-web 契约](../platform-source-contract.dynamic-web.toml)。
用户针对上一轮“直连重置、代理对照待确认”同意按推荐处理，本轮执行临时代理对照。
结论为 **incremental only**：验证域授权入口修复并通过真实原生链路，网站验证和内容采集仍阻断。
不能把代理 CONNECT 200、HTTP 请求正常返回、授权按钮成功或验证框加载当成网站内容可用。

## 隔离环境与路由

- 后端独立 worktree `ego-verification-scope-20261106`，分支
  `fix/ego-verification-scope-20261106`，基线 `c1aa7e94`；修复位于其未提交工作区。
  开始前核对 main 为 `28c9d7e3`，只有已有 `outputs/`、`promo/`、`tmp/` 未跟踪项，均保留。
- 原生独立 worktree `OpenBiliClaw-mobile-verification-scope-20261106`，分支
  `fix/native-verification-scope-20261106`，基线 `379cfcc`；使用本轮源代码重新编译。
  无合并、提交、推送、发版或正式服务切换。
- 新私有目录 `/tmp/obc-bilinovel-proxy-20261106-w6pi8x5l`，全新 Ego profile。
  仅复制官方缓存安装镜像，经正常签名检查和自动首次设置，73.46 秒 ready；
  不是新下载的验收，没有复制个人 Cookie、浏览记录或旧 profile。官方 Ego Lite 0.6.2.7。
- 系统手动代理为本机既有 HTTP/HTTPS/SOCKS 代理。仅测试宿主通过
  `EgoSetupManager.configure_direct_hosts` 在“跟随系统／精确域直连”之间切换，未改 OS 网络。
  原目录 `/tmp/obc-multisite-20261106` 的 `bilinovel.com`、`www.bilinovel.com` 直连偏好保持不变。
  路由文件前后 SHA-256 均为
  `d99301d70eccef0d4556d0af4b33277d5b36107c9082ef3118c2013a4a0e2c92`。

## 真实对照与故障证据

| 请求 | 结果 | 含义 |
| --- | --- | --- |
| curl HTTPS 直连 | exit 35，`Connection reset by peer`，HTTP 000 | TLS 路径未通 |
| curl 现有代理 | CONNECT 200，最终 HTTP 403 | 代理隧道已通，网站内容未通过 |
| Ego 系统代理第 1 次 | 11.76 秒，403 Document，标题“请稍候…” | 到达安全验证页 |
| Ego 精确域直连 | 4.08 秒，`ego_navigation_failed / net::ERR_CONNECTION_RESET` | 与独立 HTTPS 对照一致 |
| Ego 系统代理第 2 次 | 7.46 秒，同样的 403 验证页 | 可重复到达验证页，不能算采集成功 |

上述三轮只做真实 Ego 导航／snapshot，观察用占位 collect 从未执行，不统计条目。
没有 Playwright 替代、证书关闭、验证码解题、代理节点变更或未授权站点写入。

只开放实际观察到的 `https://challenges.cloudflare.com` 资源／人工验证域进行诊断后，
浏览器实际发送该域 GET/POST 与站点自身验证请求，守卫拒绝数为 0；
累计 67.58 秒仍停在安全验证页。截图确认显示网站自己的验证组件，未得到作品列表。
这排除了该诊断轮次“验证域被权限守卫拒绝”，但不能据此断定网站拒绝的具体原因。

## 可修复的问题

正常产品 `prepare` 已返回 `needs_login`、0 样例，且拒绝激活；但人工 takeover 实际返回
`blocked_frame_origins=["https://challenges.cloudflare.com"]`、顶层跳转列表为空。
旧代码只把顶层跳转作为授权候选，因而草稿候选为空，显式提交这个已观察域仍返回 409。
网页和原生 App 也只读取顶层列表，用户无法沿正常界面允许验证框。

修复后：

1. 只有已有明确 403/503 文档、验证标题及可见验证文字时，才把被拒 iframe 加入候选。
   普通文章的广告 iframe 和静态资源不加入。最多八个精确公共 DNS origin，保留 URL 校验。
2. 首次 takeover、已有会话刷新及 action 共同刷新 `suggested_login_origins`，
   排除已授权域；候选不改变权限，不持久化页面正文。
3. Web 与 Flutter 原生使用最新列表，显式点击后只授权选中的域；空列表清除旧按钮。
   旧后端缺少字段时兼容顶层跳转，Web 的显式刷新同时获取新观察。
4. 原内容 origin、交互权限、公共 DNS 守卫、后台只读边界及请求预算保持。
   允许验证域没有把失败草稿变成可激活来源。

## 原生真实请求验证

- 使用本轮代码、实际 custom-source API 路由和真实 Ego；本地 18614 测试 API 指向全新
  `gate.db`，只承载该路由，不是整套日常服务。无模型调用、模拟模型响应或候选注入。
- iPhone 17 Pro / iOS 26.5 模拟器重新编译，Xcode 构建 104.8 秒。
  `native_verification_scope_live_test.dart` 通过通用 Flutter driver 运行，场景用时 25 秒。
  这是实际 Flutter 原生页面和真实 HTTP/Ego，不是 PWA，也不是物理手机。
- 实际链路：读取真实待验证草稿 → 原生页面显示精确域授权按钮和 Ego 画面 →
  点击 App 授权按钮 → 后端保存仅此登录域 → 新 Ego 会话不再拦截该 iframe →
  新画面绘制网站验证组件 → 退出原生页面 → 截图端点返回 409，证明会话已释放。
  没有点击网站验证码，也未声称完成新的完整聊天建源链路。
- 两张原生截图经人工核看，授权前／后的 SHA-256 分别为
  `d03fc29e3290b7bc5dd4ee00fb38959721ab3d69ef223d51aafb70cb6e08d4ef`、
  `1ca6e2316154902df4b0715bfe0365c86e2f8b638ac6641567edc36cabf213a1`。
- 随后真实 HTTP `resume` 返回 200、`needs_login`、`trial_success=false`；
  `activate` 返回 422 `successful trial required before activation`。
  隔离库保留一个草稿，0 来源、0 workflow、0 正式运行、0 样例，未触发采集入池。

## 检查与结论

| 验收门 | 适用性 | 结果 |
| --- | --- | --- |
| 临时代理／直连／代理对照及原配置保护 | required | PASS，真实 Ego、前后路由哈希一致 |
| 观察到的验证 iframe 可显式授权 | required | PASS，修前真实 409，修后原生点击与持久记录通过 |
| 普通广告／未观察域／越界 origin 不授权 | required | PASS，行为与既有 Ego 守卫回归 |
| Web 控件更新与原生渲染／退出释放 | required | PASS，Web Node 控件测试；原生实际 HTTP/Ego 与截图 |
| 网站验证与作品列表采集 | required | BLOCKED，当前代理仍是验证页，直连仍重置 |
| 新聊天编制、周期采集、评估入池 | required | BLOCKED，未获得作品列表，不用旧轮次或模型模拟补通过 |
| 安装包、Android/物理手机、账号登录 | 本轮无变更／未认证 | NOT_RUN |

- Python 关联 510 项通过，143.76 秒，文件清单保存在私有 `backend-regression-files.json`。
  其中包括新增授权边界与 dynamic-web exclusion 测试；早期 164 项为子集，不相加。
  新增四项回归修前失败；修后通过。实网站点请求使用 Ego。
- `node --test tests/js/source-login.test.mjs`：12 项通过，新增两项修前失败。
- `flutter test`：170 项通过；原生页面专项 6 项是子集。最终
  `flutter analyze --no-pub` 无问题，包含新 live harness。
- `ruff check src/ tests/`、改动 Python 格式检查、`mypy src/`（352 文件）和
  `git diff --check` 均通过。未运行全仓全部 Python 测试。
- 来源注册审计为 31 PASS / 11 MANUAL / 17 N/A / 0 MISSING，required missing=0；
  只证明注册，不代替真实网站可用性。

## 收尾与处理建议

新测试 API、专用 Ego 均已停止，模拟器恢复 Shutdown。原隔离库仍为 18 个停用来源、
55 次运行，未改变此前来源的策略。原始页面、截图、测试脚本与协议只留在私有证据目录。
源码修复留在独立后端／移动端分支的工作区，未合并或发布。

当前证据不支持永久改成代理：它只将 TLS 重置推进到网站验证页，尚未带来可采集内容。
保留用户原直连策略和待验证草稿；待站点能正常完成验证并取得真实样例后，才继续审批、
调度与入池验收。不会将这个来源报作“正常但空内容”或在两条失败路径间自动循环切换。

## 后续诊断：普通 Ego 人工验证入口

用户要求继续寻找解决办法后，在同一隔离 profile、同一系统代理下进行差分诊断。
本次实际读取应用版本为 **0.6.2.8 / 6.2.8**；未执行升级命令，不据此改写上一轮版本记录。

- 原产品会话再次复现：约 40 秒后仍为验证页，0 作品链接、0 请求权限拒绝，截图持续转圈。
  私有 `diagnosis/managed-baseline.py` 的真实作品列表断言失败，退出 1。
- 使用该安装的官方 `ego-browser nodejs` Page API，复用一个专用诊断 task space，
  两轮普通开页均显示“请验证您是真人”。第二轮截图确认是可见的复选框；页面可见但未获得焦点，
  因此焦点不是该普通开页轮次显示入口的必要条件。没有点击验证码，作品列表仍未通过。
- 只记录目标类型、origin、协议方法和错误码的临时诊断发现：页面及验证 iframe 创建的
  blob dedicated worker 被暂停，`Target.closeTarget` 返回 `-32602`。这解释了一个真实
  接管兼容性问题，但不是已经证明的唯一根因。worker 的 `Fetch.enable` 返回 `-32601`，
  不能直接照搬页面的逐请求守卫，也不能静默放开 worker 网络。
- 另一个隔离实验仅恢复本地计算：worker 原生全部 URL 阻断，并在执行前固定禁用网络与
  子 worker API；仍然转圈，没有解决原始症状。实验代码没有进入产品。
  一次误改父页面的诊断变体已明确标记无效，不参与上述结论。

本轮没有新的产品代码变更；既有修复与回归结果仍属于上一轮，不重复计为本轮通过。
临时脚本、受限日志及截图留在原私有证据目录的 `diagnosis/`；原直连规则保持不变。

下一道验收门需要真实人工操作：把普通 Ego 的同一诊断页面交给用户完成站点验证，
再由用户明确交回控制，测试该隔离 profile 的验证状态能否被原有受限采集会话复用。
仅显示复选框不等于验证成功；人工验证也尚不能证明后续后台周期采集稳定。
验证入口准备期间保持 0 来源、0 workflow、0 正式运行，不发布失败草稿。
