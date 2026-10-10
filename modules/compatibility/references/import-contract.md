# 导入契约

CLI：--input FILE --module-dir DIR --kind requirements|testpoints|cases [--code CODE] [--sheet NAME] [--header-row N] [--mapping FILE] [--apply] [--replace]。
支持旧chapter/modules/requirements、testpoints、用例对象数组和本版标准JSON。旧索引目录只有摘要无详细规则时拒绝，不脑补章节。表格读取UTF-8 CSV/TSV和.xlsx；旧.xls/.xlsm请先在原应用导出数据副本，宏及复杂Excel对象不是测试数据格式的一部分。

mapping是JSON对象：

```json
{
  "fields": {"用例编号": "case_id", "操作步骤": 5},
  "defaults": {"是否可自动化": "待定", "自动化建议": "仅api自动化可用", "自动化建议明细": "理由：按本批次确认的规则填写。\n已有函数/资源：按项目已导入框架填写", "场景类型": "异常", "设计方法": ["错误推测"], "验收标准": ["AC_PRO_001_01"]},
  "ids": {"REQ": {"REQ-CH3-001": "REQ_PRO_001"}, "AC": {}, "TP": {}, "TC": {"OLD-001": "TC_PRO_001"}},
  "module": "商品管理", "code": "PRO", "images": {}
}
```

fields为标准字段→原表头或1起列号；重复/歧义表头需明确列号。defaults只能提供已确认、适用于整个导入批次的缺值；不同记录的规则不共用一个默认AC/预期，请拆批或修改逐行草稿。缺失数据输出missing_fields；不填“无”骗过校验。
ids按类别映射旧ID→新ID，imports/编号登记.json记住稳定编号，不能把相同旧ID重新指向另一项。images仅授权本地旧路径→真实本地图，不访问URL，复制后按REQ图片登记规范保留。

输出imports/唯一批次/{标准草稿JSON,导入报告.md,编号映射.json,input/原件及底稿}。--apply在全部结构/来源/关联校验通过后写标准文件；--replace先把明确目标与相关派生产物放该批次备份，REQ/TP变化会把下游旧设计移出正式位置，不能继续当当前有效用例。用例替换不清除未变化的测试点评审。
这些文件只是设计资料；原执行结果保留，历史结果不能当新版已经执行。formal应用不替代逐点评审和覆盖充分性判断。

## v1.3执行准备与评审迁移

旧用例自由文本能力不自动变成已核验能力；能力编号需在当前项目核对，准备状态缺字段保留未核验。旧无reviewed_source评审只作历史资料，不给既有结论补签。案例导入替换时，评审结果、通过覆盖矩阵及执行准备派生产物同时归档，需基于当前资料重新生成。
