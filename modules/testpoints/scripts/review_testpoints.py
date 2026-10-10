#!/usr/bin/env python3
"""Validate model-written test-point reviews and publish auditable reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testcase-builder" / "scripts"))

from qa_cases import ValidationError, cli_error, load_json, percent, write_text
from update_review import score_review, validate_review_structure as validate_case_review
from point_common import fingerprints, load_context, markdown_text, point_coverage, protect_outputs, write_json


def validate_point_review(data: dict, points: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """Pure validation shared with consumers of saved review results."""
    if not isinstance(data, dict):
        raise ValidationError("测试点评审数据顶层必须为对象。")
    raw_reviews = data.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValidationError("reviews 必须为数组。")
    adapted_reviews = []
    for index, row in enumerate(raw_reviews, 1):
        if not isinstance(row, dict):
            raise ValidationError(f"第{index}条测试点评审必须为对象。")
        adapted_reviews.append({
            "用例编号": row.get("测试点编号"),
            "结论": row.get("结论"),
            "原因": row.get("原因"),
            "建议": row.get("建议"),
        })
    adapted_points = [dict(point, 用例编号=point["测试点编号"]) for point in points]
    adapted_data = dict(data, reviews=adapted_reviews)
    try:
        reviewed, scopes, methods = validate_case_review(adapted_data, adapted_points)
    except ValidationError as exc:
        raise ValidationError(str(exc).replace("用例", "测试点").replace("TC", "TP")) from exc

    # The v1 validator checks covered evidence. Every stated TP, including evidence
    # for a missing item, must agree with the item's declared metadata here.
    for key, checks, field in (("scope_checks", scopes, "场景类型"), ("method_checks", methods, "设计方法")):
        for item in checks:
            matching = {
                point["测试点编号"] for point in points
                if (point[field] == item["item"] if field == "场景类型" else item["item"] in point[field])
            }
            if set(item["evidence"]) - matching:
                raise ValidationError(f"{key}/{item['item']} 证据与测试点{field}元数据不匹配。")
    reviews = {
        point["测试点编号"]: {
            "测试点编号": point["测试点编号"],
            "结论": reviewed[point["测试点编号"]]["结论"],
            "原因": reviewed[point["测试点编号"]]["原因"],
            "建议": reviewed[point["测试点编号"]]["建议"],
        }
        for point in points
    }
    return reviews, scopes, methods


validate_review = validate_point_review


def score_point_review(approved_coverage: dict, reviews: dict, scopes: list[dict], methods: list[dict], points: list[dict] | None = None) -> dict:
    """Calculate the v1 rubric with approved-point association coverage."""
    approved_ids = {point_id for point_id, row in reviews.items() if row["结论"] == "通过"}
    def approved_checks(checks):
        result = []
        for item in checks:
            current = dict(item)
            if item["status"] == "covered" and not (set(item["evidence"]) & approved_ids):
                current["status"] = "missing"
            result.append(current)
        return result
    score = score_review(approved_coverage, reviews, approved_checks(scopes), approved_checks(methods))
    score["flow_note"] += "评分仅计入有通过点证据的覆盖项。"
    score["method_note"] += "评分仅计入有通过点证据的覆盖项。"
    score["components"]["测试点通过率"] = score["components"].pop("用例通过率")
    score["duplicate_groups"] = duplicate_groups(points or [])
    if score["duplicate_groups"]:
        score["ready"] = False
    return score


def build_result(spec: dict, points: list[dict], review_data: dict, source_hashes: dict) -> dict:
    """Revalidate written judgments and recompute every derived field."""
    reviews, scopes, methods = validate_point_review(review_data, points)
    coverage = point_coverage(spec, points)
    approved = point_coverage(spec, [point for point in points if reviews[point["测试点编号"]]["结论"] == "通过"])
    return {
        "schema_version": "1.0", "module": spec["module"], "code": spec["code"],
        "reviewed_source": dict(source_hashes), "reviews": list(reviews.values()),
        "scope_checks": scopes, "method_checks": methods,
        "coverage": coverage, "approved_coverage": approved,
        "summary": score_point_review(approved, reviews, scopes, methods, points),
    }


def duplicate_groups(points: list[dict]) -> list[list[str]]:
    """Report exactly repeated content; semantic duplicates require model review."""
    fields = ("优先级", "测试点名称", "前置条件", "检查内容", "测试数据", "预期结果", "关联需求", "验收标准", "场景类型", "设计方法")
    groups = {}
    for point in points:
        signature = json.dumps([point[field] for field in fields], ensure_ascii=False, sort_keys=True)
        groups.setdefault(signature, []).append(point["测试点编号"])
    return [identifiers for identifiers in groups.values() if len(identifiers) > 1]


def render_report(spec: dict, points: list[dict], reviews: dict, scopes: list[dict], methods: list[dict], coverage: dict, approved_coverage: dict, score: dict, reviewed_source: dict) -> str:
    counts = score["counts"]
    blocking = counts["需确认"] + counts["不通过"] + len(score["missing"])
    lines = [
        f"# {spec['module']}测试点评审报告", "",
        "关联覆盖门禁：" + coverage["status"]
        + f"（REQ {percent(coverage['requirement_coverage']['rate'])}，"
          f"AC {percent(coverage['acceptance_coverage']['rate'])}）",
        f"阻塞项：{blocking} 个（需确认 {counts['需确认']}，不通过 {counts['不通过']}，"
        f"缺失适用范围/方法 {len(score['missing'])}）", "",
        "评审结论：" + ("已确认范围的测试点通过本次设计评审，可用于细化详细用例。"
                       if score["ready"] else "存在待处理事项，不能宣称全部测试点设计通过或全范围已完成。"), "",
        "业务评审原因与建议由模型结合需求写入；本脚本只校验结构、证据编号并计算自检得分。",
        "关联比例不能证明测试充分。本次没有执行测试点或运行真实被测系统，不代表软件测试通过。", "",
        "## 关联覆盖对比", "",
        "| 范围 | 测试点数 | REQ关联 | 验收标准关联 | 关联检查状态 |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for label, data in (("全部测试点", coverage), ("仅通过测试点（计分）", approved_coverage)):
        req, ac = data["requirement_coverage"], data["acceptance_coverage"]
        lines.append(f"| {label} | {data['point_count']} | {req['covered']}/{req['total']}（{percent(req['rate'])}） | {ac['covered']}/{ac['total']}（{percent(ac['rate'])}） | {data['status']} |")
    if coverage["status"] == "not_designable":
        lines.extend(["", "不可设计：没有active需求，关联比例不可计算，不能报告100%覆盖率。"])
    lines.extend([
        "", "## 结构自检得分（内部定位用，非质量评分）", "",
        f"- 通过点关联覆盖（45分）：{score['components']['关联覆盖']:.2f}。仅通过点的REQ关联 {percent(approved_coverage['requirement_coverage']['rate'])}、AC关联 {percent(approved_coverage['acceptance_coverage']['rate'])}，取较小值。",
        f"- 测试点通过率（30分）：{score['components']['测试点通过率']:.2f}。仅“通过”计入，需确认不视作通过。",
        f"- 适用三流覆盖（15分）：{score['components']['适用三流覆盖']:.2f}。{score['flow_note']}",
        f"- 适用方法覆盖（10分）：{score['components']['适用方法覆盖']:.2f}。{score['method_note']}",
        "", "存在阻塞项时必须另报，得分高低不改变该结论。ready仅表示已确认范围的设计评审完成。",
    ])
    if score["missing"]:
        lines.extend(["", "缺失的适用范围/方法：" + "、".join(score["missing"]) + "。"])
    for label, data in (("全部测试点", coverage), ("通过测试点", approved_coverage)):
        for title, key in (("未覆盖REQ", "requirement_coverage"), ("未覆盖验收标准", "acceptance_coverage")):
            lines.extend(["", f"{label}{title}：" + ("、".join(data[key]["missing"]) or "无。")])
    for status, title in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        excluded = coverage["excluded"][status]
        lines.extend(["", f"{title}：{len(excluded)}条，不计入已确认范围分母。"])
        lines.extend(f"- {item['id']}：{item['reason']}" for item in excluded)
    groups = duplicate_groups(points)
    lines.extend(["", "## 精确重复内容检查", ""])
    if groups:
        lines.extend("- " + "、".join(group) + "：编号不同，其余必填字段完全相同，请结合逐点评审处理。" for group in groups)
    else:
        lines.append("未发现除编号外所有必填字段完全相同的测试点。语义重复仍由模型结合需求判断。")
    lines.extend(["", "## 逐点评审", ""])
    for point in points:
        point_id = point["测试点编号"]
        item = reviews[point_id]
        lines.extend([
            f"### {point_id}：{item['结论']}", "",
            f"测试点：{point['测试点名称']}",
            "关联需求：" + "、".join(point["关联需求"]),
            "验收标准：" + "、".join(point["验收标准"]),
            f"原因：{item['原因']}", f"建议：{item['建议']}", "",
        ])
    for title, checks in (("六类场景检查", scopes), ("六种设计方法检查", methods)):
        lines.extend([f"## {title}", ""])
        for item in checks:
            evidence = "、".join(item["evidence"]) or "无"
            lines.append(f"- {item['item']}：{item['status']}。证据：{evidence}。理由：{item['reason']}")
        lines.append("")
    lines.extend([
        "## 评审源快照", "",
        f"模块：{spec['module']}；代码：{spec['code']}。",
        f"REQ清单 SHA-256：`{reviewed_source['requirements_sha256']}`",
        f"测试点数据 SHA-256：`{reviewed_source['testpoints_sha256']}`",
        "需求或测试点文件发生变化后，必须重新评审，不能沿用本次结论。", "",
    ])
    return "\n".join(lines)


def render_matrix(spec: dict, points: list[dict], reviews: dict) -> str:
    def associations(identifier: str, field: str, status: str) -> tuple[str, str, str]:
        all_ids = [point["测试点编号"] for point in points if identifier in point[field]]
        approved = [point_id for point_id in all_ids if reviews[point_id]["结论"] == "通过"]
        if status != "active":
            state = "阻塞，不计入分母" if status == "blocked" else "废弃，不计入分母"
        elif not all_ids:
            state = "全部点未覆盖；通过点未覆盖"
        elif not approved:
            state = "全部点已关联；通过点未覆盖"
        else:
            state = "通过点已关联"
        return "、".join(all_ids) or "无", "、".join(approved) or "无", state

    lines = [
        f"# {spec['module']}测试点覆盖矩阵", "",
        "逐条列出所有REQ及验收标准，包括阻塞和废弃范围。全部关联与通过点关联分别展示；编号关联不代表语义充分。", "",
        "## REQ矩阵", "",
        "| REQ | 标题 | 状态 | 全部关联TP | 通过TP | 覆盖状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for req in spec["requirements"]:
        links = associations(req["id"], "关联需求", req["status"])
        lines.append("| " + " | ".join(markdown_text(value) for value in (req["id"], req["title"], req["status"], *links)) + " |")
    lines.extend([
        "", "## 验收标准矩阵", "",
        "| AC | 所属REQ | 验收标准 | 状态 | 全部关联TP | 通过TP | 覆盖状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for req in spec["requirements"]:
        for ac in req.get("acceptance_criteria", []):
            links = associations(ac["id"], "验收标准", req["status"])
            lines.append("| " + " | ".join(markdown_text(value) for value in (ac["id"], req["id"], ac["text"], req["status"], *links)) + " |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验模型填写的逐点评审，生成测试点评审结果、报告和覆盖矩阵。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--review", type=Path, help="默认读取模块目录下的测试点评审数据.json。")
    parser.add_argument("--input", type=Path, help="可选测试点数据JSON，默认测试点数据.json。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        input_path = (args.input or module_dir / "测试点数据.json").resolve()
        review_path = (args.review or module_dir / "测试点评审数据.json").resolve()
        destinations = [module_dir / name for name in ("测试点评审结果.json", "测试点评审报告.md", "测试点覆盖矩阵.md")]
        protect_outputs(module_dir, destinations, input_path, (review_path,))
        reviewed_source = fingerprints(module_dir, input_path)
        spec, _, points = load_context(module_dir, input_path)
        result = build_result(spec, points, load_json(review_path), reviewed_source)
        reviews = {row["测试点编号"]: row for row in result["reviews"]}
        scopes, methods = result["scope_checks"], result["method_checks"]
        coverage, approved_coverage, summary = result["coverage"], result["approved_coverage"], result["summary"]
        report = render_report(spec, points, reviews, scopes, methods, coverage, approved_coverage, summary, reviewed_source)
        matrix = render_matrix(spec, points, reviews)
        if fingerprints(module_dir, input_path) != reviewed_source:
            raise ValidationError("评审期间REQ或测试点输入发生变化，请重新评审当前数据。")
        write_json(destinations[0], result)
        write_text(destinations[1], report)
        write_text(destinations[2], matrix)
        blocking = (summary["counts"]["需确认"] + summary["counts"]["不通过"]
                    + len(summary["missing"]))
        print("已生成测试点评审结果、报告与覆盖矩阵。关联覆盖门禁：" + coverage["status"]
              + f"；阻塞项：{blocking} 个；结构自检得分 {summary['total']:.2f}"
              + "（内部定位用，非质量评分）。未执行真实被测系统。")
        return 0
    except (ValidationError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
