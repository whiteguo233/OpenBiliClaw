# Jev 设置说明精简（2026-11-07）

基于 `9a0a2944`，仅调整桌面 Web、插件和移动 Web 的文案与折叠展示，不改变模型、配置字段、评估路由或 CLI。

## 用户说明

> 选择 Jev 后，只有候选内容评估改用 Jev；聊天、画像等仍沿用原来的 LLM 配置。

服务开关注明“不切换评估器”。模式限制与地址 / Token 说明默认折叠；数据发送、测试计费及后端失败原因仍可见。
简体、繁体、英文目录同步。未部署日常服务，也没有调用真实付费模型。

## 验证记录

- `pytest tests/test_i18n.py tests/test_jev_evaluator_wiring.py tests/test_jev_evaluation_config.py tests/test_jev_settings_api.py tests/test_jev_probe.py -q`：首次 113 passed / 2 failed。英文目录排序已修正；环境变量说明已由三端重复状态行改为共享表单展示，相应静态断言同步。两处失败及同组目录检查复跑 3 passed。
- `pytest tests/test_jev_connection_form.py tests/test_jev_settings_popup.py tests/test_jev_settings_mobile.py tests/test_desktop_web_settings_save_e2e.py tests/test_desktop_web_multimodal_settings.py -q`：首次 32 passed / 2 failed。Jev 重载测试原本要求长说明始终可见，已改为检查默认隐藏、展开后可见，单独复跑 1 passed。
- 另一项为 `test_external_runtime_config_event_rehydrates_settings`：一次未在 3 秒内刷新通用平台占比；本分支和原 `9a0a2944` 分支单独复跑各 1 passed。未修改通用刷新实现，也不将一次复跑成功视为消除了该测试的间歇性风险。
- 共享表单覆盖 375 / 768 / 1024 / 1440 宽度与明暗主题、Token 目标绑定、草稿测试状态失效；新增连接启用后仍保持原评估器、不会产生 LLM 配置更新，以及键盘展开 / 收起说明的检查。
- 插件 `npm run build`、`npm run build:bundle`、`npm run verify:assets` 通过；i18n 运行时 10 项通过。共享脚本和英文目录的构建副本与源文件一致。
- i18n 目录检查通过（2931 条源文），修改测试的 Ruff 检查 / 格式检查、`git diff --check` 通过。

## 页面复核

从正式 FastAPI factory 启动无真实密钥、禁用调度与来源的隔离实例，实际打开桌面 Web 和已加载的 MV3 插件。
路径：Web 左侧“设置”，或插件右上角设置按钮 → “高级功能” → “候选评分模式”。
检查一句话说明、连接开关、折叠项以及 URL / 模型 / Token 字段；没有点击付费测试按钮或写入日常配置。
本地截图位于本分支 `output/playwright/jev-settings-location-pc.png` 和 `jev-settings-location-extension.png`。
临时服务和浏览器已关闭；截图不含 Token。未重新认证模型质量、实体手机或完整安装流程。
