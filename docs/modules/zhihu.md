# 知乎（Zhihu）

知乎同时提供浏览器任务型发现、账号信号导入和平台原生保存。网络请求由扩展在真实 `zhihu.com` 页面及当前登录会话中执行；后端只持有任务、脱敏结果与布尔登录态，不接收 `z_c0` 的值。

## 原生保存确认

本地 `favorite` 或 `watch_later` 需要同步到知乎时，后端创建 durable native-save job，扩展 runner 先登记其拥有的临时 tab，再导航到精确的 question、answer 或 article URL。任务 tab 在首次加载和复核重载期间都禁用普通行为采集，避免自动化点击进入 `/api/events`。

当前知乎桌面端把内容收藏暴露为条目级全局 `收藏 / 已收藏` 开关，而不是保存时选择命名收藏夹的弹窗。因此能力目标固定为 `知乎收藏`，`supports_named_collection=false`。执行器只在精确内容容器内、且状态明确为“收藏”时点击一次；若初始就是“已收藏”，直接返回 `already_synced`，绝不会点击这个可反向取消收藏的控件。

点击后只有同一精确内容的控件明确转换为“已收藏”才返回 `synced`。若页面没有及时反映状态，runner 会终止并等待 mutation document 的 sender，重载同一内容页，取得新的 `document_instance_id` 后才发出 `verification_only`；复核只读当前开关，不打开弹窗、不创建收藏夹，也不点击任何控件。只有新文档明确显示“已收藏”才把原始 `native_confirmation_not_observed` 升级为 `already_synced`；登录失败、控件缺失、超时或其它不确定结果都保留原失败语义。

结果回调要求后端返回 2xx，并在独立总截止时间内以同一 payload 有界重试。后端仅幂等确认完全相同的 canonical terminal replay；改变状态、错误码或规范化消息的晚回调仍返回冲突。无论回调是否成功，runner 都尝试关闭临时 tab；只有确认 tab 已删除或本就不存在时才清理 owner 记录，未知删除失败保留恢复线索。

## 身份与安全边界

- 内容身份只接受 `question:<numeric-id>`、`answer:<numeric-id>` 或 `article:<numeric-id>`，URL 与 typed id 必须相互一致。
- 原生目标固定为 `知乎收藏`；`watch_later` 在知乎能力矩阵中回退到同一个全局收藏开关，不宣称命名收藏夹能力。
- Cookie 同步只检查 `z_c0` 这个名称是否存在并向后端报告布尔值，不读取或上传 Cookie 值；这个 heartbeat 只是 readiness hint，不是收藏成员关系证明。
- 真实 E2E 的每次平台写入都需要一个新鲜、精确的授权 envelope，包含 platform、action、public content id 和 expected target。测试只使用隔离项目根目录，禁止把 smoke 产物投影到 seen、affinity、memory 或 profile。

对应能力契约见 `docs/platform-source-contract.zhihu-native-save-confirmation.toml`，跨平台运行手册见 `docs/testing/six-platform-native-save-e2e.md`。
