# Excel contract v1.2

## 命令

以技能目录为基准定位modules/excel/scripts/excel_tool.py，使用工作区提供的Python。路径可含空格，命令参数用shell安全引号。

```text
excel_tool.py inspect --input 原表.xlsx --report 检查结果
excel_tool.py analyze --input 原表.xlsx --sheet 数据 --range A1:D20 --report 分析结果
excel_tool.py create --spec create-spec.json --output 新表.xlsx --report 操作结果
excel_tool.py edit --input 模板.xlsx --spec edit-spec.json --output 更新副本.xlsx --report 操作结果
excel_tool.py recalculate --input 原表.xlsx --output 重算副本.xlsx --spec checks.json
excel_tool.py import-csv --input 数据.csv --spec csv-spec.json --output 数据.xlsx
excel_tool.py export-csv --input 数据.xlsx --sheet 数据 --output 数据.tsv
```

`--report`生成JSON和Markdown。inspect/analyze不重算，以缓存读公式，报告缓存缺失；要实际更新公式须recalculate。
CSV/TSV读UTF-8/UTF-8 BOM，导出UTF-8 BOM。导出只保存所选sheet/范围的当前值，CSV不保留公式、样式或其他sheet。多sheet必须指定--sheet。

## 创建spec

可直接用examples/create-spec.json运行。每个sheet的values/formulas/formats为显式范围块；data尺寸必须匹配有界A1范围。跨sheet公式先创建所有sheet。

```json
{
  "sheets": [{
    "name": "数据",
    "values": [{"range": "A1:C3", "data": [["项目","数量","单价"],["A",2,10],["B",3,20]]}],
    "formulas": [{"range":"D2:D3","data":[["=B2*C2"],["=B3*C3"]]}],
    "formats": [{"range":"C2:D3","format":{"numberFormat":"\"¥\"#,##0.00"}}],
    "column_widths":[{"range":"A1:D3","width":18}],
    "freeze_rows":1,
    "tables":[{"range":"A1:C3","name":"InputData","style":"TableStyleMedium2"}],
    "charts":[{"type":"bar","range":"A1:B3","title":"各项目数量","position":["F2","L15"]}]
  }],
  "checks":[{"sheet":"数据","cell":"D2","value":20}]
}
```

values里的数字、布尔、null分别保留数值、布尔、缺失。百分比使用0.15并配0.0%。日期用`{"type":"date","value":"2026-10-06"}`或datetime ISO对象，写成真正日期并给格式。以=开头字符串是文本；仅formulas/set_formulas执行公式。
新建默认Arial 10、隐藏网格线；仅格式使用范围，不给整sheet着色或加密集边框。标题/表头/合计/数据区域由模型依据任务设计。派生数值用公式，不写固定计算结果。
checks指定独立期望值，允许数值tolerance；不匹配就不交付文件。默认公式错误/缺失缓存拒绝导出；明确需要保留错误审计副本时用--allow-formula-errors，报告仍列出错误。

## 局部编辑spec

```json
{
  "operations":[
    {"op":"set_values","sheet":"数据","range":"B2","data":[[5]]},
    {"op":"set_formulas","sheet":"数据","range":"D2","data":[["=B2*C2"]]},
    {"op":"format","sheet":"数据","range":"D2:D3","format":{"numberFormat":"#,##0.00"}}
  ],
  "checks":[{"sheet":"数据","cell":"D2","value":50}]
}
```

支持set_values、set_formulas、format、append_rows、add_sheet、add_table、add_chart。append_rows需明确start_row/start_column/data；已有内容拒绝追加覆盖。不插入/删除原表物理行列，不替用户猜测尾行。表格追加、图表扩展属于相关变更，必须明确请求并检查绑定，不承诺自动无限扩容。
add_sheet的sheet对象同创建；add_table用table对象；add_chart用chart对象。format允许fill、font、numberFormat、borders、horizontalAlignment、verticalAlignment、wrapText、columnWidth、rowHeight。
局部编辑保留未请求内容：保存后对比值、公式、样式、工作表顺序、冻结、筛选、表格、图表、常见行列/打印设置。失败时不给出“成功”文件。模板中任意复杂特性都能保留的保证不在本版范围。

## 图表

支持bar、line、area、pie、doughnut、scatter、radar；chart.range为连续源，ranges为类别列+数据列的等高范围列表。图表必须给标题和两个位置单元格，放在空白区域，不覆盖源表。保存检查数据/类别引用，形成真实Excel图表而非图片。可用number_format设置数值轴，colors设置系列颜色，折线会设置导出线条颜色。现代原生图（瀑布/树图/地图等）当前不交付。

## CSV显式类型

```json
{"csv":{"sheet_name":"数据","header":true,"columns":[{"index":2,"type":"number"},{"index":3,"type":"percentage"},{"index":4,"type":"date"}]}}
```

index从1开始。类型text/number/percentage/date/boolean。无类型时全部文本，避免商品编号前导0丢失。percentage使用小数份额，脚本不把"15%"猜成0.15。空白保留缺失。boolean只接true/false。数字/日期解析失败指出原行列，不改成0。

## 已知边界和保护

只作者.xlsx，拒绝.xls/.xlsm/.xlsb，不接受改扩展名绕过VBA检测。宏、透视、切片器、外链、数据模型、控件、嵌入对象、查询连接、数字签名、扩展特性、数组/What-If公式、保护、图片/形状/连接线/组合绘图、富文本、迭代计算、1904日期系统、旧VML/线程批注等拒绝编辑/重算。只读inspect可说明风险，原文件不改。
原图表保真比较采用完整可读图表结构（重算缓存除外）；工具重序列化改变未请求设置时会拒绝，而不会假称无损支持。此版只支持原模板简单连续且带标题的类别/数据列图表重绑；非相邻、无标题和复杂原图拒绝重算/编辑。新建图表可用非相邻ranges。
当前Artifact Tool导出的图表范围公式完整，但图表缓存可能为空。作者预览按源数据绘图，保存后核对范围绑定与当前源值。v1.2本机样例已由bundled LibreOffice实际读取并绘图，柱状图随数量2→5正确变化，汇总80→110；原xlsx未写回。该运行环境中文字体不足，中文标题/标签由Artifact预览核对。Microsoft Excel及其他版本未验证，不能把空缓存声称为已填充或所有客户端已验证。
每次范围上限250,000格，不截取后冒充完整分析。新公式禁用网络/高级数组函数，普通SUM/IF/乘除/COUNTIFS/SUMIFS等按实际计算核验。未计算成功的公式不以缓存占位伪装通过。
