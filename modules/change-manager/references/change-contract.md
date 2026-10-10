# 变更契约

history/唯一快照包含module/文件副本、evidence/原文副本及snapshot.json SHA清单；文件被篡改或缺失会拒绝分析。历史原件不参与当前新规则的确认。
变更影响.json按稳定ID记录REQ/AC/TP/TC的added/deleted/deprecated/revised、source_relocated/source_evidence_changed/source_file_changed和execution_record_changed。来源位置只要求重新核对，执行记录变化不触发重设计；字符串差异仍需模型解释真实业务含义。

updates/批次保存REQ清单.json、需求规范.md、测试点数据.json、用例数据.json、更新计划.json、移除记录.json及分析报告。所有候选顶层draft=true；移除记录是不可变档案，无需把它改正式。
更新计划保存当前文件/来源指纹、基线清单指纹、affected_items、新增AC待覆盖及retirement_decisions。不能删影响项绕过门禁；apply还分析prepare后候选新增的变化。草稿期间当前输入再次变化，需要重新准备。

受影响记录必须：update_state=resolved、update_resolution真实说明；needs_redesign保留旧编号还须same_test_intent=true。同意图参数变化允许稳定ID，不同意图新ID。未处理缺字段/draft/needs_redesign标志拒绝。
所有删除项唯一retirement_decisions：kind/id/update_state=resolved/disposition=archive/reason；移除记录指纹保持不变。当前已有用例执行字段保持不变；删除用例保留历史记录，并不能把旧执行结果当新版结果。

正式发布隔离校验候选REQ及证据、active关系、AC归属、TP/TC关系、新增AC覆盖，成功后保存before-update快照。旧派生评审/覆盖矩阵/Excel/XMind移动到该快照stale-derived，更新应用报告.md及批次应用结果.json给出相对位置与重生成清单。发布故障自动回滚当前资料；若回滚部分失败，停止并使用报告中的完整备份恢复，不继续覆盖。
