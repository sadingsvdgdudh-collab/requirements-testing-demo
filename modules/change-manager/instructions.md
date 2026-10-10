# 需求变更与用例更新

读本说明和[变更契约](references/change-contract.md)。语义更新由模型根据新版原文设计，脚本负责快照、结构差异/依赖传播、发布门禁和历史备份，不运行游戏。

1. 变更前snapshot保存模块设计资料、Excel/XMind/评审和原文证据。新版原文使用新文件，保留旧输入；不要直接覆盖唯一的历史策划案。
2. 根据新版规则修订当前REQ、AC及九节规范，保留稳定编号或明确废弃。运行analyze_changes对比旧基线，分清规则变化、纯来源移行及执行记录变化。
3. prepare_update生成updates/批次的三个JSON草稿及更新计划。受影响点/用例标needs_redesign或needs_evidence_check；未影响项保留，执行结果不能被设计更新清空。
4. 模型阅读新规范逐条修订步骤、数据、边界及预期。不是用字符串替换数字。同意图参数修订保留ID须same_test_intent=true；改成另一测试意图新增编号，旧项在retirement_decisions明确archive+reason。
5. 真正完成后，候选JSON/更新计划顶层draft=false、update_state=resolved；各受影响记录有resolved及update_resolution。新增AC必须有相关测试点和用例覆盖，不能只塞一个编号。
6. apply_update检查基线、源指纹、全部最新关联及候选差异，拒绝草稿、过期材料、悬空关系和不明证据。先备份，再发布；失败尝试恢复原资料并报告可恢复备份位置。
7. 旧评审、覆盖率、Excel和XMind归档到history，需重新导出、逐点评审、用例评审和覆盖检查。新版设计通过结构校验不等于业务评审通过，更不等于测试执行通过。

```text
snapshot.py --module-dir 项目/output/商品管理 --label 初版
analyze_changes.py --module-dir 项目/output/商品管理 --baseline 项目/output/商品管理/history/具体快照
prepare_update.py --module-dir 项目/output/商品管理 --baseline 项目/output/商品管理/history/具体快照
apply_update.py --module-dir 项目/output/商品管理 --draft-dir 项目/output/商品管理/updates/具体批次
```

脚本在scripts/，Python 3.10+。外部快照可只读分析，prepare/apply基线必须在本模块history。快照是可检查的文件备份，不提供一键把历史评审冒充当前有效结果的恢复命令。需恢复时先保留当前快照，再根据明确用户授权拷贝所需旧设计/来源并重新验证，历史派生结论须重生成。
真实20→30字符变更演示见examples-v12/change-example。
