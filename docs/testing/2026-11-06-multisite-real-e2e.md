# 动态 Discovery 多站真实验收（2026-11-06）

范围：`discovery-only` 验证与修复，沿用 [dynamic-web 契约](../platform-source-contract.dynamic-web.toml)。
本轮覆盖 12 个不同站点，区分聊天建源、正式调度、真实评估入池、原生 App 与失败边界。
本轮测试、四项可修复问题的验证与清理已完成；多站正向接入的整体 verdict 为
**incremental only**：8 站通过，2 站外部网络阻断，2 站触及现有能力边界。
不能据此认证任意网站、所有账号能力或所有安装平台。

## 环境与证据来源

- 后端独立 worktree `ego-multisite-20261106`，分支 `test/ego-multisite-20261106`，
  基线 `37954c126d3c5e29f7d8e7c303dedbe06e32779a`。所有 Python import、API 和
  worker 均指向本 worktree；主工作区、其他 worktree 和日常服务不切换。
- 新隔离数据目录 `/tmp/obc-multisite-20261106`（0700），API 18612、模型桥 18613。
  候选与缓存均从 0 开始，使用明确标记的合成技术兴趣画像，未复制真实用户数据。
  请求、数据库、模型日志、安装日志和截图保存在该私有目录，不提交凭据。
- 网站执行为官方真实 Ego Lite **0.6.2.7** 原生通道；RSS / Atom / JSON Feed 为
  实际 HTTP 请求。没有 Playwright 替代、网页响应回放、手工候选注入或修改准入门槛。
- 原配置的 DeepSeek / GLM 两条模型路由均真实返回 429，之后仅在隔离配置中使用
  用户已授权的官方 Codex **GPT-6 Luna CLI** 兼容桥。实际模型处理聊天、编制、评估，
  tool calls 由产品真实执行；未提供脚本化模型答案。embedding 为真实 Ollama
  `nomic-embed-text`。桥接的流式时序和 token 计数不作为生产性能指标。
- 网络偏好保持 `direct_hosts=["bilinovel.com","www.bilinovel.com"]`，其他 Ego
  网站继续跟随系统代理。没有改全局代理或扩大直连域名。匿名 Feed 使用现有
  SafeWebTransport 的公共 DNS、robots 和 socket pin 规则。
- App checkout 为 `OpenBiliClaw-mobile-multisite-20261106`，基线 `7baf731`，
  产品代码 `3e66d85`；iPhone 17 Pro / iOS 26.5 原生模拟器。没有 App 产品代码变更。

## 站点矩阵

除注明的原生路径外，聊天使用与 App 相同的持久 turn / SSE / 审批 API，模型自行调用
真实建源工具。全部成功来源保留独立 source ID、份额和五分钟调度，正式运行使用原
Discovery worker；没有直接调用 producer 冒充正常调度，没有修改 next_run_at 或去重表。

| 站点与入口 | 执行器 | 结果及实际范围 |
| --- | --- | --- |
| Hacker News Ask：`news.ycombinator.com/ask` | Ego | 原生聊天→试跑→App 审批→份额 1→2→正式采集→接管→继续试跑→返回聊天通过；严格截图、退出释放、不同草稿重开通过；多轮各 3 条 |
| NGA 二次元：`bbs.nga.cn/thread.php?fid=-447601` | Ego | 聊天新增、审批、三轮五分钟调度通过；每轮 3 个实际帖子，保留 `read.php?tid=` 范围；最终资源优化后的正式复验也通过 |
| 少数派：`sspai.com` | Ego | 建源和首次调度通过；第二轮真实资源超限，修复后 6 次完整真实采集通过；同一聊天修正标题字段并更新原工作流成功，最终版本 3 连续三轮各 3 条，间隔 300.05／308.18 秒；最终原生 App 全流程也通过 |
| MDN Blog：`developer.mozilla.org/en-US/blog/` | Ego | 首条测试提示对作者字段有歧义；同一聊天澄清“缺少则留空”后沿用草稿完成审批；三轮调度和最终资源优化后复验通过 |
| Django：`www.djangoproject.com/weblog/` | Ego | 原生全流程通过，包含比例管理、正式采集、接管和恢复；之后原生再建 Atom 也通过；发布版本 2 三轮五分钟调度各 3 条，间隔 322.05／317.17 秒 |
| 阮一峰：`www.ruanyifeng.com/blog/atom.xml` | HTTP Atom | 聊天创建、审批、管理、三轮调度通过；后两轮实际 304，未重复入队；原生 App 也独立添加成功 |
| Solidot：`www.solidot.org/index.rss` | HTTP RSS | 聊天创建和三轮调度通过，实际新文章继续入队，重复项去重 |
| Daring Fireball：`daringfireball.net/feeds/json` | HTTP JSON Feed | 聊天创建和三轮调度通过，每轮 3 条，后两轮没有重复入队 |
| 哔哩轻小说：`www.bilinovel.com` | Ego | **BLOCKED**：已授权直连下首次及两次同草稿重试均 `net::ERR_CONNECTION_RESET`；独立 HTTPS 检查也被重置。没有改路由，没有激活；不能沿用十月的成功结论 |
| BBC：`feeds.bbci.co.uk/news/technology/rss.xml` | HTTP RSS | **BLOCKED**：实际 robots.txt 前置请求不可用。修复错误说明后真实 API 明确返回该原因，仍不激活或绕过规则 |
| V2EX：`www.v2ex.com/?tab=hot` | 专用域边界 | 原生聊天明确返回现有平台来源或显式 Feed 的引导。当前合同不允许将该已支持平台的 HTML 另建通用 Ego 源；正向测试等待审批被中断，不算 App 全流程 PASS |
| Planet Python：`planetpython.org/rss20.xml` | 聚合 RSS 范围边界 | 实际两次准备均发现超过 8 个文章 origin，返回 `content_scope_changed`，未激活。当前需较窄或分开的博客 Feed，再次确认同一大范围不会提高上限 |

上表共有 **12 个站点**，其中成功路径 8 个、外部网络阻断 2 个、现有能力边界 2 个。
少数派历史失败及字段修正需结合下节看待，不能把“后来成功”写成“从未失败”。

## 本轮修复及因果证据

1. **冷安装末页文案漂移。** 官方包下载与签名验证成功，但 0.6.2.7 把第三个末页选项
   改为“加入用户体验计划”，原严格识别器报 `ego_setup_unknown_options`。
   只加入实际观察到的新标签别名，仍要求恰好三个已知选项且全部关闭。
   新旧标签回归先红后绿（74 tests）；随后全新专用 profile 从第 0 至第 8 步自动完成，
   并验证原生连接。复验复用已校验的安装包，不宣称第二次重新下载；未知标签仍拒绝。
2. **少数派被动资源超限。** 原正式第二轮失败；私有只读诊断宿主保持原计划、路由、
   预算，六次执行的第六次复现：136 请求、17,412,281 字节，多张图片合计超限，
   最大图片约 4 MB。计数每个会话重置，不是跨会话累积。
   非交互观察／试跑／采集跳过 Image、Media、Font 下载，仍观察原域名授权；
   Document、Script、Stylesheet、XHR、Fetch 正常执行，交互页继续完整加载。
   原 250 请求、4 MiB 单响应、16 MiB 总量限制保留。
   修复后六次执行均 3 条，44 请求、约 2.73 MB，未放宽预算；协议回归先红后绿。
3. **少数派标题混入整张卡片。** 原计划及第一次聊天更新的真实样本均混入作者、日期、
   摘要，保留失败样本。真实 Ego DOM 诊断确认 `p.article__card__title`、摘要节点存在，
   但 100 项顶层观察被行链接和页头占满，卡片细字段未提供给模型。
   为最多 16 个抽样行各补最多 8 个文字字段，保留顶层上限、输入值保护与真实选择器；
   编制提示优先独立标题字段。确定性回归先红后绿；实际聊天再次更新后，三条样本
   标题均独立，作者和实际存在的摘要分别提取。发布为版本 3，原 source/workflow ID、
   历史、去重和份额 2／五分钟策略保留。期间模型一次错误传递更新参数，被明确拒绝后
   自行更正，未绕过校验；旧缓存文字不自动回写。
4. **BBC 错误原因不明确。** 将 `robots_unavailable` 映射为无法读取 robots.txt 的明确
   说明，与 robots 明确拒绝分开；测试确认不泄露底层代理／凭据字符串。
   最终实例真实请求仍被阻断并明确解释，修复的是诊断，不是 BBC 网络可达性。

## 端到端与准入证据

- `native-hn.log`：原生网站＋Atom 整轮 **2 分 32 秒 PASS**。
- `native-django.log`：第二站原生网站＋Atom 整轮 **2 分 52 秒 PASS**。
- `native-sspai.log`：最终全部修复代码下，第三站原生网站＋Atom 整轮
  **2 分 32 秒 PASS**；首次试跑三条标题即正确，实际 App 审批、份额设置、正式采集、
  完整交互页截图就绪、继续试跑和返回聊天均通过，无须再次提示修正字段。
- `native-visual-hn.log`：严格截图／释放／重开／真实恢复 **33 秒 PASS**。
- `native-visual-final.log`：资源优化后重跑严格原生用例 **34 秒 PASS**；实际绘制区域
  370×260.2，PNG 已人工核看，SHA-256
  `09defb4096dbd9064a311ec92785bd21131c2c21f3afeef1f8a4101a8fde15d8`。
- `final-web-cohort.json`：资源优化后 HN、NGA、MDN 原正式 worker 各采 3 条；
  HN／MDN 全部重复，NGA 有 2 个新候选。没有把重复读取算成新入池。
- `live-settings-negative.json`：5 个实际非法设置请求均 422，原来源份额／间隔／启用
  状态未改变。`live-draft-negative.json`：回环 URL、file URL 被拒绝；普通网页按
  HTTP Feed 创建返回明确非订阅结果，均未激活。
- 最终代码的 47 个关联测试文件 **887 passed / 268.20 秒**；Ruff 通过，MyPy
  **352 源文件通过**。新增字段观察修复另有 160 项直接关联回归通过。
  命令为 `PYTHONPATH=src .venv/bin/python -m pytest -q` 加上述 ego、custom、workflow、
  web 与 browser_workflow 文件集；排除无关的 `test_web_guided_init_e2e.py`。
  不是全仓所有 pytest。完整命令和输出见 `regression-final-all.log`、`ruff-final.log`、
  `mypy-final.log`；采用主仓库虚拟环境解释器，导入与测试文件来自本 worktree。
- 最终 **41 次正式调度**，38 次 `ok`、2 次实际 304、1 次修复前资源超限。
  共读取 114 条，33 条新候选、81 条重复；新候选全部完成真实评估，**12 条入池，
  21 条低分拒绝**，无待评估或评估错误。缓存分数 0.61–0.92，均为 `web/article`，
  保留具体 source ID 和名称；推荐 API 实际返回 8 条。候选／入队／入池分别计数，
  没有将所有采集结果算成推荐库存。
- 缓存按来源为阮一峰 3、HN 3、MDN 3、少数派 1、Daring Fireball 1、Django 1；
  NGA 与 Solidot 实际候选因原评分规则未入池，并非采集失败。重复创建的原生验证源
  继续使用原去重规则，不给重复文章再计入池。份额验证包括保存、回读、原生管理与
  正常来源注册，不以这些小样本声称推荐频次严格等于比例。
- `final-data.json`、`final-data-summary.txt` 保留完整分来源轮次、得分与去重统计。
  41 次包括修复前与后各阶段，不能全部称为最终代码运行；最终少数派版本 3、最终
  原生少数派＋Atom 以及资源优化后的 HN／NGA／MDN 证据分别标明。
- 12 个隔离测试来源已全部停用，28 个草稿会话已显式关闭。它们包含重复站点的原生
  验证实例，不等于 12 个成功站点。最后一次 Ego 采集完成后 **122.24 秒**检查为
  `ready/offline`，同时 API 仍返回 200；下一轮之前的空闲退出也实际观察到，之后冷启动
  继续成功。见 `idle-between-rounds.json`、`final-idle.json`。
- 随后关闭隔离 API、四个所属 worker／服务、Luna 桥和监视进程；18612／18613 端口
  均关闭，本轮托管 Ego 进程数为 0，模拟器恢复到原来的关闭状态。
  `cleanup.json` 确认调度仍为 41 次、候选 33、缓存 12、启用来源 0，域名偏好保持原值。
  其他既有服务／Ego 实例未停止，原始证据保留在私有目录。

## 验收边界

这是 macOS 真实 Ego＋实际网站＋真实模型＋iOS 原生模拟器的测试。
真实账号登录、付费内容、验证码、真机、Android、Windows、Linux/Docker Ego 和正式
安装包发布不在本轮 PASS 范围。接管／截图／按键／退出／恢复已验收，不能等同于成功
登录真实账号。普通聊天／假设操作本轮没有重复，沿用此前独立记录，不能混算成本轮。

首次广泛回归收集包含无关的旧浏览器 UI 测试，在其执行前中断并排除；后续回归不启动
该路径。单元协议 fixture 和 mock provider 测试仅证明回归行为，不计作真实网站验收。
静态契约审计为 31 PASS / 11 MANUAL / 17 N/A，required missing=0，只证明注册证据。

代码仅在独立分支实现及验证，未合 main、未推送、未发版，未切换日常部署。
