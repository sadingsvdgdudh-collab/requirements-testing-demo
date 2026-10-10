#!/usr/bin/env python3
"""Validate model-designed case data and render the 13-column workbook."""

import argparse
from pathlib import Path

from qa_cases import (METRICS_HEADERS, ValidationError, case_matrix, cli_error, load_json,
                      merged_precondition, module_shared_lines, run_xlsx, trace_rows,
                      validate_cases, validate_spec)


def trace_sheet(cases: list[dict], *, draft: bool) -> dict:
    """逐条追溯表：把只存 JSON 的关联字段带进工作簿。

    前 13 列契约不变；本表是 JSON 的派生只读视图，不参与用例表校验，也不写回 JSON。
    """
    return {
        "name": "追溯",
        "rows": [METRICS_HEADERS] + trace_rows(cases, draft=draft),
        "table": "QATraceability",
        "widths": [17, 9, 34, 34, 12, 30, 26],
    }


def extra_sheets(data: dict, cases: list[dict], *, draft: bool) -> list[dict]:
    """公共段说明工作表 + 追溯表。

    追溯表总是生成：只有独立交付 13＋1 列工作簿时，AC、场景类型、设计方法和
    测试点关联才有可读载体；缺了它，收件人无法区分"未登记"与"登记为空"。
    """
    shared = module_shared_lines(data)
    sheets = [trace_sheet(cases, draft=draft)]
    if shared["module_preconditions"]:
        sheets.append({
            "name": "公共前置条件",
            "rows": [["序号", "公共前置条件（全模块适用）"]]
                    + [[str(index), line] for index, line in enumerate(shared["module_preconditions"], 1)],
            "table": "QAModulePreconditions",
            "widths": [8, 110],
        })
    if shared["evidence_setup"]:
        sheets.append({
            "name": "取证要求",
            "rows": [["序号", "取证要求（全模块适用）"]]
                    + [[str(index), line] for index, line in enumerate(shared["evidence_setup"], 1)],
            "table": "QAEvidenceSetup",
            "widths": [8, 110],
        })
    if shared["module_preconditions"] or shared["evidence_setup"]:
        sheets.append({
            "name": "合并前置条件",
            "rows": [["用例编号", "用例名称", "合并前置条件（公共段＋本条独有）"]]
                    + [[case["用例编号"], case["用例名称"], merged_precondition(data, case)] for case in cases],
            "table": "QAMergedPreconditions",
            "widths": [17, 31, 110],
        })
    return sheets


def main() -> int:
    parser = argparse.ArgumentParser(description="从用例数据生成13列Excel；不自动设计业务用例。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--draft", action="store_true", help="明确生成未核验需求关联的草稿，不宣称完整覆盖。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        source = (args.cases or module_dir / "用例数据.json").resolve()
        output = (args.output or module_dir / ("详细用例_草稿.xlsx" if args.draft else "详细用例.xlsx")).resolve()
        spec = None if args.draft else validate_spec(module_dir)
        if spec is not None and not any(req["status"] == "active" for req in spec["requirements"]):
            raise ValidationError("没有active需求，正式用例不可设计。")
        data = load_json(source)
        cases = validate_cases(data, spec, draft=args.draft, module_dir=module_dir)
        payload = {"operation": "create", "rows": case_matrix(cases, draft=args.draft),
                   "sheetName": "草稿用例" if args.draft else "用例"}
        sheets = extra_sheets(data, cases, draft=args.draft)
        payload["sheets"] = sheets
        run_xlsx(payload, output)
        print(f"已生成{'未核验草稿' if args.draft else '正式用例表'}：{output}，{len(cases)}条用例，"
              f"另含 {len(sheets)} 份派生表（追溯＋公共段说明）。"
              + (" 草稿未核验覆盖率。" if args.draft
                 else " 请继续检查REQ和验收标准双关联覆盖率，并把覆盖率报告随表交付。"))
        return 0
    except (ValidationError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())

