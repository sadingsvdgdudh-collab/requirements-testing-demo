# 测试点数据契约

基础REQ、AC、来源和状态以 [需求数据契约](../../req-processor/references/data-contract.md) 为准。
新增文件在同一output/模块名/目录。结构版本保持1.0。

## 测试点数据.json

```json
{
  "schema_version": "1.0", "module": "商品管理", "code": "PRO",
  "testpoints": [{
    "测试点编号": "TP_PRO_001", "优先级": "P1",
    "测试点名称": "名称为空时阻止新增",
    "前置条件": "已登录并打开新增商品页，能够核对本次新增结果。",
    "检查内容": "名称为空且价格合法时保存，检查校验提示及未创建结果。",
    "测试数据": "名称为空；价格99.90。",
    "预期结果": ["提示‘请输入商品名称’。", "未创建商品。"],
    "关联需求": ["REQ_PRO_001"], "验收标准": ["AC_PRO_001_01"],
    "场景类型": "异常", "设计方法": ["等价类"]
  }]
}
```

所有示例字段必填。TP编号为TP_模块缩写_至少3位ASCII正序号，唯一、不复用。
优先级P0/P1/P2/P3；六类场景、六种方法与详细用例契约一致。顶层draft=true不能作为正式测试点，导入或更新后须补全并确认。
关联需求、验收标准、设计方法为不重复非空数组；预期结果为非空文字数组。条件、检查、数据均须具体。
模块名/code与REQ清单一致，仅关联active要求，AC必须属于关联REQ。
来源直接从关联REQ中读取，展示真实文件、行范围和原文，不让模型重复维护一份可能错位的来源。
一个点可以关联多个REQ和AC，不自动推定同REQ全部AC均被覆盖。
没有active需求时输出不可设计报告，不能报100%。

导出结果为测试点.md、测试点覆盖率数据.json、测试点覆盖率报告.md，分别统计REQ及AC关联。
未覆盖时仍输出清单和报告，退出1，帮助补齐设计。blocked/deprecated范围单列。

## 测试点评审数据.json

结构同 [用例评审结构](../../req-processor/references/data-contract.md)，但reviews编号字段为测试点编号，证据全部使用TP编号。

```json
{
  "schema_version": "1.0",
  "reviews": [{"测试点编号": "TP_PRO_001", "结论": "通过", "原因": "输入与预期有明确原文依据。", "建议": "无。"}],
  "scope_checks": [{"item": "异常", "status": "covered", "evidence": ["TP_PRO_001"], "reason": "覆盖名称为空校验。"}],
  "method_checks": [{"item": "等价类", "status": "covered", "evidence": ["TP_PRO_001"], "reason": "空值无效类。"}]
}
```

示例只展示一项；实际scope_checks须包含全部六类，method_checks须包含全部六方法，不能缺项或重复。
每个TP恰好一个评审，结论通过/需确认/不通过，原因建议不空。
covered必须有真实且匹配类型/方法的TP；missing/not_applicable须说明理由，不能将已存在类型标不适用。

## 可消费评审结果

脚本生成测试点评审结果.json，包含module/code、reviews、scope_checks、method_checks、全部点coverage、通过点approved_coverage及summary。
reviewed_source记录REQ清单与测试点数据的SHA256，后续读取结果时核对，避免沿用过期结论。
业务输入变动后重新评审；不要手动更改派生结论或指纹。评分沿用45/30/15/10分结构，关联分按通过点覆盖计算，场景与方法覆盖也只有通过点证据支持时计分。
存在missing、需确认或不通过时summary.ready为false，即使自检得分很高仍报告待处理事项；不产出字母等级。

## 用例关联

用例数据的每条case增加：

```json
{"关联测试点": ["TP_PRO_001"]}
```

仅关联当前已通过的TP；该TP的REQ与AC必须包含在用例自身关联中。
通过点至少一个用例，允许一个用例覆盖多个点。新字段保留于JSON，不增加Excel业务列。
check_traceability.py在设计用例后生成测试点用例关联报告.md，缺失、错挂或过期评审退出1。
已有v1用例可以沿用原路径；只有进入新增测试点衔接流程时要求这些关联。
