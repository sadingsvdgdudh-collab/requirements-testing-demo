# 需求到测试 · v1.6.0单技能完整包

> **版本号只有一处权威来源：`SKILL.md` front-matter 里的 `version`。**
> 本文件标题与本节的版本号只作展示，改版本时以 SKILL.md 为准，并用 `tools/检查版本一致.py` 校验。

仍只有一个skill、一个入口：`$requirements-testing`。不要把modules拆开安装。v1.1的需求规范、REQ清单、独立测试点及评审、XMind、详细Excel用例和用例评审全部保留。

## v1.6.0本版新增

把「自动化建议」与「框架能力清单」的对接从"靠人读"补成**机械校验**。此前只核对"被引用的能力是否真实存在"，这版再核对两件：

1. **通道一致性**。清单升级到 `schema_version: "1.1"`，每条能力必须声明 `channel`（`api` / `ui` / `both`），于是 `UI自动化可用`、`UI自动化可做，需采集新资产`、`仅api自动化可用`、`双通道均可用` 不再只看"引用了几个函数"，而是核对**引用的能力属于哪条通道**：仅 api 却只引 UI 函数、双通道却只有单侧证据，都会被拒并列出当前证据的通道。清单为 1.0（无通道信息）时该组规则不生效——通道未知不等于通道不符，不猜。
2. **反向证据**。声称`当前框架能力不足`时，`缺失能力：`里的每一项会与清单中已核对的能力比对（保守整词匹配，去掉括号参数）：清单里明明存在同名能力（含 `buy(player, item, qty)` 这种带参数写法）就报错，要求改判或说明现有能力为何不适用。"只说缺什么"曾是一条最省事的逃生通道——少设计、少测，而这版把它关上了。

配套：框架侧新增 `scripts/capabilities_manifest.py`，直接从 `src/gameqa/*.py` 与 `assets/**/*.png` 生成符合本契约的 JSON（`id`/`file`/`sha256`/`symbol`/`channel` 全自动，能力集合未变时保留 `checked_at`，支持 `--check`）。清单从"人工转录"变为"生成产物"，手工漂移不再可能。

业务契约不变：13＋1 列、JSON `schema_version: "1.0"`、八项取值与既有评审绑定规则都没动；1.0 清单继续可用。新增测试 13 项（`tests/test_manifest_channels.py`）。

## v1.5.1本版修复

按一次只读代码审计逐条修的 14 项。全部是小改动：业务契约、13＋1 列与 JSON schema_version 都不变，只有崩溃路径、两条 Excel 写入路径的口径和若干文档措辞发生变化。

2026-10-10 追加（仅文档）：版本号收敛到 `SKILL.md` 一处，README 与 `验证结果.md` 对齐并加上机器校验；功能与契约未变。

1. **半写入**：重解析在 `解析.md` 冲突被拒时不再先把新图落进 `images/`。此前会留下不在 `解析来源.json` 登记表内的图片，让该模块的 `validate_spec` 永久失败，只能手工删图。
2. **外部工作簿不再崩栈**：`read_xlsx` 把共享字符串越界（`IndexError`）纳入捕获，越界引用报"无法读取用例Excel"而不是 traceback。
3. **手改 JSON 不再崩栈**：`REQ.source` 被写成字符串时报"source 必须为对象"并指出哪条 REQ；此前 `change_common.source_evidence`／`snapshot.py` 与 `review_context.py` 会抛 `AttributeError`。
4. **可读性检查不再崩栈**：用例缺 `前置条件`、`预期结果`、`用例名称` 时进告警清单，而不是 `KeyError`。
5. **两条 Excel 写入路径口径统一**：Python 回退的宽度按"码点 > 255 记 2"（与 `xlsx_io.mjs` 同），`=` 开头的用户文本逐字落盘、不带撇号、无公式节点。此前同一份用例在两条路径上"文字过长拒绝导出"结论不同。
6. **覆盖率门禁不再被存量评审卡死**：需求全部作废/阻塞时按契约输出"不可设计"并生成报告，而不是报"用例评审已过期"。
7. **旧编号映射对 REQ/测试点生效**：旧编号自带标点时整串优先于按分隔符拆分，与 AC 侧已有的逃生通道一致。
8. 数据契约与 SKILL.md 补齐 `当前框架能力不足`／`待核对框架能力` 的明细前缀（`理由：`/`缺失能力：`/`补齐办法：`、`理由：`/`待核对能力：`/`核对办法：`），不再靠试错猜关键字。
9. 通用Excel说明写明：工作区缺 `@oai/artifact-tool` 时报告该能力不可用并停止，不安装依赖、也不改用用例模块的回退写入器。
10. 交付清单区分阶段：`用例覆盖矩阵.md` 标注为评审发布后生成，设计阶段不再要求一份当时不可能存在的文件。
11. README 复验节改成一条 `python3 -m unittest discover -s tests`（此前写 `pytest tests -q`，且漏列最新门禁）；`验证结果.md` 清掉陈旧计数与设备绝对路径。
12. 模块级 `module_preconditions`/`evidence_setup` 变化进入变更影响报告，并把全部用例标为 `needs_evidence_check`；此前一次真实的公共段变更被报告成"无差异"。
13. 同级目录里半写坏/旧格式的 `REQ清单.json` 只影响它自己，不再锁住当前模块的校验、发布与导入。
14. 示例产物去掉契约已禁止的字母等级，改为"结构自检得分（内部定位用，非质量评分）"。

## v1.5本版新增

1. **补上"只有工作簿"时的编号门禁**。此前编号存在性只在 JSON 侧把关（`validate_cases` 拦不存在的 AC 与需求、`check_traceability` 拦不存在的测试点），而这两道校验都要求模块里有 `用例数据.json`。别人递来一个只有 xlsx 的成品表时没人核对编号，覆盖率就按"引用了几条"虚高——实测三份候选里有一份引用了 16 个不存在的 AC 编号、32 个不存在的测试点编号，仍显示 49/49 覆盖。
   - 新增 `check_workbook_links.py`：直接读 13＋1 列工作簿（或 `用例数据.json`）与 `追溯` 派生表，逐条核对 REQ/AC/测试点的存在性与归属，写 `编号校验报告.md`/`编号校验数据.json`；**发现编号问题时以非零退出码结束**。
   - 新增 `load_link_targets()`、`read_trace_sheet()`、`audit_case_links()`（`qa_cases.py`）作为共用底座。
2. **`compare_designs.py` 不再"只数引用"**：候选工作簿的 AC/测试点改为从 `追溯` 派生表读取（此前硬编码为空数组），报告首节固定为「关联编号门禁」，给出编号不存在的数量与**编号有效的 AC 覆盖**；候选存在假编号时默认拒绝比较，需显式 `--allow-link-issues` 才继续，并在报告中标注该豁免。门禁只核对编号存在与归属，语义错挂仍需逐条独立核对。
3. **`追溯` 派生表反向可读**：缺少该表时 AC/测试点按"未登记"处理（不推断成已登记），报告会显式标注"未登记不等于通过"。

## v1.4本版新增

1. **工作簿增加 `追溯` 派生表**（固定第 2 张）：逐条列出关联需求、验收标准、场景类型、设计方法与关联测试点。这些字段按契约不写进前 13 列，此前只交付工作簿时收件人无法区分"未登记"与"登记为空"，会把已挂 AC/测试点的设计误判成没有关联。该表只读派生，不写回 JSON，第 14 列 `AI评审` 位置不变。
2. **覆盖率报告增加场景类型与设计方法分布**，单列用例数为 0 的项，并转录评审的 `scope_checks`/`method_checks` 适用性结论。REQ/AC 双 100% 不再掩盖"整类场景没有用例"。该提示只告警，不改变关联覆盖门禁；也不进入 `coverage_data()` 的返回值——那个结构被测试点评审结果逐字复核，加字段会让既有评审被判过期。
3. **明确跨 REQ 与跨模块的关联写法**：模块内多 REQ 可同时关联且 AC 必须分别归属；跨模块协作不挂对端 REQ 编号，改为置 `场景类型=端到端` 并写清上下游观察点。此前没有任何规则规定该挂一条还是多条，各模块自行其是。
4. **新增 `compare_designs.py`**：比较同一模块的多份候选用例设计，支持 `用例数据.json` 与 13＋1 列工作簿，输出 `设计对比数据.json` 与 `设计对比报告.md`，不改动输入。元数据深度不一时同时给「口径 A（优先声明字段）」与「口径 B（全组统一启发式）」；启发式结果标注为估算。对比结果不评审单份设计能否发布。

## v1.3本版新增

1. 自动化建议增加“当前框架能力不足”和“待核对框架能力”；仍保留13列用例与14列评审副本。
2. 结构化核对实际函数、资产、文件摘要，不执行框架；旧自由文本能力保留未核验。
3. 新评审绑定需求、设计和真实证据，旧无指纹结论不能直接发布。
4. 同时展示全部与设计通过用例的关联覆盖，生成逐REQ/AC矩阵。
5. 按五类汇总准备事项，区分设计通过、人工/自动化就绪与执行记录。

使用与迁移见[能力证据与评审契约](modules/testcase-builder/references/automation-and-review.md)。业务JSON格式版本仍为1.0；未将本次Stendhal特殊规则写成通用要求。

## v1.2能力（保留）

| 能力 | 可以交付什么 | 需要注意什么 |
| --- | --- | --- |
| Word/PDF/截图解析 | 原件副本、文字及表格底稿、图片、真实来源定位、解析报告 | OCR与复杂结构必须对照原件核对，不猜文字 |
| 旧资料导入 | 旧REQ/测试点/用例JSON、CSV/TSV/XLSX转成标准资料，保留编号映射 | 缺字段先给草稿，不自动补假规则或挂全AC |
| 需求变更与用例更新 | 快照、差异及影响报告、待修订用例、校验发布及过期资料归档 | 模型依据新版规则重设计；旧执行结果不是新版结果 |
| 通用Excel | 多工作表、模板局部编辑、公式重算、统计、CSV/TSV、可编辑图表 | 宏、透视等不支持特性会拒绝编辑，不悄悄丢失 |

## 本机改动（相对发布包 v1.2）

> 本节保留 v1.2 发布后的历史改动记录；**枚举取值、列契约以 [数据契约](modules/req-processor/references/data-contract.md) 与 `qa_cases.py` 为准**（现行为八值枚举，本节第 1 条括号内为当时的取值状态）。

本机在发布包 v1.2 基础上做了两处改动。功能向后兼容，但**列契约有变化**：

1. **用例表新增两列**。在 `是否可自动化` 之后插入：

   | 列 | 内容 |
   | --- | --- |
   | `自动化建议` | 枚举：`UI自动化可用` / `UI自动化可做，需采集新资产` / `仅api自动化可用` / `双通道均可用` / `不建议自动化` / `本项目尚无自动化框架` / `当前框架能力不足` / `待核对框架能力`（v1.2 发布后当时只到第 6 个取值；v1.3 追加后两个，现行为八值） |
   | `自动化建议明细` | 判定理由；`已有函数/资源`（依据项目已导入的自动化框架）；取“需采集新资产”时另附需采集的资产及其存放路径；取 `当前框架能力不足` 时写明缺失能力与补齐办法；取 `待核对框架能力` 时写明待核对能力与核对办法 |

   > `本项目尚无自动化框架` 是为「项目还没引入框架」补的第 6 个取值。实测过：没有它时，如实写「不适用」「无」会被枚举校验直接拒绝，只能被迫谎称「仅api自动化可用 + 已有函数/资源：无」。该取值只要求明细含 `理由：`，可配 `是` 或 `待定`；断言主观的仍取 `不建议自动化`。

   用例表由 11 列变为 **13 列**，评审副本的 `AI评审` 由第 12 列顺延为**第 14 列**。校验器强制 `是否可自动化=否` 与 `自动化建议=不建议自动化` 同时出现，反之亦然；其余取值必须给出可自动化通道。

2. **修复 stdin 分块编码缺陷**。`modules/testcase-builder/scripts/xlsx_io.mjs` 与 `modules/excel/scripts/excel_tool.mjs` 原先以 `for await (const chunk of process.stdin) input += chunk;` 读取请求，未调用 `setEncoding`；chunk 为 Buffer 时逐块解码，跨越 chunk 边界的多字节汉字会被写成 `U+FFFD`，造成中文单元格静默损坏（且随内容长度随机出现）。现改为整块拼接后一次性按 UTF-8 解码。

3. **调整评审输出的定位**：去掉字母等级（A/B/C/D），报告与控制台顺序改为「关联覆盖门禁 → 阻塞项 → 结构自检得分」，并把得分明确标注为**内部定位用、非质量评分**。原因：字母等级最容易被当成质量结论，而该得分对内容质量几乎不敏感（关联由作者自己挂、结论由评审者自己下），只反映交付结构是否齐全。原「评分占头条、免责声明在第 4 段」的重要性顺序是反的。

4. **新增两项工程化检查**（补在技能自检里，随 `python3 -m unittest discover -s tests` 一起跑）：

   - **文档与代码列契约一致性**（`tests/test_workflow.py::test_documented_column_contract_matches_code`）：断言用例表列数、每一列的名称、全部自动化建议取值及其顺序、评审列号都与 `qa_cases.py` 一致；并扫描 `modules/**.md`、`SKILL.md` 与 `README.md`，禁止残留旧列数写法。改列而忘记同步文档时会直接失败。
   - **自动化建议明细的段落格式**（`qa_cases.py` 的 `ADVICE_DETAIL_SECTIONS`）：按取值强制明细必须包含相应段落前缀——`UI自动化可用`/`仅api自动化可用`/`双通道均可用` 要求 `理由：` 与 `已有函数/资源：`；`UI自动化可做，需采集新资产` 另要求 `需采集新资产：`；`不建议自动化` 要求 `理由：` 与 `判定依据：`。避免自由文本退化成无法复核的一句话。

已同步更新：`example/` 示例数据与两份示例工作簿、各模块说明与数据契约（`data-contract.md`、`case-template.md`、`review-rules.md`、`instructions.md`、`import-contract.md`）、变更管理器的可变更字段集合、导入模块的字段别名，以及技能自检用例。

## 下载与使用

解压后保留完整requirements-testing文件夹，包括根SKILL.md、agents、modules、tests与示例。放入支持的用户级或项目级skill目录后调用。本次是下载包，未改变设备上的既有技能或旧发布包。

可以直接说：

> 使用 $requirements-testing 解析这份游戏策划案，先指出待确认规则，再生成测试点、XMind和详细Excel用例。

> 使用 $requirements-testing 导入这些旧Excel用例，保留原编号与执行记录，列出还缺哪些信息。

> 使用 $requirements-testing 对比新旧需求，更新受影响用例，保留历史版本。

> 使用 $requirements-testing 修改这个Excel模板里的指定数据，并更新公式和图表。

技能是指导模型和处理文件的工具包，不会自动连接游戏、运行测试，也不能保证含糊策划案产生完全正确的预期。

## 自检工具（tools/）

```sh
python3 tools/检查版本一致.py     # 版本号只有一处权威来源：SKILL.md 的 version
python3 tools/检查依赖.py         # 本机有／缺哪些依赖，缺失会影响什么（--strict 供 CI）
python3 tools/打包skill.py .      # 打成 UTF-8 安全的 zip（不要用 macOS 的 zip 命令）
python3 tools/修复zip编码.py <zip>  # 修已有 zip 里的中文名乱码
python3 tools/同步skill.py        # 把本技能同步到其它安装位置（默认 dry-run）
python3 tools/检查能力清单对接.py --framework <框架根> --scan <项目根>
                                  # 跨框架与技能核对：清单是否与代码一致、
                                  # 每条用例的自动化建议与所引能力的通道是否对得上
```

## 许可

本仓库以 [MIT](LICENSE) 发布。不夹带第三方代码或二进制依赖，仅通过依赖声明使用第三方库；可选外部能力（Node 与 `@oai/artifact-tool`、Apple Vision / Tesseract OCR）不随仓库分发。

## 开发方式与 AI 参与

这是一个**个人作品集项目**，采用 **AI 结对**方式开发。为免歧义，说明各自负责什么：

| 由我（作者）负责 | 由 AI 辅助产出 |
|---|---|
| 选题、范围与取舍；把"需求→测试点→用例"的流程定成可执行阶段 | 各模块脚本与测试的具体实现 |
| 数据契约、编号规则、13＋1 列约定、8 值枚举、draft 门禁等**规则的定义** | 规则的具体校验代码与报错文案 |
| 变更流程（快照→影响分析→重设计→校验→重评）与授权边界的设计 | 变更流程的落地实现 |
| 对产出的验证：跑复验、核对失败原因、判断是环境问题还是逻辑问题 | 文档初稿与示例产物 |

**结论**：设计决策、规则与验收标准由人负责；实现大量借助 AI，并逐项验证。
README 与 `验证结果.md` 里记录的每个数字都可用 `python3 -m unittest discover -s tests` 复核。
本仓库不夹带第三方代码或二进制依赖，仅通过依赖声明使用第三方库；可选外部能力（Node 与 `@oai/artifact-tool`、Apple Vision / Tesseract OCR）不随仓库分发。

## 运行环境

**依赖清单见 [依赖说明.md](依赖说明.md)，Python 依赖见 [requirements.txt](requirements.txt)。**
一句话版：**缺哪个依赖只会让对应能力/测试不可用，技能不会自动安装也不会假装可用** ——

| 缺什么 | 后果（实测） |
|---|---|
| openpyxl | 2 个测试文件无法导入、整轮少收 25 项；`Ran 222 → failures=3, errors=8` |
| Node（或 `@oai/artifact-tool`） | Excel 测试整组被 `setUpClass` 拦住；用例工作簿自动回退 `xlsx_fallback.py`，通用 Excel 模块报能力不可用 |
| pdfplumber | PDF 解析报能力不可用并停止，DOCX 路径不受影响 |
| pypdf | 1 项 ERROR（`test_password_pdf_reports_clear_failure`） |
| 原生 OCR | 3 项 skipped（需 `QA_TEST_OCR=1` 显式开启） |

环境齐全时基线为 `Ran 247 → errors=2, skipped=4`；那 2 个 error 正是"缺 pypdf"与"缺 Node 运行时"，见依赖说明。

Python 3.10+；DOCX文字解析及既有确定性检查使用标准库。PDF解析需pdfplumber（及其Pillow/pypdfium2渲染依赖）；旧XLSX导入/只读检查需openpyxl。macOS OCR需Vision与Swift/xcrun，其他系统需Tesseract和chi_sim+eng语言包；缺能力明确报告，可由用户选择off后人工视觉转录。
Excel作者统一使用Node与@oai/artifact-tool。可使用当前工作区提供的依赖；QA_NODE_EXECUTABLE和QA_NODE_MODULES指向实际Node与node_modules。modules/excel/scripts/runtime.json或modules/testcase-builder/scripts/runtime.json可保存本机node_executable/node_modules，环境变量优先。包里不放设备绝对路径、运行时或OCR编译缓存。
没有自动下载外部图片/原件、也没有自动安装依赖。运行环境提供的文档、PDF、表格技能仍用于视觉验收与依赖发现。

`@oai/artifact-tool` 只在部分环境提供。缺少它（或本机没有 Node）时，`render_xlsx.py` 与 `update_review.py` 自动回退到包内 `modules/testcase-builder/scripts/xlsx_fallback.py`：openpyxl 写入，保持同一 13 列契约与第 14 列评审列，读取侧仍是原有的只读解析。回退不承诺 Node 版的视觉细节（条件格式、表格筛选按钮、行高精算），也不改变 `modules/excel/` 的通用 Excel 工具（仍只走 Node）。用 `QA_XLSX_FALLBACK=python` 可显式指定回退、`QA_XLSX_FALLBACK=node` 禁止回退，便于分别验证两条路径；`tests/test_xlsx_fallback.py` 固定回退侧的契约。

## 产物与示例

原件在项目input，当前结果在output/业务模块。imports/保留导入批次，updates/保留更新草稿，history/保留旧快照和过期派生结果。草稿不会当作正式资料，评审结论依然分测试点与用例两层。

- example/：v1.1完整商品管理流程，6REQ/10AC/13TP/10TC；详细用例仍有1项需确认，不伪造全通过。
- example-independent/、example-testpoints-independent/：保留历史独立试跑。
- examples-v12/document-inputs/：真实中文Word、文字PDF、扫描PDF和截图；document-parsed/对应真实解析产物。
- examples-v12/import-example/：旧REQ、旧测试点、9列CSV；先缺字段草稿，后显式映射发布。
- examples-v12/change-example/：名称上限20→30，边界20/21→30/31；编号与历史执行记录保留，旧Excel/XMind归档。
- modules/excel/examples/：多sheet公式/图表及局部更新副本，汇总80→110。

复验命令、原生打开与视觉检查范围见本节与[验证结果.md](验证结果.md)（后者是历史记录，不写测试数）。中文OCR须核对；特殊复杂文档和Excel格式以模块说明的实际支持边界为准。

## 复验

使用含对应依赖的Python，在技能根执行一条命令即可跑完全部 18 个测试文件：

```text
python3 -m unittest discover -s tests
```

测试是 unittest，不是 pytest；`discover` 会自动加载 `tests/` 下所有 `test_*.py`。`tests/run_import_example.py`、`tests/run_change_example.py` 是示例 runner，用 `python3 tests/run_import_example.py --help` 查看参数后再单独跑。

缺对应依赖时只有相关用例失败（`test_document_parser` 需 pypdf/pdfplumber，`test_excel` 与 `--xlsx` 集成用例需 Node 与 `@oai/artifact-tool`），失败信息会说明缺什么；不要为了变绿而跳过或改断言。QA_TEST_OCR=1可开启三项真实本机OCR验收；解析复验另需python-docx生成内部临时检查文件。测试均用独立临时项目，不执行游戏。示例runner别跑在用户唯一当前资料上。Excel作者前遵循当前环境表格操作标记与视觉验收要求。
