
# 用例设计与评审

消费 `req-processor` 的需求规范和 REQ 清单。模型逐条设计并评审业务场景，脚本检查关联、渲染工作簿和写入评审。这是总技能内部的用例设计与评审模块。

## 按任务读取

- 字段、来源、状态和命令以 [公共数据契约](../req-processor/references/data-contract.md) 为准；编号见 [编号规则](../req-processor/references/numbering-rules.md)。
- 设计用例时读 [设计规则](references/design-rules.md) 和 [用例模板](references/case-template.md)。
- 评审用例时读 [评审规则](references/review-rules.md)，同时读取原需求、用例 JSON 和工作簿。

设计和评审同时读取[框架能力、版本绑定与执行准备](references/automation-and-review.md)。

## 设计与检查

1. 读取模块的 `需求规范.md`、`REQ清单.json` 及相关来源、附图、跨模块信息。先运行上游规范检查；不足材料按 [设计规则](references/design-rules.md) 处理。
2. 只为 active 需求设计正式用例。P0 未决项阻塞的需求不进用例；其余已确认要求可以继续。保留未确认规则，不能自行确定重复名称、限额、取消或网络失败行为。
3. 保存 `用例数据.json`。正常、备选、异常、边界、非功能和端到端按需求适用性设计；不适用项说明原因。JSON 保留 AC、场景类型和设计方法。
   若已进入测试点流程，读取当前有效测试点评审结果，只使用通过点，在用例JSON记录关联测试点，并调用测试点模块的check_traceability.py检查多对多衔接。详见 [测试点契约](../testpoints/references/testpoint-contract.md)。新增字段不改变Excel前13列。
4. 渲染 13 列工作簿（含 `追溯` 派生表），再检查 REQ 与 AC 双关联覆盖。修复遗漏用例或错误关联后重跑；100% 仅表示已确认范围均有关联。覆盖率报告同时列出场景类型与设计方法的实际分布，并单列用例数为 0 的项；该提示不判定适用性，缺类要有评审的 `scope_checks`/`method_checks` 结论支撑。
5. 交付用例及覆盖报告，并注明阻塞/废弃要求、待确认事项和是否为草稿。没有 active 需求时报告不可设计。
6. **交付包必须一起给出**：`详细用例.xlsx`（含 `追溯` 表）、`需求覆盖率报告.md`、`测试点用例关联报告.md`；`用例覆盖矩阵.md` 只在评审发布后存在，属于评审阶段交付物。只发工作簿会丢掉 AC、场景类型、设计方法与测试点关联——这些按契约不写进前 13 列。

## 比较候选设计

同一模块存在两份及以上候选用例设计时（不同批次、不同模型或外部交付的工作簿），用对比脚本代替人工逐维比对：

```bash
python3 "$CASE_SKILL_DIR/scripts/compare_designs.py" --module-dir "$MODULE_DIR" \
  --alternative <候选A.json|候选A.xlsx> --label 候选A \
  --alternative <候选B.xlsx> --label 候选B
```

- 支持 `用例数据.json` 与 13＋1 列工作簿两种输入；工作簿没有场景类型/设计方法字段，只能按启发式估算并标注来源。
- 输出 `设计对比数据.json` 与 `设计对比报告.md`，不改动任何输入。
- 元数据深度不一时同时给出「口径 A（优先声明字段）」与「口径 B（全组统一启发式）」，两处结论不一致时以人工复核为准，不得只引用其中一个总分。
- 对比结果**不评审单份设计能否发布**，也不执行被测系统；候选之间的关联覆盖差异不能替代正式评审。

## 评审与交付

逐条检查需求依据、步骤与预期、独立性、优先级、自动化判断和重复内容；再检查适用场景与方法。先运行prepare_review.py创建当前版本待填写表；完成实际评审后保存绑定当前reviewed_source的评审JSON，再调用评审写入脚本。只在新的工作簿追加第 14 列 `AI评审`，不改原前 13 列或用例数据。结论、评分与待确认项分开汇报，评审报告明确没有执行真实系统。

## 命令

`CASE_SKILL_DIR` 为本说明文件所在模块目录；`REQ_SKILL_DIR` 为同包的需求模块目录；`MODULE_DIR` 为项目模块目录。根据实际位置设置变量，不固定 `.agents/skills` 或当前用户路径。

```bash
python3 "$REQ_SKILL_DIR/scripts/validate_spec.py" --module-dir "$MODULE_DIR"
python3 "$CASE_SKILL_DIR/scripts/render_xlsx.py" --module-dir "$MODULE_DIR"
python3 "$CASE_SKILL_DIR/scripts/check_coverage.py" --module-dir "$MODULE_DIR"
python3 "$CASE_SKILL_DIR/scripts/update_review.py" --module-dir "$MODULE_DIR" --review "$MODULE_DIR/评审数据.json"
python3 "$CASE_SKILL_DIR/scripts/compare_designs.py" --module-dir "$MODULE_DIR" --alternative <候选.json|xlsx>
```

每条命令成功后再运行依赖它的步骤。Python 需要 3.10+；Excel 写入需要可用的 Node 与 `@oai/artifact-tool`，运行时指定方式见公共数据契约。若依赖缺失，保留已写数据，清楚报告未生成或未验证的产物。

只有用户明确要求在材料不完整时直接生成草稿，才对渲染命令使用 `--draft`；该模式不能作为已通过覆盖门禁的交付。
