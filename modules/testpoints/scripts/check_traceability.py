#!/usr/bin/env python3
"""Check approved testpoints against model-authored detailed cases."""

from __future__ import annotations

import argparse
from pathlib import Path

from point_common import (ValidationError, cli_error, fingerprints, load_context,
                          load_current_review, load_json, markdown_text,
                          protect_outputs, unique_list, validate_cases,
                          write_json, write_text)


def traceability_data(spec: dict, points: list[dict], cases: list[dict], review: dict) -> dict:
    point_map = {point["测试点编号"]: point for point in points}
    reviews = {item["测试点编号"]: item for item in review["reviews"]}
    approved = {point_id for point_id, item in reviews.items() if item["结论"] == "通过"}
    linked, errors, case_links = set(), [], []
    for case in cases:
        case_id = case["用例编号"]
        try:
            point_ids = unique_list(case.get("关联测试点"), f"{case_id}/关联测试点")
        except ValidationError as exc:
            errors.append(str(exc))
            case_links.append({"用例编号": case_id, "关联测试点": [], "status": "failed"})
            continue
        case_errors = []
        for point_id in point_ids:
            if point_id not in point_map:
                case_errors.append(f"{case_id} 关联了未知测试点：{point_id}。")
                continue
            if point_id not in approved:
                case_errors.append(f"{case_id} 关联测试点 {point_id} 尚未通过评审（{reviews[point_id]['结论']}）。")
                continue
            point = point_map[point_id]
            missing_reqs = set(point["关联需求"]) - set(case["关联需求"])
            missing_acs = set(point["验收标准"]) - set(case["验收标准"])
            if missing_reqs or missing_acs:
                detail = []
                if missing_reqs:
                    detail.append("REQ：" + "、".join(sorted(missing_reqs)))
                if missing_acs:
                    detail.append("AC：" + "、".join(sorted(missing_acs)))
                case_errors.append(f"{case_id} 未包含 {point_id} 的全部需求/验收标准关联（{'；'.join(detail)}）。")
            else:
                linked.add(point_id)
        errors.extend(case_errors)
        case_links.append({"用例编号": case_id, "关联测试点": point_ids, "status": "failed" if case_errors else "passed"})
    missing = sorted(approved - linked)
    if missing:
        errors.append("通过评审的测试点尚无有效用例覆盖：" + "、".join(missing) + "。")
    if not approved:
        errors.append("当前没有通过评审的测试点，不能完成正式关联验收。")
    excluded = [{"测试点编号": point_id, "结论": item["结论"], "原因": item["原因"]}
                for point_id, item in reviews.items() if point_id not in approved]
    passed = not errors
    return {"schema_version": "1.0", "module": spec["module"], "code": spec["code"],
            "scope": "当前通过评审的测试点范围", "status": "passed" if passed else "failed",
            "point_count": len(points), "approved_point_count": len(approved), "linked_point_count": len(linked),
            "case_count": len(cases), "missing_approved_points": missing, "excluded_points": excluded,
            "excluded_requirements": review["coverage"]["excluded"], "case_links": case_links, "errors": errors,
            "full_scope_complete": passed and bool(review["summary"]["ready"]) and not excluded}


def render_traceability(data: dict) -> str:
    lines = [f"# {data['module']}测试点用例关联报告", "", f"范围：{data['scope']}。", "",
             f"全部测试点：{data['point_count']}；通过评审：{data['approved_point_count']}；有有效用例关联：{data['linked_point_count']}；用例：{data['case_count']}。", "",
             "通过点范围关联检查：" + ("通过。" if data["status"] == "passed" else "未通过。"),
             "全范围设计完成：" + ("本次评审的已确认范围全部通过且关联完整。" if data["full_scope_complete"] else "尚不能宣称全范围完成，请查看未通过点评审、覆盖率和场景/方法检查。"), "",
             "该检查只核对TP→TC以及REQ/AC映射，不替代详细用例评审，也没有执行测试。", "",
             "## 关联问题", ""]
    lines.extend(["- " + item for item in data["errors"]] or ["无。"])
    lines.extend(["", "## 用例关联", "", "| 用例 | 关联测试点 | 检查结果 |", "|---|---|---|"])
    lines.extend(f"| {markdown_text(item['用例编号'])} | {markdown_text('、'.join(item['关联测试点']) or '无')} | {item['status']} |"
                 for item in data["case_links"])
    lines.extend(["", "## 未通过测试点（不属于本次通过点范围）", ""])
    lines.extend([f"- {item['测试点编号']}：{item['结论']}；{item['原因']}" for item in data["excluded_points"]] or ["无。"])
    for status, label in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        excluded = data["excluded_requirements"][status]
        lines.extend(["", f"{label}（{len(excluded)}条，不属于已确认需求范围）：", ""])
        lines.extend([f"- {item['id']}：{item['reason']}" for item in excluded] or ["无。"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查每个用例与当前通过测试点的REQ/AC关联，以及通过点用例覆盖。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--input", type=Path, help="测试点数据；默认测试点数据.json。")
    parser.add_argument("--cases", type=Path, help="用例数据；默认用例数据.json。")
    parser.add_argument("--review", type=Path, help="测试点评审结果；默认自动探测测试点评审结果.json。")
    args = parser.parse_args()
    module_dir = args.module_dir.resolve()
    report_path = module_dir / "测试点用例关联报告.md"
    data_path = module_dir / "测试点用例关联数据.json"
    safe_to_report = False
    points = []
    try:
        cases_path = args.cases or module_dir / "用例数据.json"
        review_path = args.review or module_dir / "测试点评审结果.json"
        protect_outputs(module_dir, [report_path, data_path], args.input,
                        extra_inputs=(cases_path, review_path))
        safe_to_report = True
        spec, _, points = load_context(module_dir, args.input)
        review = load_current_review(module_dir, args.input, args.review)
        if review is None:
            raise ValidationError("尚未生成测试点评审结果，请先逐点评审并生成当前结果，再做正式关联验收。")
        cases = validate_cases(load_json(cases_path), spec, module_dir=module_dir)
        result = traceability_data(spec, points, cases, review)
        result["reviewed_source"] = fingerprints(module_dir, args.input)
        write_json(data_path, result)
        write_text(report_path, render_traceability(result))
        if result["status"] != "passed":
            print("已生成测试点用例关联报告；当前关联检查未通过。")
            return 1
        scope_note = "已确认范围完整" if result["full_scope_complete"] else "仍有全范围待处理事项"
        print(f"通过点范围TP→TC关联检查通过；{scope_note}。该结果不替代用例评审或实际执行。")
        return 0
    except (ValidationError, OSError) as exc:
        if safe_to_report:
            try:
                failure = {"schema_version": "1.0", "status": "failed", "scope": "未完成正式关联验收",
                           "errors": [str(exc)], "full_scope_complete": False}
                write_json(data_path, failure)
                point_ids = "、".join(point["测试点编号"] for point in points) or "输入未完成校验"
                write_text(report_path, "# 测试点用例关联报告\n\n正式关联验收未完成。\n\n原因：" + str(exc)
                           + "\n\n当前输入测试点：" + point_ids
                           + "。未能核验当前完整评审，不能认定上述测试点已通过。\n")
            except OSError:
                pass  # Preserve the original validation error if publishing also failed.
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
