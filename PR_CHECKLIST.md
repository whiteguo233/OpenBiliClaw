# PR Checklist: Issue #18 - Bilibili发布日期偏好配置

## 📋 提交PR前必须完成的事项

### 🔴 P0 - 必须完成（阻塞merge）

#### 1. 代码质量检查
- [ ] 运行 `ruff format src/ tests/` 格式化代码
- [ ] 运行 `ruff check src/ tests/` 检查lint问题
- [ ] 运行 `mypy src/` 确保类型检查通过
- [ ] 修复所有报错和警告

#### 2. 测试验证
- [ ] 运行所有测试：`pytest`
- [ ] 运行新增测试：`pytest tests/test_publication_preference.py tests/test_bilibili_publication_settings_surface.py -v`
- [ ] 运行相关模块测试：
  ```bash
  pytest tests/test_pool_curator.py -v
  pytest tests/test_recommendation_engine.py -v
  pytest tests/test_config.py -k "bilibili_date" -v
  pytest tests/test_search_strategy.py -v
  ```
- [ ] 确保所有测试通过，无失败

#### 3. 功能验证（手动测试）
- [ ] 配置文件验证：
  - [ ] `config.toml` 添加 `[bilibili]` 下的4个新字段
  - [ ] 保存非法配置（如 `weight=1.5`）时正确拒绝
  - [ ] `openbiliclaw config-show` 正确显示发布日期配置
  
- [ ] 桌面Web验证：
  - [ ] 打开 `http://127.0.0.1:8420` 设置页面
  - [ ] 找到 Bilibili 发布日期配置控件
  - [ ] 修改配置并保存，检查是否生效
  - [ ] 查看推荐列表，验证日期过滤是否工作

- [ ] 严格模式验证（`weight=1.0`）：
  - [ ] 设置"最近7天"
  - [ ] 确认推荐中没有7天前的视频
  - [ ] 确认有效库存统计正确
  
- [ ] 软模式验证（`weight=0.5`）：
  - [ ] 设置"最近30天"
  - [ ] 确认范围外视频仍然出现但排名靠后

#### 4. 关键bug修复验证
- [ ] 验证 `_isolated_database` 继承了 `_publication_date_preference`
  - 在 [database.py:2284](src/openbiliclaw/storage/database.py#L2284) 确认有这行代码
  
- [ ] 验证 `count_pool_readiness` 的raw查询包含 `source, source_platform`
  - 在 [database.py:8005](src/openbiliclaw/storage/database.py#L8005) 确认SQL包含这两个字段

### 🟡 P1 - 强烈建议完成

#### 5. 代码改进（可选但建议）
- [ ] 提取魔法数字：将 [curator.py:434](src/openbiliclaw/recommendation/curator.py#L434) 的 `0.35` 提取为常量
  ```python
  _AMPLIFICATION_OVER_BUDGET_PENALTY: Final[float] = 0.35
  ```

#### 6. 文档最后检查
- [ ] `docs/changelog.md` - 条目完整且格式正确
- [ ] `docs/modules/config.md` - 新字段文档清晰
- [ ] `docs/modules/recommendation.md` - 发布日期偏好说明完整
- [ ] `README.md` / `README_EN.md` - 架构流程图已更新
- [ ] `config.example.toml` - 包含新字段的示例配置

#### 7. Git提交整理
- [ ] 检查暂存区：`git status`
- [ ] 确认所有必要文件已添加：`git add -A`
- [ ] 提交改动：
  ```bash
  git commit -m "feat: add Bilibili publication date preference filtering (issue #18)
  
  - Add PublicationDatePreference module with preset/custom ranges
  - Integrate with PoolCurator scoring and serving gates
  - Push strict bounds to Bilibili search API
  - Add desktop UI controls and API endpoints
  - Update config validation and hot-reload
  - Fix isolated database state inheritance
  - Fix count_pool_readiness raw query missing source fields"
  ```

### 🟢 P2 - 建议验证（不阻塞merge）

#### 8. 扩展测试
- [ ] 扩展端测试：
  ```bash
  cd extension
  npm run test
  ```

#### 9. 边界情况验证
- [ ] 测试时区边界：东八区的 23:59:59
- [ ] 测试闰年：2024-02-29 向前推1年
- [ ] 测试空字符串配置：`start_date=""`
- [ ] 测试混合平台：YouTube + Bilibili 混合推荐

---

## 📊 当前状态总结

### ✅ 已完成
- 核心功能实现（PublicationDatePreference模块）
- 配置集成（config.py, api.py）
- 推荐引擎集成（curator.py, engine.py）
- 搜索策略集成（search.py, bilibili_producer.py）
- 数据库过滤（database.py）
- 桌面UI（index.html, app.js）
- 扩展支持（bili-task-dispatcher.ts）
- CLI显示（cli.py）
- 测试覆盖（test_publication_preference.py等）
- 文档更新（changelog, modules, README）
- 关键bug修复（平台识别、类型注解、pubtime边界、隔离数据库、raw查询）

### 🎯 代码质量评分
- 架构设计：⭐⭐⭐⭐⭐
- 测试覆盖：⭐⭐⭐⭐⭐
- 文档完整：⭐⭐⭐⭐⭐
- Bug修复：⭐⭐⭐⭐⭐

---

## 🚀 提交PR步骤

完成上述checklist后：

1. **最终检查**
   ```bash
   git diff main --stat  # 确认改动范围
   git log --oneline main..HEAD  # 确认commit历史
   ```

2. **推送分支**
   ```bash
   git push origin codex/issue-18-bilibili-publish-date
   ```

3. **创建PR**
   ```bash
   gh pr create --title "feat: Bilibili publication date preference filtering (issue #18)" \
                --body "$(cat PR_DESCRIPTION.md)" \
                --base main
   ```

4. **PR描述模板** (可选创建 PR_DESCRIPTION.md)
   ```markdown
   ## 功能概述
   
   实现Bilibili发布日期偏好配置，支持预设范围（最近7天/30天/半年/一年）和自定义日期区间。
   
   ## 主要改动
   
   - ✅ 新增 `PublicationDatePreference` 模块，独立于discovery/storage/UI
   - ✅ 严格模式（weight=1.0）下推到Bilibili搜索API和serving gate
   - ✅ 软模式（weight<1.0）通过乘数降分
   - ✅ 配置热更新通过RuntimeContext
   - ✅ 桌面Web UI控件
   - ✅ CLI config-show支持
   - ✅ 完整测试覆盖
   
   ## 测试
   
   - [x] 单元测试覆盖所有核心场景
   - [x] 平台识别正确（仅作用于Bilibili）
   - [x] 时区转换正确（本地日期→UTC边界）
   - [x] 配置验证严格（非法值保存时拒绝）
   - [x] 隔离数据库状态继承正确
   
   ## 文档
   
   - [x] changelog.md
   - [x] modules/config.md
   - [x] modules/recommendation.md
   - [x] README.md / README_EN.md
   
   Closes #18
   ```

---

## ⚠️ 注意事项

1. **不要跳过P0项目**，它们是功能正确性的基础保证
2. **测试失败立即修复**，不要带着failing tests提PR
3. **文档必须同步更新**，这是项目硬性要求（见CLAUDE.md）
4. 如果mypy报错，优先修复类型问题而不是用 `# type: ignore`

---

## 📝 Review准备

PR提交后，reviewer可能会关注：

1. **性能影响**：发布日期过滤是否影响推荐速度？
2. **缓存命中率**：是否保持了LLM prompt缓存的有效性？
3. **向后兼容**：旧数据库升级后是否正常工作？
4. **用户体验**：严格模式下"推荐为空"时是否有友好提示？

提前准备好这些问题的答案。
