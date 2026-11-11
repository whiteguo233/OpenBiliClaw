# 相对惊喜推荐：定位、取舍与验证（2026-11-11）

分支：`fix/delight-relative-selection`。用户目标：从剩余合格候选里取前约 10%～15%，处理单条冷启动、消费与耗尽。本次选择前 10%，继续使用现有队列展示上限，不增加配置、依赖、模型请求或数据库列。

## 经典问题与采用的做法

1. **Top-K 后过滤导致结果不足或为空**。Elastic 官方文档明确区分 pre-filter 与 post-filter：后过滤即使有足够合格文档也可能返回不到 K 条。本项目采用同样的顺序原则：准入、文案、已读、避雷、时效与普通推荐历史先过滤，再计算名额和展示上限。这里借用的是检索阶段顺序，不引入向量索引或 HNSW。[Elastic：Filter approximate kNN results](https://www.elastic.co/docs/solutions/search/vector/knn/filtered-knn-search)
2. **同分边界与非确定性分页**。PostgreSQL 官方文档要求 LIMIT 配合可确定唯一顺序的 ORDER BY，并区分精确名额与 WITH TIES。惊喜不能只使用 `score >= p90`：同分会占空普通池。这里共享具体身份集合，以分数、相关度、发现时间、唯一存储键稳定决胜；SQLite 的 JSON 参数只用于传递这组精确身份，不新增依赖。[PostgreSQL：LIMIT and OFFSET](https://www.postgresql.org/docs/current/queries-limit.html)
3. **冷启动缺少历史交互，不必等到大量样本才可服务**。Google Research 的 RecSys 2023 研究比较了语言偏好与交互偏好的近冷启动推荐。本项目已经通过画像和 Evo 提供内容相关度，因此复用已有内容信号，直接展示合格池最好的一条，不要求先积累 150 条候选。[Google Research：Large Language Models are Competitive Near Cold-start Recommenders](https://research.google/pubs/large-language-models-are-competitive-near-cold-start-recommenders-for-language-and-item-based-preferences/)

10% 配额、向上取整、单条就绪即显示和点赞保留是本项目的产品取舍；上述资料不构成对这些数值的实验标定。

## 修复前的确定性复现

- 160 条分数在 0.60～0.68、正式快照就绪的内容，前 10% 应为 16 条；旧 `max(0.75, p90)` 返回 0。取消额外底线后返回 16。
- 150 条已读 0.90 与 10 条未读 0.80，旧分位线仍为 0.90，最终已读过滤后返回 0。
- 前 20 条命中避雷、第 21 条合格，旧接口 `limit=20` 返回 0，`limit=21` 返回 1。
- 新增回归中 13 个旧行为断言先失败；历史准备队列另有最小复现：一条已普通推荐的 0.99 历史占掉 limit=1 的准备名额，新鲜 0.65 内容仍没有快照，最终队列为空。

## 当前合同

```text
当前准入 + 正式文案/快照 + 可跳转 + 时效
  → 已读与 canonical seen 排除
  → 最新 effective dislikes / 普通推荐历史排除（未消费点赞保留）
  → 身份去重 / 稳定排序 → ceil(N * 10%) 具体身份
       ├ 惊喜读取 / 手动触发 / CLI / 主动推送计数
       └ 普通推荐 / 库存 / maintenance 使用同一占位
消费持久提交 → 重算剩余池 → delight.refreshed → 三端队列补位
                               └ 既有去抖、预算、退避和 presence 控制的补货
```

- 空池返回空；1 / 2 / 10 条选 1 条，11 / 149 / 150 / 160 条选 2 / 15 / 15 / 16 条。同分不扩额。
- 只有一条内容被惊喜占位时，初始化完成检查仍认可它，普通池为 0 不等于没有可浏览内容。
- ACK 只表示送达；view 排除已读；dismiss 写永久 seen；like 保留未消费卡。已有点赞可以超过池缩小后的配额，不额外扩充新精选。
- 准备队列优先当前 fresh，排除已读/普通历史和避雷后再取 batch limit。全部当前合格内容保留正式快照，之后补位不等待模型。
- 缺文案、不同步快照、过期、非法历史分数和未达到来源准入的条目不参加排名；正式文案发生写入竞态时原子拒绝。正常准入仍为配置值，exact `explore` 保持 0.58。
- 查看成功还会将 fresh 行原子转为 shown，归还 raw-material headroom；原始素材计数同时排除旧版遗留的 fresh + delight_seen 行，避免“展示已消费、补货却认为素材已满”。
- 单次相对读取直接返回当前具体身份，外部传来的旧尾分不能否决新一轮补位；动态阈值只作诊断和旧适配器兼容。
- 首屏空响应晚于补位响应时，旧版本会把新卡清空；新增浏览器复现先红。手机端现在让首屏与后续刷新共用同一代际号，并拒绝晚到快照。输入框打开时暂缓队列替换，失焦关闭后再取；同一卡片保留草稿/聊天和当前浏览位置，读失败保留原队列。桌面和插件复用已有 `delight.refreshed` 监听。
- 三端消费成功才发布补位事件。view 写入失败返回 500，不能假报成功或移除服务端候选。

## 验证入口

```bash
PYTHONPATH=src pytest tests/test_delight_relative_selection.py tests/test_delight_scorer.py
PYTHONPATH=src pytest tests/test_refresh_runtime.py tests/test_refresh.py tests/test_recommendation_engine.py
PYTHONPATH=src pytest tests/test_storage.py tests/test_pool_maintenance.py tests/test_api_app.py
PYTHONPATH=src pytest tests/test_mobile_web_probe_delight_e2e.py
ruff check src/ tests/
mypy src/
pytest -m 'not integration'
```

下列回归测试使用临时 SQLite 与模拟服务，浏览器测试通过仓库既有移动端 HTTP/WebSocket stub 驱动真实 Chrome。真实实例、真实 TCP 请求、来源/模型及页面操作的后续验收见 [真实环境验收](2026-11-11-delight-live-e2e.md)：接口与页面补位通过，模型生成受上游 429 阻断，不能视为完整生成链路通过。

生产数据验证只对本机保留库（最新发现记录 2026-10-21）做只读在线备份，在临时副本运行新引擎。该数据不能代表用户此刻的线上现场，原库没有被修改。回填为本地 Evo 分数和正式文案快照同步，模型调用数为 0；未消费点赞按旧合同保留。

文档同步覆盖推荐、存储、API、runtime、CLI、移动 Web、架构/spec、双语 README 图和当前版本 changelog。本次无安装器、依赖或配置字段变化，版本号不变，尚未发布或重启现有服务。

## 旧库副本与 CLI 输出

最终副本有效人口为 **246**，精选 **25 = ceil(246 × 10%)**：20 条 fresh，加 5 条用户已经点赞但未消费的保留卡；最小精选分数 0.80。旧版尚未给新鲜候选准备完整快照时，原有 5 张点赞卡继续可见。按现有每批 50 条回填，队列依次为 6 / 11 / 16 / 21 / 25 条，约 2.05 秒完成本地同步，模型调用 **0**。该时间来自本机单次样本，不是服务延迟承诺。

空库下通过真实 Typer `delight` 入口验证渲染（只替换 runtime 配置、画像与临时 DB，exit=0）：

```text
惊喜推荐
从池中寻找你可能意外喜欢的内容
暂时没有惊喜候选
当前没有可展示的惊喜内容：可能已看完、被偏好过滤，或文案仍在准备；
新内容就绪后会自动补充。
```

## 已完成检查

- 相关后端模块整组回归：1274 passed；此后库存消费修正重跑存储/runtime/边界组 482 passed，API delight 组 20 passed。
- 移动端真实 Chrome：16 passed，含补位、输入延后、草稿保留、旧首屏响应晚到的竞态。
- 移动/桌面/来源身份静态与行为守护：23 passed。
- `ruff check src/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 均通过。
- `pytest -m 'not integration'`：13,870 passed、51 skipped、189 deselected；首轮 1 个旧测试未区分 0.55 shortlist 与默认 0.60 入池线而失败。该测试现显式配置 admission=0.50，独立验证旧 shortlist 兼容性，同时另有测试保证 shortlist 不能绕过正式 admission；`pytest -m 'not integration' --lf --last-failed-no-failures=none` 最终 1 passed，无遗留失败。
- 最后两处消费/raw headroom 修正后，再跑存储/runtime/边界组合 482 passed、delight API 20 passed、补货协调器/来源 deficit 组合 80 passed。新增边界文件共 31 个参数化用例，涵盖消费到底、旧已读库存、旧历史准备挤占、非法分数、过期、失败写入、冷启动和点赞保留。

## Git 合入收尾（2026-11-11）

- 修复提交为 `e05d5c36`；真实请求验收与脱敏回执提交为 `9be71b27`。真实生成链路的上游 429 缺口继续保留，不因合入代码而改判成功。
- 合入前 `main` 与 `origin/main` 均为 `020d4151`，修复分支从该基线直接演进；收尾采用快进合入并推送 `origin/main`，保留原始修复和验收提交。
- 合入前追加检查：`PYTHONPATH=src pytest -q tests/test_delight_relative_selection.py tests/test_docs_index.py tests/test_release_consistency.py`，**38 passed**。应用代码与已经完成完整检查、真实请求验收的版本一致；文档、架构与 changelog 随分支同步。
- 版本继续为 `0.3.237`，本轮只收尾 Git 交付，不发版或重启正式服务。
- 清理范围仅为本任务已合入的本地分支与 worktree；主工作区原有 `outputs/`、`promo/` 以及其他任务的 worktree 保留。
