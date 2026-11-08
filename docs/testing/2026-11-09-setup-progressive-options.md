# 初始化表单按需展开验收（2026-11-09）

范围：首启向导移除手动免费组件准备按钮；首启、PC 初始化卡片与插件的 Bangumi / GitHub 额外字段随平台勾选展开。

## 验证

- Chrome 打开独立 worktree 的 `/setup/`，使用 `test_web_guided_init_e2e.py` 的隔离 HTTP 夹具并补齐两个只读免费实例。免费模型页无手动准备按钮，点击保存后顺序调用 `POST /api/config/sponsored/prepare`、两次 `POST /api/config/probe-service`、`PUT /api/config`，进入下一步。
- 首启及 `/web/`：默认未选 Bangumi / GitHub 时用户名、令牌和帮助全部不可见；单独勾选各平台只展开其字段；取消再勾选保留输入。没有修改用户正在运行的后端或个人配置。
- 插件采用同样的分组与显示逻辑，完成 JavaScript 语法检查及相关现有回归；本轮未加载候选插件进行浏览器实测。
- `node --check extension/popup/popup.js`、`node --check src/openbiliclaw/web/desktop/assets/js/app.js` 通过。
- `PYTHONPATH=src pytest -q tests/test_web_guided_init.py tests/test_desktop_asset_bundle.py tests/test_web_llm_instance_routing.py tests/test_sponsored_settings_api.py tests/test_desktop_web_config_probe.py`：105 passed，仅既有 FastAPI 生命周期弃用警告。
- `git diff --check` 通过。

这是界面与请求编排验收；本轮不重复调用真实模型。安装与后端准备接口、配置字段、初始化请求协议保持不变，尚未发布安装包。
