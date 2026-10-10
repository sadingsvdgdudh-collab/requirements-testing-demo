#!/usr/bin/env python3
"""Check both active REQ links and acceptance-criterion links."""

import argparse
import json
from pathlib import Path

from qa_cases import (METHODS, SCOPES, ValidationError, case_method_counts, case_scope_counts,
                      cli_error, coverage_report, distribution_summary, load_json, validate_cases,
                      validate_spec, write_text)
from case_reporting import render_scope_applicability, review_coverage, render_review_coverage
from update_review import validate_review


def case_distribution(cases: list[dict]) -> dict:
    """场景类型与设计方法的实际分布；只进报告，不进被复核的覆盖率结构。"""
    return {"scope": distribution_summary(case_scope_counts(cases), SCOPES),
            "method": distribution_summary(case_method_counts(cases), METHODS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查已确认范围的REQ及验收标准双关联覆盖率。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--review", type=Path, help="使用绑定当前版本的评审；默认读取已发布的评审结果.json，缺少时报告未评审。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        spec = validate_spec(module_dir)
        if any(req["status"] == "active" for req in spec["requirements"]):
            data = load_json(module_dir / "用例数据.json")
            cases = validate_cases(data, spec, module_dir=module_dir)
        else:
            cases = []
        review_path = args.review or module_dir / '评审结果.json'
        reviews = scopes = methods = None
        # 需求全部作废/阻塞时没有可设计范围，契约要求输出"不可设计"；
        # 此时不得再拿存量评审去卡门禁，否则覆盖率报告永远生不出来。
        if cases and (args.review is not None or review_path.exists()):
            reviews, scopes, methods = validate_review(load_json(review_path), cases, spec, module_dir)
        combined = review_coverage(spec, cases, reviews)
        coverage = dict(combined['all_coverage'], approved_coverage=combined['approved_coverage'],
                        matrix=combined['matrix'], review_available=reviews is not None)
        # 场景/方法分布与适用性放在报告层：coverage_data() 的返回值被测试点评审
        # 结果缓存并逐字复核，往里加字段会让既有评审被判为过期。
        distribution = case_distribution(cases)
        if reviews is not None:
            approved = [case for case in cases if reviews.get(case["用例编号"], {}).get("结论") == "通过"]
            approved_distribution = case_distribution(approved)
            distribution["approved_scope"] = approved_distribution["scope"]
            distribution["approved_method"] = approved_distribution["method"]
        coverage['case_distribution'] = distribution
        if scopes is not None:
            coverage['scope_applicability'] = {'scopes': scopes, 'methods': methods}
        write_text(module_dir / "覆盖率数据.json", json.dumps(coverage, ensure_ascii=False, indent=2) + "\n")
        write_text(module_dir / "需求覆盖率报告.md",
                   coverage_report(coverage, distribution) + "\n" + render_review_coverage(combined)
                   + render_scope_applicability(scopes, methods))
        print(f"覆盖率检查：{coverage['status']}。已写入覆盖率数据.json和需求覆盖率报告.md。")
        if scopes is not None:
            absent = [item['item'] for item in list(scopes) + list(methods) if item['status'] == 'missing']
            if absent:
                print("另有缺失的适用场景/方法：" + "、".join(absent)
                      + "（不改变关联覆盖门禁，但属设计缺失，必须作为阻塞处理）。")
        else:
            zero = distribution["scope"]["without_cases"]
            if zero:
                print("提示：用例数为 0 的场景类型：" + "、".join(zero)
                      + "（无已发布评审时无法判定适用性，请先评审）。")
        return 0 if coverage["status"] == "passed" else 1
    except (ValueError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
