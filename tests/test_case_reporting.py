"""Focused regressions for review coverage and actionable execution preparation."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))

from qa_cases import ValidationError, coverage_data
from case_reporting import (build_preparation_report, render_preparation_report,
                            render_review_coverage, review_coverage, validate_preparation)


def spec():
    return {"module": "准备核验", "code": "PRE", "requirements": [
        {"id": "REQ_PRE_001", "title": "正常行为", "description": "确认行为", "kind": "function", "status": "active",
         "acceptance_criteria": [{"id": "AC_PRE_001_01", "text": "正常验收"}]},
        {"id": "REQ_PRE_002", "title": "非功能行为", "description": "资源限制", "kind": "nonfunctional", "status": "active",
         "acceptance_criteria": [{"id": "AC_PRE_002_01", "text": "资源验收"}]},
        {"id": "REQ_PRE_003", "title": "待澄清行为", "description": "需要规则", "status": "blocked", "status_reason": "规则未定",
         "acceptance_criteria": [{"id": "AC_PRE_003_01", "text": "待澄清"}]},
        {"id": "REQ_PRE_004", "title": "旧行为", "description": "已废弃", "status": "deprecated",
         "acceptance_criteria": [{"id": "AC_PRE_004_01", "text": "已废弃"}]},
    ]}


def case(number=1, requirement=1, **values):
    return {"用例编号": f"TC_PRE_{number:03}", "关联需求": [f"REQ_PRE_{requirement:03}"],
            "验收标准": [f"AC_PRE_{requirement:03}_01"], "是否可自动化": "是",
            "自动化脚本": "", "实际结果": "", **values}


def review(cases, verdict="通过"):
    return {item["用例编号"]: {"用例编号": item["用例编号"], "结论": verdict,
                              "原因": "人工设计判断", "建议": "明确准备"} for item in cases}


def preparation(identifier="PREP_001", *, affects=None, status="open", **values):
    result = {"id": identifier, "category": "状态准备", "description": "需要固定组队状态",
              "action": "增加状态准备夹具并记录复位方法。", "status": status,
              "affects": affects or ["manual", "automation"]}
    if status == "resolved":
        result["resolution"] = "准备账号与状态，并记录复位证据。"
    result.update(values)
    return result


class ReviewCoverageTests(unittest.TestCase):
    def test_full_and_approved_coverage_use_same_denominator(self):
        cases = [case(1), case(2, 2), case(3, 2), case(4, 2)]
        reviews = review(cases[:3])
        reviews["TC_PRE_002"]["结论"] = "需确认"
        reviews["TC_PRE_003"]["结论"] = "不通过"
        data = review_coverage(spec(), cases, reviews)
        self.assertEqual(data["all_coverage"], coverage_data(spec(), cases))
        self.assertEqual(data["all_coverage"]["status"], "passed")
        self.assertEqual(data["approved_coverage"]["status"], "failed")
        self.assertEqual(data["approved_coverage"]["acceptance_coverage"]["rate"], 0.5)
        self.assertEqual(data["approved_coverage"]["requirement_coverage"]["total"], 2)
        self.assertEqual(data["approved_coverage"]["acceptance_coverage"]["total"], 2)
        req_row = data["matrix"]["requirements"][1]
        ac_row = data["matrix"]["acceptance_criteria"][1]
        for row in (req_row, ac_row):
            self.assertEqual(row["status"], "only_unapproved")
            self.assertEqual(row["all_case_ids"], ["TC_PRE_002", "TC_PRE_003", "TC_PRE_004"])
            self.assertEqual(row["approved_case_ids"], [])
            self.assertEqual(row["pending_case_ids"], ["TC_PRE_002"])
            self.assertEqual(row["rejected_case_ids"], ["TC_PRE_003"])
            self.assertEqual(row["unreviewed_case_ids"], ["TC_PRE_004"])
        report = render_review_coverage(data)
        self.assertIn("全部用例 | 4 | 2/2", report)
        self.assertIn("设计评审通过用例 | 1 | 1/2", report)
        self.assertIn("only_unapproved", report)

    def test_pending_only_cannot_claim_approved_full_coverage(self):
        cases = [case(1), case(2, 2)]
        data = review_coverage(spec(), cases, review(cases, "需确认"))
        self.assertEqual(data["all_coverage"]["acceptance_coverage"]["rate"], 1)
        self.assertEqual(data["approved_coverage"]["acceptance_coverage"]["rate"], 0)
        self.assertEqual(data["approved_coverage"]["case_count"], 0)
        self.assertTrue(all(row["status"] == "only_unapproved" for row in data["matrix"]["acceptance_criteria"]))

    def test_without_reviews_and_missing_links_are_explicit(self):
        data = review_coverage(spec(), [case()])
        self.assertEqual(data["matrix"]["requirements"][0]["status"], "only_unapproved")
        self.assertEqual(data["matrix"]["requirements"][1]["status"], "uncovered")
        self.assertEqual(data["approved_coverage"]["acceptance_coverage"]["covered"], 0)
        self.assertEqual(len(data["all_coverage"]["excluded"]["blocked"]), 1)
        self.assertEqual(len(data["all_coverage"]["excluded"]["deprecated"]), 1)

    def test_review_lists_are_supported_and_bad_ids_rejected(self):
        rows = [case()]
        self.assertEqual(review_coverage(spec(), rows, {"reviews": list(review(rows).values())})["approved_coverage"]["case_count"], 1)
        with self.assertRaises(ValidationError):
            review_coverage(spec(), rows, list(review(rows).values()) * 2)
        with self.assertRaises(ValidationError):
            review_coverage(spec(), rows, review([case(2)]))


class PreparationValidationTests(unittest.TestCase):
    def test_legacy_absence_and_explicit_empty_remain_readable(self):
        validate_preparation([case(), case(2, preparation=[])])

    def test_duplicate_ids_within_one_case_rejected(self):
        item = preparation()
        with self.assertRaisesRegex(ValidationError, "重复准备项"):
            validate_preparation([case(preparation=[item, copy.deepcopy(item)])])

    def test_shared_item_must_have_consistent_definition(self):
        first = preparation()
        second = preparation(affects=["automation", "manual"])
        validate_preparation([case(preparation=[first]), case(2, preparation=[second])])
        for field, value in (("action", "另一个补齐办法"), ("status", "resolved"), ("category", "账号")):
            conflict = copy.deepcopy(second)
            conflict[field] = value
            if field == "status":
                conflict["resolution"] = "另一项已处理"
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "定义不一致"):
                validate_preparation([case(preparation=[first]), case(2, preparation=[conflict])])

    def test_shapes_required_fields_and_resolution(self):
        mutations = [None, {}, ["text"], [preparation(category="未知")], [preparation(action="")],
                     [preparation(affects=["manual", "manual"])], [preparation(affects=["other"])],
                     [preparation(status="ready")], [preparation(status="resolved", resolution="")]]
        for items in mutations:
            with self.subTest(items=items), self.assertRaises(ValidationError):
                validate_preparation([case(preparation=items)])
        item = preparation()
        item["affects"] = []
        with self.assertRaises(ValidationError):
            validate_preparation([case(preparation=[item])])


class PreparationReportingTests(unittest.TestCase):
    def test_missing_metadata_does_not_mean_ready(self):
        cases = [case(), case(2, preparation=[])]
        data = build_preparation_report(spec(), cases, review(cases))
        self.assertEqual(data["counts"]["design_approved"], 2)
        self.assertEqual(data["counts"]["manual_ready"], 1)
        self.assertEqual(data["counts"]["preparation_unknown"], 1)
        self.assertIsNone(data["case_readiness"][0]["manual_ready"])
        self.assertIsNone(data["case_readiness"][0]["automation_ready"])
        self.assertEqual(data["open_preparation"][0]["category"], "未核验准备")
        self.assertEqual(data["open_preparation"][0]["affected_case_ids"], ["TC_PRE_001"])
        self.assertIn("preparation: []", data["open_preparation"][0]["action"])

    def test_no_automation_readiness_from_design_pass_or_function_alone(self):
        cases = [case(1, preparation=[]), case(2, preparation=[], _automation_verified=True),
                 case(3, preparation=[], _automation_verified=True, _automation_ready=False),
                 case(4, preparation=[], _automation_verified=True, _automation_ready=True)]
        data = build_preparation_report(spec(), cases, review(cases))
        self.assertEqual(data["counts"]["design_approved"], 4)
        self.assertEqual(data["counts"]["manual_ready"], 4)
        self.assertEqual(data["counts"]["automation_ready"], 0)
        self.assertEqual(data["counts"]["capability_ready"], 1)
        self.assertIsNone(data["case_readiness"][0]["automation_ready"])
        self.assertIsNone(data["case_readiness"][1]["automation_ready"])
        self.assertFalse(data["case_readiness"][2]["automation_ready"])
        self.assertFalse(data["case_readiness"][3]["automation_ready"])

    def test_open_manual_and_automation_items_affect_only_declared_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
            shared = {"自动化脚本": "run.py", "_automation_verified": True, "_automation_ready": True}
            cases = [case(1, preparation=[preparation("PREP_manual", affects=["manual"])], **shared),
                     case(2, 2, preparation=[preparation("PREP_auto", affects=["automation"])], **shared),
                     case(3, preparation=[preparation("PREP_fixed", status="resolved")], **shared)]
            data = build_preparation_report(spec(), cases, review(cases), project_root=root)
        first, second, third = data["case_readiness"]
        self.assertFalse(first["manual_ready"])
        self.assertTrue(first["automation_ready"])
        self.assertTrue(second["manual_ready"])
        self.assertFalse(second["automation_ready"])
        self.assertTrue(third["manual_ready"])
        self.assertTrue(third["automation_ready"])
        self.assertEqual(data["counts"]["open_preparation_items"], 2)
        self.assertEqual({item["id"] for item in data["open_preparation"]}, {"PREP_manual", "PREP_auto"})

    def test_shared_items_group_cases_and_acceptance_criteria(self):
        items = [preparation(category="观测接口", affects=["automation"])]
        cases = [case(1, preparation=copy.deepcopy(items)), case(2, 2, preparation=copy.deepcopy(items))]
        data = build_preparation_report(spec(), cases, review(cases, "需确认"))
        item = data["open_preparation"][0]
        self.assertEqual(item["affected_case_ids"], ["TC_PRE_001", "TC_PRE_002"])
        self.assertEqual(item["affected_acceptance_criteria_ids"], ["AC_PRE_001_01", "AC_PRE_002_01"])
        self.assertEqual(item["action"], items[0]["action"])
        self.assertFalse(any(row["manual_ready"] is True for row in data["case_readiness"]))
        self.assertFalse(any(row["automation_ready"] is True for row in data["case_readiness"]))

    def test_free_text_is_not_structured_preparation_or_test_success(self):
        cases = [case(1, 实际结果="通过", 自动化建议明细="已有函数/资源：all_ready()"),
                 case(2, 实际结果="执行失败", preparation=[])]
        reviews = review(cases)
        reviews["TC_PRE_001"]["原因"] = "账号状态资产全部具备，可执行且已通过"
        data = build_preparation_report(spec(), cases, reviews)
        self.assertEqual(data["counts"]["execution_records_present"], 2)
        self.assertEqual(data["counts"]["automation_ready"], 0)
        self.assertEqual(data["counts"]["preparation_unknown"], 1)
        self.assertNotIn("execution_passed", data["counts"])
        self.assertNotIn("execution_passed", data["case_readiness"][0])
        report = render_preparation_report(data)
        self.assertIn("不从自由文本推定测试通过", report)
        self.assertIn("未核验准备", report)

    def test_script_requires_verified_known_local_path_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project, framework = base / "project", base / "framework"
            project.mkdir()
            framework.mkdir()
            (project / "run.py").write_text("raise RuntimeError('never execute')", encoding="utf-8")
            (framework / "run.py").write_text("raise RuntimeError('never execute')", encoding="utf-8")
            outside = base / "outside.py"
            outside.write_text("raise RuntimeError('never execute')", encoding="utf-8")
            (project / "escape.py").symlink_to(outside)
            common = {"preparation": [], "_automation_verified": True, "_automation_ready": True}
            cases = [case(1, 自动化脚本=str(project / "run.py"), **common),
                     case(2, 自动化脚本="run.py", **common),
                     case(3, 自动化脚本=str(outside), **common),
                     case(4, 自动化脚本="escape.py", **common),
                     case(5, 自动化脚本="python run.py", **common),
                     case(6, 自动化脚本="missing.py", **common)]
            data = build_preparation_report(spec(), cases, review(cases), project_root=project, framework_root=framework)
            no_roots = build_preparation_report(spec(), cases[:1], review(cases[:1]))
        self.assertEqual(data["counts"]["capability_ready"], 6)
        self.assertEqual(data["counts"]["automation_ready"], 1)
        self.assertTrue(data["case_readiness"][0]["automation_ready"])
        self.assertIsNone(data["case_readiness"][1]["automation_ready"])
        self.assertTrue(all(row["automation_ready"] is False for row in data["case_readiness"][2:]))
        self.assertIsNone(no_roots["case_readiness"][0]["automation_ready"])


if __name__ == "__main__":
    unittest.main()
