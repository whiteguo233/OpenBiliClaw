# 免费方案打包集成与剩余问题回归（2026-11-06）

本轮基于 PR336 的 `fffcf74a`，独立分支 `fix/sponsored-release-integration`。继续使用已公开签名的 `sponsored-runtime-2026-11-05.1`，未读取或改动共享Key、私有策略签名材料。本轮没有发布主程序或合入main。

## 改动与交付合同

| 问题 | 处理 |
| --- | --- |
| 后端包可能遗漏免费组件 | PyInstaller和Docker必须纳入当前平台固定SDK；缺发行、摘要不符、验签失败直接中止。复制先在临时目录校验，成功才落目标。 |
| 更新包仍命中旧Key组件 | 多个本地有效组件中优先当前Core固定manifest，防止旧active指针遮住新包。独立签名的新旧夹具验证离线更新无需下载。 |
| 冻结包只检查Python导入 | 安装后自检增加随包SDK验签和真实原生 `--check-policy`；新用户配置必须启用内置免费Provider。 |
| Provider入口不够明确 | 初始化、PC、插件、手机共用“硅基流动免费方案”卡片，即使关闭仍保留；后端提供只读身份和两个模型/上下文目录。 |
| 本地向量误报未安装 | 用户设置测试给Ollama 45秒；超时标记 `EMBEDDING_BUSY`、可重试，组合测试保留实际原因；后台健康预算不变。 |
| 觉察误引用历史回看 | 本轮事件、只读回看分开；失败后唯一重试携带具体字段原因，仍拒绝越界引用、不推进失败水位。 |
| 两屏规划超时/错误嵌套 | 免费模型只输出有序引用；后端机械分组并沿用保守brief，继续全部配额/重复/覆盖校验，默认20秒、至多纠错一次。 |
| 大段记忆取错记录 | 新增只读 `read_memory.json_pointer` 精确字段查询，返回完整原值；错误路径/超大结果明确拒绝，不能用截断结果猜测。 |

## 真实模型请求

模型测试关闭其它免费模型和付费回退，真实 Rust/SSE、生产AgentLoop、工具执行、SQLite认知水位与HTTP设置探测。夹具只包含合成行为/记录；每次修复后使用原基线的新副本和新事件。证据保留在 [evidence.json](assets/sponsored-release-integration/evidence.json)，包括原始失败。

| 场景 | Qwen3-8B | Qwen3.5-4B |
| --- | --- | --- |
| 6条候选分两屏，原20秒预算 | 通过，3.322秒，1次调用 | 通过，3.878秒，1次调用 |
| 1200条存储记忆精确查询两条原值 | 通过，5.256秒，4次模型调用 | 严格首轮无错误检查未过；工具参数误用后收到提示并自行改正，最终取值正确 |
| 新4条行为＋历史回看的认知落库 | 通过，27.051秒；来源仅新批次且水位保存 | 通过，7.906秒；来源仅新批次且水位保存 |
| HTTP三路并发模型＋云端向量测试 | 3/3通过 | 3/3通过 |

本地向量独立组合（8B＋本地bge-m3）：精确记忆6.308秒、认知39.546秒均通过；三路并发设置探测3/3通过，单路5.062/6.526/8.101秒。没有重启用户的Ollama或修改用户模型。

规划的第一版仅缩短提示仍在两次调用中生成损坏JSON；该失败另存 `cloud-8b/director-after.json`，随后改为扁平引用协议。最早认知复测的结果序列化用了不存在的 `AwarenessNote.to_dict()`，属于验收脚本错误；改为 `dataclasses.asdict` 后在全新基线副本重跑，未复用已经成功推进的水位。

长上下文原始1200记录直接投喂仍不是可靠精确检索：额外开启低档思考的4次对照仅1次严格通过（8B取错一次，4B两次添加空格）。本次新增的是项目的精确读取能力，**不宣称修复了模型本身的长文本能力**。4B首次工具调用或最终输出格式仍可能偏离要求；默认保持8B。错误引用与结构化结果继续先验证再写入，失败走已有有限重试/降级。

## 浏览器、CLI与安装验证

- 真实 `/setup/` 默认选硅基流动，显示8B/128K及4B/256K；选择4B后真实测试成功并保存进入账号连接步骤。[截图](assets/sponsored-release-integration/setup.png)
- PC设置关闭免费方案后仍显示内置卡片；重新启用、改8B、测试、保存，运行时热更新成功。[入口保留](assets/sponsored-release-integration/provider-disabled.png)
- PC切换本地Ollama，真实测试成功（4.4秒）并保存；CLI `config-show` 确认默认注册为 `sponsored`、8B＋本地bge-m3。[截图](assets/sponsored-release-integration/settings-local.png)
- 真实下载Mac Intel SDK、重复安装复用、随包stage、损坏更新保留原组件全部通过；直接调用新的冻结入口自检，实际SDK原生策略检查通过。
- Python专项297通过、2跳过；浏览器自动化70/70，插件构建及测试1859/1859，网页JS150/150；Ruff、MyPy（386文件）、i18n（2983条文案）、shell语法通过。
- 本机全量pytest运行10分05秒，2879通过、2跳过、无失败后主动中止，完整门禁改由下述CI承担；不把中止记录当成全量通过。

完整CI与三平台实际安装包构建结果将在本记录补充。手动构建只生成Actions附件，不创建GitHub Release。

## 复验

在新隔离目录先运行 `scripts/sponsored_system_acceptance.py` 生成合成基线，再运行 `scripts/sponsored_release_acceptance.py --root <基线目录>`；重复试验必须创建新副本或使用新的报告名，不能覆盖失败证据。规划可用 `scripts/sponsored_extended_acceptance.py --root <基线目录> --report-name director.json --only director_two_screens_with_default_planning_budget`。实际安装检查用 `scripts/sponsored_install_smoke.py`。

用户个人B站登录仍需要其自行恢复；本轮没有将公开/合成数据验收冒充个人账号导入成功。其它此前覆盖的日常流程及失效凭据、网络中断、进程恢复边界见[上一轮完整验收](2026-11-05-sponsored-comprehensive.md)。
