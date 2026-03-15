# Profile Groups Density Design

当前 popup 画像页虽然已经有 4 组结构，但每组内部内容偏少，尤其是“最近常点开的”会显得薄；同时底层已经存在 `disliked_topics`，却没有在画像页显式展示，导致用户只能看到“喜欢什么”，看不到“明显会避开什么”。

这次只做最小扩容，不重做画像结构：

1. 保留现有画像页分组顺序和整体布局
2. 放宽各组返回上限：
   - `core_traits` 最多 `6`
   - `deep_needs` 最多 `5`
   - `top_interests` 最多 `8`
3. 在 `/api/profile-summary` 增加 `disliked_topics`
4. popup 新增一组 `最近明显会避开`

这样可以补足两个核心问题：

- 画像每层内部不再过于稀薄，尤其兴趣层会更像真实“最近常点开的”
- 用户能直接看到稳定避雷方向，而不是只从反馈或认知卡里间接猜

有意不做的事：

- 不增加新的长期画像层次
- 不把 `personality_portrait` 拉得更长
- 不改 cognition card 结构
- 不改 SoulProfile 的生成 schema，只调整 profile-summary 的聚合与展示
