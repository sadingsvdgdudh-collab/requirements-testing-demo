# 独立测试点、评审和XMind

这是requirements-testing的内部模块，执行用户请求涉及的阶段。先读取需求规范、REQ清单、原文及相关图片。
没有这些材料时先进入需求整理模块。只为active要求提取正式测试点，保留待澄清范围。

## 按当前任务读取

- 编写或导入当前格式测试点时读 [数据契约](references/testpoint-contract.md)。
- 提取及评审时读 [设计与评审规则](references/design-review-rules.md)。
- 生成XMind时读 [导图规则](references/xmind-format.md)。

## 输出测试点

模型根据需求逐条设计检查点，保存测试点数据.json。测试点描述“检查什么”，包含具体条件、数据和预期，通常比详细操作用例简短。
一个REQ可以有多个测试点，一个测试点可以验证多个验收条件。边界与异常只能基于已知规则，未明确上限等进入待澄清。
运行export_testpoints.py检查数据并输出测试点.md、测试点覆盖率数据.json和测试点覆盖率报告.md。
脚本渲染清单、计算关联，不能自动替代业务分析或把关联100%说成充分覆盖。

## 评审测试点

对照原文逐点评审，填写测试点评审数据.json，同时检查六类场景和六种方法的适用性及证据。
运行review_testpoints.py生成测试点评审结果.json、测试点评审报告.md和测试点覆盖矩阵.md。
评审仅标注，不修改源JSON；根据当前请求可以修正自己的设计，再重评。用户只要求评审时保留用户原内容。
通过点、需确认点、不通过点分别报告。高分不抵消缺失或未通过，脚本通过仅表明输入与评审结构有效。

## 输出XMind

运行export_xmind.py生成测试点.xmind，按REQ展示测试点、优先级和当前评审状态；备注保存条件、数据、预期、来源与意见。
没有评审时导图标“未评审”。有过期评审时先重评，不把旧结论放进新图。
导图是规划资料，不是执行结果；可以只交付测试点和导图，不强制生成Excel。

## 衔接详细用例

需要继续设计用例时，用当前“通过”测试点作为依据，在用例JSON增加关联测试点数组。
测试点和用例允许多对多。REQ/AC集合必须与关联测试点一致，新增元数据不改变Excel前13列。
运行check_traceability.py检查每个通过点至少一个用例、每条用例的TP有效且通过、REQ/AC没有错挂。
其他点尚未通过时，可继续处理通过范围，报告明确全范围仍有待处理事项。

## 命令

POINT_MODULE_DIR是本说明文件所在目录，MODULE_DIR是业务模块目录。Python需3.10+，本模块不需要额外库。

```bash
python3 "$POINT_MODULE_DIR/scripts/export_testpoints.py" --module-dir "$MODULE_DIR"
python3 "$POINT_MODULE_DIR/scripts/review_testpoints.py" --module-dir "$MODULE_DIR" --review "$MODULE_DIR/测试点评审数据.json"
python3 "$POINT_MODULE_DIR/scripts/export_xmind.py" --module-dir "$MODULE_DIR"
python3 "$POINT_MODULE_DIR/scripts/check_traceability.py" --module-dir "$MODULE_DIR"
```
