# 已有资料导入与兼容

先阅读[导入契约](references/import-contract.md)。按用户指定的资料与模块处理；原资料不改。支持旧需求JSON、旧测试点JSON、用例对象数组及当前规范JSON、CSV/TSV和XLSX。

1. 查看原资料的章节、字段/列名、编号、来源和缺失。多个模块或工作表必须明确范围；别默认只取第一张表冒充全部。
2. 默认导入在imports/批次生成draft=true的标准JSON、导入报告、编号映射及原件副本。先看missing_fields和原格式保留记录，格式转换成功不表示已经可正式测试。
3. 模型结合实际需求补全，或由用户提供明确mapping。缺少status、场景、方法、AC、自动化属性等不能自动填假答案；旧FT/BT/ET测试分类不直接等于六场景分类。
4. REQ导入建立真实旧结构化文本底稿及九节规范；这只能证明来自旧资料，不认证旧资料等于原策划案。模型应核对新项目规则。TP/TC要先有通过验证的REQ。
5. 保留旧→新REQ/AC/TP/TC映射、P3和已有执行记录。AC只有明确ID或相关REQ下唯一完整文本匹配才绑定；不能只根据REQ挂全部AC。
6. 完整资料才使用--apply发布；已有目标须用户授权后--replace并备份。源为draft=true或needs_redesign时不能直接发布，先核对修改候选文件、明确draft=false。发布后重新导出并评审，绝不把导入算作评审通过。

```text
import_material.py --input 旧需求.json --module-dir 项目/output/商品管理 --kind requirements --code PRO
import_material.py --input 旧测试点.json --module-dir 项目/output/商品管理 --kind testpoints --mapping tp-mapping.json --apply
import_material.py --input 旧用例.xlsx --sheet 功能用例 --header-row 2 --module-dir 项目/output/商品管理 --kind cases --mapping case-mapping.json
```

scripts/import_material.py使用Python 3.10+，读取XLSX需openpyxl（只读，不用其作者）。真实旧REQ、TP和9列CSV完整演示见examples-v12/import-example，包含缺字段草稿与显式核对映射。
