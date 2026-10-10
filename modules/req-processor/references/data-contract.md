# 首版数据契约

这是需求整理与用例设计内部模块共用的基础数据定义。格式版本为 `1.0`，JSON 使用 UTF-8。新增测试点数据及评审衔接见 [测试点契约](../../testpoints/references/testpoint-contract.md)。模型根据原文写业务内容，脚本校验和转换，不能从 Markdown 加粗或标题推断业务规则。

## 文件与路径

```text
项目/
  input/                       原件及不可变 Markdown 底稿
  output/
    _公共/拆分清单.json
    _公共/拆分报告.md
    {模块}/解析.md
    {模块}/images/             有附图时创建
    {模块}/需求规范.md
    {模块}/REQ清单.json
    {模块}/用例数据.json
    {模块}/详细用例.xlsx
    {模块}/覆盖率数据.json
    {模块}/需求覆盖率报告.md
    {模块}/评审数据.json
    {模块}/详细用例_已评审.xlsx
    {模块}/评审报告.md
```

拆分清单的路径和 REQ 的 `source.file` 都相对 `output/` 根目录。图片的 `path` 与 `source.images` 相对模块目录。清单 targets 不能越出 output；相对图片路径必须在模块内。底稿原文的行号从 1 开始，范围包含两端。

拆分清单完整定义见 [split-rules.md](split-rules.md)，编号规则见 [numbering-rules.md](numbering-rules.md)。所有脚本用 `--module-dir` 接收模块目录，拆分检查用 `--root` 接收 output 根目录；不假定技能安装位置。

## REQ清单.json

```json
{
  "schema_version": "1.0",
  "module": "商品管理",
  "code": "PRO",
  "requirements": [
    {
      "id": "REQ_PRO_001",
      "title": "名称必填",
      "description": "商品名称不能为空。",
      "kind": "constraint",
      "status": "active",
      "acceptance_criteria": [
        {"id": "AC_PRO_001_01", "text": "名称为空时阻止保存并提示名称必填。"}
      ],
      "source": {
        "file": "../input/新增商品.md",
        "lines": [6, 6],
        "quote": "商品名称不能为空。",
        "images": []
      }
    }
  ],
  "questions": [
    {
      "id": "Q_PRO_001",
      "priority": "P1",
      "status": "open",
      "text": "名称是否允许重复？",
      "affected_requirements": ["REQ_PRO_001"],
      "resolution": ""
    }
  ],
  "images": []
}
```

示例编号、原文和行号按当前材料填写。顶层 `module` 是模块名，`code` 与拆分清单一致。`requirements`、`questions`、`images` 都是数组；无问题或附图时使用空数组。

需求各字段均保留；`kind` 为 `functional`、`constraint`、`nonfunctional` 之一。依赖关系在规范第6节说明，涉及可验证行为时归入相应需求类型。`status` 为 `active`、`blocked` 或 `deprecated`。每个 active 需求至少一个验收标准；blocked、deprecated 可以无 AC，但仍保留描述和来源。所有 REQ 和 AC 编号唯一，AC 属于其父 REQ。

`source.file` 必须存在，`lines` 是真实有效行范围，`quote` 非空且存在于这个范围的原文。对图中规则先保存有页/图定位的转录底稿，再引用底稿行，并在 `source.images` 指向原图；不能用虚拟行号替代证据。

每张图登记为以下对象；磁盘 `images/` 中每个图片文件都须登记且可读取，引用图片也须在此列表中：

```json
{"path": "images/price-rule.png", "description": "价格输入规则截图", "transcription": "价格大于0，最多2位小数。"}
```

转录看不清时明示位置和不确定内容，不填造文字。

待澄清问题使用 `P0`、`P1`、`P2`，状态为 `open` 或 `resolved`；resolved 必须有非空 `resolution`。受影响 REQ 必须存在。P0 open 对应的需求全部设 blocked；`affected_requirements: []` 表示除 deprecated 外整个模块阻塞。P1/P2 不强制阻塞，但用例不能依赖其未确认规则。

`需求规范.md` 有 `## 0.` 至 `## 8.` 九节，每个 REQ 在定义处加粗且仅一次，定义集合与 JSON 完全一致。脚本不生成业务 JSON。

## 用例数据.json

```json
{
  "schema_version": "1.0",
  "module": "商品管理",
  "code": "PRO",
  "cases": [
    {
      "用例编号": "TC_PRO_001",
      "优先级": "P1",
      "用例名称": "名称为空时保存",
      "前置条件": "已登录商品后台并打开新增商品页面。",
      "操作步骤": ["保持商品名称为空。", "输入价格99。", "点击保存。"],
      "测试数据": "名称为空；价格99。",
      "预期结果": ["提示名称必填。", "商品未创建。"],
      "实际结果": "",
      "关联需求": ["REQ_PRO_001"],
      "是否可自动化": "是",
      "自动化建议": "仅api自动化可用",
      "自动化建议明细": "理由：价格为数值规则，可由协议动作直接断言。\n已有函数/资源：api_player.action({\"type\":\"use\"})、observer.inspect() 的 slots/inventory 字段。",
      "自动化脚本": "",
      "验收标准": ["AC_PRO_001_01"],
      "场景类型": "异常",
      "设计方法": ["等价类"]
    }
  ]
}
```

工作簿固定前 13 列：用例编号、优先级、用例名称、前置条件、操作步骤、测试数据、预期结果、实际结果、关联需求、是否可自动化、自动化建议、自动化建议明细、自动化脚本。以上字段必须存在；实际结果、自动化脚本可以为空，其余内容不空。操作步骤与预期结果为非空字符串数组；不把列表编号写进每个字符串。

关联需求与验收标准规范格式为不重复的数组。兼容导入旧版中文分隔字符串时，先规范化成数组再保存。用例仅关联 active REQ，所列 AC 必须属于该用例关联的 REQ；不能跨挂 AC 或把一个 REQ 的所有 AC 自动算作已覆盖。

`优先级` 为 P0/P1/P2/P3；`是否可自动化` 为是/否/待定。P3保留旧项目低风险约定，不因导入自动升级。疑问优先级仍仅P0/P1/P2。没有真正执行时实际结果为空，没有已存在脚本时自动化脚本为空。

前置条件只描述业务状态：一行一条，只写本条独有内容，不重复模块公共段，不写取证手段，不出现“已核实”“已确认”这类审计措辞。**能力是否核对由 `automation_evidence` 表达，准备缺口由 `preparation` 表达，两者都不写进前置条件。** 写法细则见[用例模板](../../testcase-builder/references/case-template.md#字段写法)。

顶层可选 `module_preconditions` 与 `evidence_setup`（均为非空文本数组）：前者登记测试环境与账号约定，后者登记取证方式，每个模块只写一次。渲染时生成「公共前置条件」「取证要求」两份说明工作表，并追加「合并前置条件」列供执行者阅读；**前 13 列的字面内容不因此改变**。字段缺省表示未登记，旧资料保持可读，可读性检查按重复情况提示。

```json
{
  "module_preconditions": ["隔离测试服，各用例独立重置账号状态。"],
  "evidence_setup": ["事件取证：各观察端独立消费事件队列并保存批次。"]
}
```

`自动化建议` 为八值枚举，取值与含义：`UI自动化可用`（框架现成原语与资产即可执行）、`UI自动化可做，需采集新资产`（原语齐备，缺该场景的校准截图）、`仅api自动化可用`（断言落在服务端状态或协议参数，UI 无入口）、`双通道均可用`（协议动作与界面操作都有入口）、`不建议自动化`（断言依赖人工主观判断）、`本项目尚无自动化框架`（项目尚未引入任何自动化框架，无通道可判）、`当前框架能力不足`（已有框架但缺动作、状态准备或观测能力）、`待核对框架能力`（引用和场景适用性尚未核对）。后两项必须搭配`是否可自动化=待定`。仅当 `是否可自动化` 为 `否` 时取 `不建议自动化`，其余取值不得使用它；校验器强制该一致性。**`本项目尚无自动化框架` 用于项目未导入框架的场景**：此时不得为了满足枚举而谎称某条通道可用；一旦项目引入框架，应按自动化能力清单重新判定并改回具体通道。**优先级**：若断言本身依赖人工主观判断，取 `不建议自动化`（该结论与有无框架无关，故 `是否可自动化=否` 时不得改用它值）；只有「框架一旦引入结论就会改变」的情况才取 `本项目尚无自动化框架`。

`自动化建议明细` 为非空文本，必须写明判定理由，并依据项目中已导入的自动化框架列出依据：

- 取值为 `UI自动化可用`、`仅api自动化可用`、`双通道均可用` 时，列出框架中已有的自动化函数与资源（模块/函数名、资产文件路径）。
- 取值为 `UI自动化可做，需采集新资产` 时，除已有函数与资源外，还须列出需要采集的新资产及各自的存放路径。
- 取值为 `不建议自动化` 时，写明不可自动化的具体判断依据。

明细只描述实际核对过的框架能力；未核对的框架能力不得写入。

明细的段落前缀由取值决定，校验器强制：`UI自动化可用`、`仅api自动化可用`、`双通道均可用` 要求同时含 `理由：` 与 `已有函数/资源：`；`UI自动化可做，需采集新资产` 在前两者之外还要求 `需采集新资产：`；`不建议自动化` 要求 `理由：` 与 `判定依据：`；`本项目尚无自动化框架` 只要求 `理由：`；`当前框架能力不足` 要求 `理由：`、`缺失能力：` 与 `补齐办法：`；`待核对框架能力` 要求 `理由：`、`待核对能力：` 与 `核对办法：`。前缀缺失时校验失败，这是为了避免自由文本退化成一句无法复核的空话。

场景类型为正常、备选、异常、边界、非功能、端到端之一。设计方法为场景法、等价类、边界值、错误推测、判定表、状态迁移中的一项或多项。这三个附加字段（验收标准、场景类型、设计方法）保留在 JSON，不加入前 13 列；它们供覆盖与评审检查。工作簿另有派生只读工作表 `追溯`，逐条呈现关联需求、验收标准、场景类型、设计方法与关联测试点，使单独交付工作簿时这些关联仍可读出；该表由 `render_xlsx.py` 从 JSON 生成，不写回 JSON，也不是新的数据来源。

`关联需求` 可以关联多条 active REQ，条件是这些 REQ 都在本模块 REQ 清单内，且所列 AC 分别属于它们。跨模块协作不挂对端模块的 REQ 编号（本模块清单里没有该 REQ，校验会判定为未知需求）：改为只挂本模块 REQ 并置 `场景类型=端到端`，在对应用例的预期结果里写明上游输入与下游可见结果两侧的观察点。多挂一条 REQ 必须有内容支撑，不得只改关联字段。

关联覆盖率分别计算 active REQ 被用例关联的比例、active REQ 所有 AC 被用例关联的比例。两项都为 100% 才通过关联覆盖门禁；这不证明场景充分或软件正确。blocked/deprecated 不进分母，但报告必须列出数量、原因和范围。没有 active 需求时结果为不可设计，不能报告 100%。

覆盖率数据与报告另列 `case_scope_distribution` 与 `case_method_distribution`，逐项给出六类场景与六种方法的实际用例数，并单列用例数为 0 的项。该分布只提示缺口，不判定适用性，也不改变关联覆盖门禁；适用性由评审数据 `scope_checks`/`method_checks` 判定，并转录进同一份覆盖报告。

## 评审数据.json

```json
{
  "schema_version": "1.0",
  "reviews": [
    {"用例编号": "TC_PRO_001", "结论": "通过", "原因": "步骤与预期明确，验证名称必填。", "建议": "无。"}
  ],
  "scope_checks": [
    {"item": "正常", "status": "missing", "evidence": [], "reason": "本示例尚缺正常保存用例。"},
    {"item": "备选", "status": "not_applicable", "evidence": [], "reason": "原文没有备选流。"},
    {"item": "异常", "status": "covered", "evidence": ["TC_PRO_001"], "reason": "名称为空阻止保存。"},
    {"item": "边界", "status": "missing", "evidence": [], "reason": "需根据实际价格规则补边界。"},
    {"item": "非功能", "status": "not_applicable", "evidence": [], "reason": "原文没有规定非功能指标。"},
    {"item": "端到端", "status": "not_applicable", "evidence": [], "reason": "材料范围为单模块。"}
  ],
  "method_checks": [
    {"item": "场景法", "status": "missing", "evidence": [], "reason": "缺正常保存流程。"},
    {"item": "等价类", "status": "covered", "evidence": ["TC_PRO_001"], "reason": "名称为空属于无效类。"},
    {"item": "边界值", "status": "missing", "evidence": [], "reason": "存在数值约束，尚缺边界用例。"},
    {"item": "错误推测", "status": "not_applicable", "evidence": [], "reason": "未识别到有需求依据的额外失效模式。"},
    {"item": "判定表", "status": "not_applicable", "evidence": [], "reason": "没有多条件组合决策。"},
    {"item": "状态迁移", "status": "not_applicable", "evidence": [], "reason": "没有已定义的状态转换规则。"}
  ]
}
```

上述为评审结构示意，适用性要重新依据当前需求判断。每条真实用例恰好一个 reviews 条目。结论为通过/需确认/不通过，原因与建议非空。scope_checks 完整且不重复地列出六个场景类型；method_checks 完整且不重复地列出六个设计方法。

检查状态为 covered/missing/not_applicable。covered 的 evidence 必须指向真实 TC，且 TC 的场景类型或设计方法匹配该项。missing 与 not_applicable 都须解释原因；“没有设计”不是不适用的理由。

写回工作簿只追加第 14 列 `AI评审`（结论＋原因＋建议）。不改变原前 13 列、不修改用例 JSON、不覆盖原工作簿。自动化判定待定时也只给建议。

**结构自检得分（内部定位用，非质量评分）**共 100 分：关联覆盖 45 分（REQ 与 AC 覆盖率较小者 × 45）；用例通过率 30 分（仅通过计入）；适用正常/备选/异常三流覆盖 15 分；适用方法覆盖 10 分。not_applicable 不计该项分母，无适用项该项得满分并注明原因。

**不产出字母等级。** 字母等级最容易被当成质量结论，而该得分对内容质量几乎不敏感——关联是自己挂的、评审结论是自己下的，它只反映交付结构是否齐全。报告与控制台输出顺序固定为「**关联覆盖门禁 → 阻塞项 → 结构自检得分**」；存在 missing、需确认或不通过时必须另报阻塞项，不得凭得分宣称可执行。得分的正当用途只有内部定位（哪一块覆盖弱、该先补什么）。评审不是测试执行，没有运行真实被测系统。

## 命令与失败处理

```text
check_parse.py --md PATH
check_split.py --root PATH
validate_spec.py --module-dir PATH
render_xlsx.py --module-dir PATH [--cases PATH] [--output PATH] [--draft]
check_workbook_links.py --module-dir PATH --input PATH [--out-dir PATH]
check_coverage.py --module-dir PATH
update_review.py --module-dir PATH --review PATH [--input PATH] [--output PATH]
```

render 默认写 `详细用例.xlsx`，`--draft` 默认写 `详细用例_草稿.xlsx`；覆盖检查写 `覆盖率数据.json` 与 `需求覆盖率报告.md`；评审写 `详细用例_已评审.xlsx` 与 `评审报告.md`。派生产物可以重跑，原件和输入用例内容不可覆盖。
`check_workbook_links.py` 写 `编号校验报告.md` 与 `编号校验数据.json`，核对输入（工作簿或 `用例数据.json`）引用的 REQ/AC/测试点编号是否存在且归属成立，发现问题退出 1。它只核对编号，不判断用例内容是否真的在验证该 AC。

成功退出 0，数据校验失败退出 1；依赖或程序错误应显示明确原因，不能无限重试或伪造成功。正式用例入口校验上游规范；用户明确要求直接生成草稿时才用 `--draft`，保留草稿标识并说明无法宣称已核验覆盖率。

## v1.2导入、转录与更新门禁

REQ、测试点或用例顶层draft=true时正式验证器拒绝。不能仅删草稿标记：须补全实际业务内容、来源、AC关联与设计字段，并完成相应评审。
解析模块生成解析来源.json；需要核对的OCR/复杂结构须有绑定原件及底稿SHA的解析核对.json。仍不确定的区域登记影响REQ并blocked；其他已确认规则可继续。校验器检查记录与指纹，不能证明核对者确实看过原件。
旧资料导入默认在imports/批次中生成草稿、映射及报告，--apply只有完整验证才发布，--replace须授权并先备份。变更草稿在updates/批次，受影响条目需resolved及update_resolution，换意图新增编号；执行记录保持不变。发布后历史派生产物归档并重新生成、评审。

Python 3.10+ 用于检查；Excel 写入由包内 `xlsx_io.mjs` 使用 `@oai/artifact-tool` 完成。可用 PATH/正常 Node 模块安装，或用 `QA_NODE_EXECUTABLE`、`QA_NODE_MODULES` 指定当前可用运行时。不要把当前机器路径写进交付数据。

## v1.3能力证据、评审指纹与准备状态

格式版本保持1.0，前13列不变。新增框架能力清单、用例automation_evidence与preparation、评审reviewed_source的完整定义见[框架能力与执行准备](../../testcase-builder/references/automation-and-review.md)。旧自由文本可读但不算能力已核验；无指纹旧评审须重评。新增派生文件为评审结果.json、用例覆盖矩阵.md、执行准备数据.json及执行准备清单.md。

## 用例可读性门禁

`case_readability.py --module-dir 模块目录` 检查三项可复算指标并输出可读性数据.json与可读性报告.md：前置独有信息占比（模块内重复片段不计入独有信息）、前置最长单句、预期结果落点率；同时报出残留的内部标识符与未完成句。阈值定义在 `case_readability.py` 的 `THRESHOLDS`：`unique_ratio_min` 默认 70%、`max_sentence_chars` 默认 60、`expected_anchor_rate_min` 默认 80%。默认只告警（退出码 0），`--strict` 用于新资料门禁。

该检查只判断书写形式，不代表用例设计正确、准备就绪或已执行；与 `automation_evidence`、`preparation` 是三条独立判断线，互不抵消。
