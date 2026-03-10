# M105 手动刷新异步化实现计划

1. 为 `ContinuousRefreshController` 增加手动刷新状态模型与后台任务入口
2. 扩展 `/api/recommendations/refresh` 和 `/api/runtime-status` 的返回结构
3. 为手动刷新状态与接口新增后端测试
4. 修改 popup 刷新按钮逻辑，切到“触发后轮询状态”的模式
5. 为 popup 刷新交互新增/更新测试
6. 更新 `docs/modules/extension.md`、`docs/changelog.md`、`docs/v0.1-todolist.md`
7. 运行 `ruff check src/ tests/`、`mypy src/`、`pytest -q`
8. 运行 `extension` 下 `npm test -- --runInBand`、`npm run typecheck`、`npm run build`
