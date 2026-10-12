#!/usr/bin/env python3
"""Publish a version-bound design review in a derived 14-column workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from qa_cases import HEADERS, METHODS, SCOPES, ValidationError, case_matrix, cli_error, coverage_data, load_json, percent, read_xlsx, require_text, run_xlsx, unique_list, validate_cases, validate_spec, write_text, require_final
from review_context import review_fingerprints, validate_review_context, protected_inputs
from case_reporting import (review_coverage, validate_preparation,
                            build_preparation_report, render_preparation_report,
                            render_review_coverage)
from automation_evidence import load_manifest, framework_root


def validate_review(data: dict, cases: list[dict], spec: dict | None = None,
                    module_dir: Path | None = None) -> tuple[dict, list[dict], list[dict]]:
    require_final(data, "评审数据")
    validate_review_context(data, spec, cases, module_dir)
    require_text(data.get('reviewer'), '评审数据/reviewer')
    validate_preparation(cases)
    return validate_review_structure(data, cases)


def validate_review_structure(data: dict, cases: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Shared judgment shape; domain-specific freshness is checked by its caller."""
    require_final(data, "评审数据")
    if data.get("schema_version") != "1.0":
        raise ValidationError("评审数据 schema_version 必须为1.0。")
    case_map = {case["用例编号"]: case for case in cases}
    raw = data.get("reviews")
    if not isinstance(raw, list):
        raise ValidationError("reviews 必须为数组。")
    reviews = {}
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise ValidationError(f"第{index}条评审必须为对象。")
        case_id = require_text(row.get("用例编号"), f"第{index}条评审用例编号")
        if case_id not in case_map:
            raise ValidationError(f"评审引用未知用例：{case_id}。")
        if case_id in reviews:
            raise ValidationError(f"重复评审：{case_id}。")
        if row.get("结论") not in ("通过", "需确认", "不通过"):
            raise ValidationError(f"{case_id} 评审结论必须为通过/需确认/不通过。")
        require_text(row.get("原因"), f"{case_id}/原因")
        require_text(row.get("建议"), f"{case_id}/建议")
        reviews[case_id] = row
    missing = set(case_map) - set(reviews)
    if missing:
        raise ValidationError("每条用例须恰好一条评审，缺少：" + "、".join(sorted(missing)) + "。")

    def checks(key: str, names: list[str], field: str) -> list[dict]:
        rows = data.get(key)
        if not isinstance(rows, list):
            raise ValidationError(f"{key} 必须为数组。")
        by_name = {}
        for index, item in enumerate(rows, 1):
            if not isinstance(item, dict):
                raise ValidationError(f"{key} 第{index}项必须为对象。")
            name = item.get("item")
            if name not in names or name in by_name:
                raise ValidationError(f"{key} 含未知或重复项：{name}。")
            if item.get("status") not in ("covered", "missing", "not_applicable"):
                raise ValidationError(f"{key}/{name} 状态必须为covered/missing/not_applicable。")
            require_text(item.get("reason"), f"{key}/{name}/reason")
            evidence = unique_list(item.get("evidence"), f"{key}/{name}/evidence", allow_empty=True, allow_string=False)
            unknown = set(evidence) - set(case_map)
            if unknown:
                raise ValidationError(f"{key}/{name} 引用了不存在的TC：{'、'.join(sorted(unknown))}。")
            matching = {case_id for case_id, case in case_map.items() if (case[field] == name if field == "场景类型" else name in case[field])}
            if item["status"] == "covered":
                if not evidence:
                    raise ValidationError(f"{key}/{name} covered 必须有真实TC证据。")
                if set(evidence) - matching:
                    raise ValidationError(f"{key}/{name} 证据与用例{field}元数据不匹配。")
            elif item["status"] == "not_applicable":
                if evidence or matching:
                    raise ValidationError(f"{key}/{name} 已有适用TC，不能标记not_applicable。")
            by_name[name] = dict(item, evidence=evidence)
        missing_names = set(names) - set(by_name)
        if missing_names:
            raise ValidationError(f"{key} 必须说明全部六项，缺少：{'、'.join(name for name in names if name in missing_names)}。")
        return [by_name[name] for name in names]

    return reviews, checks("scope_checks", SCOPES, "场景类型"), checks("method_checks", METHODS, "设计方法")


def score_review(coverage: dict, reviews: dict, scopes: list[dict], methods: list[dict]) -> dict:
    req_rate = coverage["requirement_coverage"]["rate"]
    ac_rate = coverage["acceptance_coverage"]["rate"]
    link_score = 45 * min(req_rate or 0, ac_rate or 0)
    counts = {verdict: sum(row["结论"] == verdict for row in reviews.values()) for verdict in ("通过", "需确认", "不通过")}
    pass_score = 30 * counts["通过"] / len(reviews) if reviews else 0
    def component(items: list[dict], weight: int) -> tuple[float, str]:
        applicable = [item for item in items if item["status"] != "not_applicable"]
        if not applicable:
            return float(weight), "无适用项，按约定该项得满分。理由：" + "；".join(f"{item['item']}：{item['reason']}" for item in items)
        covered = sum(item["status"] == "covered" for item in applicable)
        return weight * covered / len(applicable), f"适用{len(applicable)}项，覆盖{covered}项。"
    flow_score, flow_note = component([item for item in scopes if item["item"] in ("正常", "备选", "异常")], 15)
    method_score, method_note = component(methods, 10)
    total = round(link_score + pass_score + flow_score + method_score, 2)
    missing = [item["item"] for item in scopes + methods if item["status"] == "missing"]
    ready = coverage["status"] == "passed" and not missing and counts["需确认"] == 0 and counts["不通过"] == 0
    # 只保留分项得分供内部定位；不再产出字母等级（字母最容易被误当质量结论）。
    return {"total": total, "ready": ready, "counts": counts, "missing": missing, "components": {"关联覆盖": round(link_score, 2), "用例通过率": round(pass_score, 2), "适用三流覆盖": round(flow_score, 2), "适用方法覆盖": round(method_score, 2)}, "flow_note": flow_note, "method_note": method_note}


def render_report(module: str, coverage: dict, reviews: dict, scopes: list[dict], methods: list[dict], score: dict,
                  approved_coverage: dict | None = None, preparation: dict | None = None,
                  reviewed_source: dict | None = None) -> str:
    counts = score["counts"]
    blocking = counts["需确认"] + counts["不通过"] + len(score["missing"])
    lines = [f"# {module}用例评审报告", "",
             "关联覆盖门禁：" + coverage["status"]
             + f"（REQ {percent(coverage['requirement_coverage']['rate'])}，"
               f"AC {percent(coverage['acceptance_coverage']['rate'])}）",
             f"阻塞项：{blocking} 个（需确认 {counts['需确认']}，不通过 {counts['不通过']}，"
             f"缺失适用范围/方法 {len(score['missing'])}）", "",
             "评审结论：" + ("已确认范围的用例设计通过本次评审，可进入执行准备。"
                            if score["ready"] else "存在待处理事项，不能宣称全部用例可执行。"), "",
             "本报告评审用例设计，没有执行用例或运行真实被测系统，不代表软件测试通过。", ""]
    if approved_coverage is not None:
        lines.extend(["## 设计评审通过用例的关联覆盖", "",
                      f"REQ {percent(approved_coverage['requirement_coverage']['rate'])}；"
                      f"AC {percent(approved_coverage['acceptance_coverage']['rate'])}。",
                      "全部用例的关联覆盖与通过用例的关联覆盖分别报告；需确认或不通过用例不贡献本项。",
                      "具体AC及其全部/通过/待确认用例见用例覆盖矩阵.md。", ""])
    if preparation is not None:
        lines.extend(["## 执行准备", "", render_preparation_report(preparation), ""])
    lines.extend([
             "## 结构自检得分（内部定位用，非质量评分）", "",
             f"合计 {score['total']:.2f}/100。该得分只反映交付结构是否齐全（关联是否挂齐、"
             "结论是否齐备、适用场景与方法是否覆盖），**不衡量用例质量**，不得作为对外质量结论。", "",
f"- 关联覆盖（45分）：{score['components']['关联覆盖']:.2f}。REQ {percent(coverage['requirement_coverage']['rate'])}，AC {percent(coverage['acceptance_coverage']['rate'])}，取较小值。", f"- 用例通过率（30分）：{score['components']['用例通过率']:.2f}。仅“通过”计入，需确认不视作通过。", f"- 适用三流覆盖（15分）：{score['components']['适用三流覆盖']:.2f}。{score['flow_note']}", f"- 适用方法覆盖（10分）：{score['components']['适用方法覆盖']:.2f}。{score['method_note']}", "", "存在阻塞项时必须另报，得分高低不改变该结论。"])
    if score["missing"]:
        lines.extend(["", "缺失的适用范围/方法：" + "、".join(score["missing"]) + "。"])
    for title, key in (("未关联REQ", "requirement_coverage"), ("未关联验收标准", "acceptance_coverage")):
        lines.extend(["", title + "：" + ("、".join(coverage[key]["missing"]) or "无。")])
    for status, title in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        excluded = coverage["excluded"][status]
        lines.extend(["", f"{title}：{len(excluded)}条，不计入已确认范围分母。"])
        lines.extend(f"- {item['id']}：{item['reason']}" for item in excluded)
    lines.extend(["", "## 逐条评审", ""])
    for case_id, item in reviews.items():
        lines.extend([f"### {case_id}：{item['结论']}", "", f"原因：{item['原因']}", f"建议：{item['建议']}", ""])
    for title, checks in (("六类场景检查", scopes), ("六种设计方法检查", methods)):
        lines.extend([f"## {title}", ""])
        for item in checks:
            evidence = "、".join(item["evidence"]) or "无"
            lines.append(f"- {item['item']}：{item['status']}。证据：{evidence}。理由：{item['reason']}")
        lines.append("")
    if reviewed_source is not None:
        lines.extend(["## 被评审版本", "",
                      "用例设计、需求、来源或能力证据发生变化后必须重新评审；执行记录不冒充设计变更。", ""])
        lines.extend(f"- {key}：`{value}`" for key, value in reviewed_source.items())
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="在派生Excel第14列写入AI评审，保留源文件和原13列。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        source = (args.input or module_dir / "详细用例.xlsx").resolve()
        output = (args.output or module_dir / "详细用例_已评审.xlsx").resolve()
        if source == output:
            raise ValidationError("评审输出不可覆盖源Excel，请指定不同输出路径。")
        spec = validate_spec(module_dir)
        cases = validate_cases(load_json(module_dir / "用例数据.json"), spec, module_dir=module_dir)
        review_data = load_json(args.review)
        reviews, scopes, methods = validate_review(review_data, cases, spec, module_dir)
        combined = review_coverage(spec, cases, reviews)
        coverage, approved_coverage = combined['all_coverage'], combined['approved_coverage']
        manifest = load_manifest(module_dir)
        score = score_review(coverage, reviews, scopes, methods)
        before_context = dict(review_data["reviewed_source"])
        destinations = [output] + [module_dir / name for name in (
            '评审报告.md', '评审结果.json', '用例覆盖矩阵.md', '执行准备数据.json', '执行准备清单.md',
             '可读性数据.json', '可读性报告.md')]
        inputs = protected_inputs(spec, module_dir) | {source, args.review.resolve()}
        if any(path.resolve() in inputs for path in destinations):
            raise ValidationError("派生评审输出不可覆盖源工作簿、需求、用例或评审输入。")
        original_hash = hashlib.sha256(source.read_bytes()).digest()
        snapshot = read_xlsx(source, columns=len(HEADERS))
        if hashlib.sha256(source.read_bytes()).digest() != original_hash:
            raise ValidationError("读取工作簿期间源文件发生变化，请重新评审当前内容。")
        if snapshot["name"] == "草稿用例":
            raise ValidationError("草稿表不能作为正式评审输入，请先核验需求并生成正式用例表。")
        if snapshot["rows"][0] != HEADERS:
            raise ValidationError("源Excel前13列标题与约定不一致。")
        order = [row[0] for row in snapshot["rows"][1:]]
        if len(order) != len(set(order)) or set(order) != set(reviews):
            raise ValidationError("源Excel用例编号与用例数据/评审不一致，或含重复/空编号。")
        expected_rows = {row[0]: row for row in case_matrix(cases)[1:]}
        for row in snapshot["rows"][1:]:
            expected = expected_rows[row[0]]
            for index, (actual, wanted) in enumerate(zip(row, expected)):
                if index in (7, 12):
                    continue  # Execution records and scripts may have been filled by people.
                if actual != wanted and not (wanted.startswith("=") and actual == "'" + wanted):
                    raise ValidationError(f"源Excel {row[0]}/{HEADERS[index]} 与当前用例数据不同，请重新评审当前内容。")
        # Preserve and report the two fields humans may fill in the source XLSX.
        rows_by_id = {row[0]: row for row in snapshot['rows'][1:]}
        recorded_cases = [dict(case, 实际结果=rows_by_id[case['用例编号']][7],
                               自动化脚本=rows_by_id[case['用例编号']][12]) for case in cases]
        preparation = build_preparation_report(spec, recorded_cases, reviews, project_root=module_dir.parent.parent,
                                               framework_root=framework_root(manifest, module_dir) if manifest else None)
        review_cells = [f"结论：{reviews[case_id]['结论']}\n原因：{reviews[case_id]['原因']}\n建议：{reviews[case_id]['建议']}" for case_id in order]
        def publication_guard():
            current_spec = load_json(module_dir / 'REQ清单.json')
            current_cases = validate_cases(load_json(module_dir / '用例数据.json'), current_spec, module_dir=module_dir)
            if (hashlib.sha256(source.read_bytes()).digest() != original_hash
                    or review_fingerprints(current_spec, current_cases, module_dir) != before_context
                    or load_json(args.review) != review_data):
                raise ValidationError("评审发布前输入已改变，请重新评审当前版本；已拒绝发布。")
        run_xlsx({"operation": "review", "input": str(source), "sheetName": snapshot["name"], "reviews": review_cells},
                 output, publication_guard=publication_guard)
        result = {'schema_version':'1.0', 'module':spec['module'], 'code':spec['code'],
                  'reviewer':review_data['reviewer'],
                  'reviewed_source':before_context, 'reviews':list(reviews.values()),
                  'scope_checks':scopes, 'method_checks':methods, 'coverage':coverage,
                  'approved_coverage':approved_coverage, 'matrix':combined['matrix'],
                  'preparation':preparation, 'summary':score}
        write_text(module_dir / '评审结果.json', json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        write_text(module_dir / '用例覆盖矩阵.md', render_review_coverage(combined))
        write_text(module_dir / '执行准备数据.json', json.dumps(preparation, ensure_ascii=False, indent=2)+'\n')
        write_text(module_dir / '执行准备清单.md', render_preparation_report(preparation))
        write_text(module_dir / "评审报告.md", render_report(spec["module"], coverage, reviews, scopes, methods, score,
                   approved_coverage, preparation, before_context))
        blocking = score["counts"]["需确认"] + score["counts"]["不通过"] + len(score["missing"])
        print("已生成评审副本及报告。关联覆盖门禁：" + coverage["status"]
              + f"；阻塞项：{blocking} 个；结构自检得分 {score['total']:.2f}"
              + "（内部定位用，非质量评分）。评审未执行真实被测系统。")
        return 0
    except (ValueError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
