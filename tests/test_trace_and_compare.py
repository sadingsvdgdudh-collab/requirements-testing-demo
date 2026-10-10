"""追溯表、场景分布与候选设计对比的回归测试。

Run from any directory: python tests/test_trace_and_compare.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "modules" / "testcase-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from qa_cases import (METHODS, SCOPES, HEADERS, ValidationError, case_method_counts, case_scope_counts,
                      coverage_data, distribution_summary, linked_test_points, trace_rows)
from render_xlsx import extra_sheets
import compare_designs as compare


def make_case(case_id: str, **overrides) -> dict:
    # AC 必须属于该用例关联的需求；默认按首个需求派生，避免夹具自身产生错挂。
    reqs = overrides.get("关联需求", ["REQ_TST_001"])
    default_ac = ["AC_" + reqs[0].replace("REQ_", "") + "_01"] if reqs else ["AC_TST_001_01"]
    case = {
        "用例编号": case_id,
        "优先级": "P1",
        "用例名称": "默认名称",
        "前置条件": "已登录。",
        "操作步骤": ["点击保存。"],
        "测试数据": "名称=甲。",
        "预期结果": ["列表出现新记录。"],
        "实际结果": "",
        "关联需求": ["REQ_TST_001"],
        "是否可自动化": "是",
        "自动化建议": "仅api自动化可用",
        "自动化建议明细": "理由：断言落在服务端。\n已有函数/资源：api_player.inspect()。",
        "自动化脚本": "",
        "验收标准": default_ac,
        "场景类型": "正常",
        "设计方法": ["场景法"],
    }
    case.update(overrides)
    return case


def make_spec() -> dict:
    return {
        "schema_version": "1.0",
        "module": "追溯样例",
        "code": "TST",
        "requirements": [
            {"id": "REQ_TST_001", "title": "甲", "status": "active", "kind": "functional",
             "acceptance_criteria": [{"id": "AC_TST_001_01", "text": "保存成功。"}]},
            {"id": "REQ_TST_002", "title": "乙", "status": "active", "kind": "functional",
             "acceptance_criteria": [{"id": "AC_TST_002_01", "text": "边界生效。"}]},
        ],
    }


class TraceSheetTests(unittest.TestCase):
    def test_trace_rows_expose_json_only_fields(self):
        """AC、场景类型、设计方法、测试点关联只存 JSON，必须能进工作簿。"""
        case = make_case("TC_TST_001", 关联测试点=["TP_TST_001"], 验收标准=["AC_TST_001_01", "AC_TST_001_02"],
                         设计方法=["边界值", "等价类"])
        rows = trace_rows([case])
        self.assertEqual(rows[0], ["TC_TST_001", "P1", "REQ_TST_001",
                                   "AC_TST_001_01\nAC_TST_001_02", "正常", "边界值、等价类", "TP_TST_001"])

    def test_trace_marks_unregistered_and_draft_explicitly(self):
        """未登记与草稿未核验必须可区分，不能留空让人误读成"没有关联"。"""
        case = make_case("TC_TST_001", 验收标准=[], 关联测试点=[], 关联需求=[])
        row = trace_rows([case])[0]
        self.assertEqual(row[2], "未登记")
        self.assertEqual(row[3], "未登记")
        self.assertEqual(row[6], "未登记")
        draft = trace_rows([case], draft=True)[0]
        self.assertEqual(draft[2], "未核验（草稿）")
        # 草稿标记只替换空值，已填的关联照原样保留。
        filled = trace_rows([make_case("TC_TST_002")], draft=True)[0]
        self.assertEqual(filled[2], "REQ_TST_001")

    def test_linked_test_points_accepts_list_and_string_only(self):
        self.assertEqual(linked_test_points({"关联测试点": "TP_TST_001、TP_TST_002"}), ["TP_TST_001", "TP_TST_002"])
        self.assertEqual(linked_test_points({"关联测试点": ["TP_TST_001"]}), ["TP_TST_001"])
        self.assertEqual(linked_test_points({}), [])
        with self.assertRaises(ValidationError):
            linked_test_points({"关联测试点": 3})

    def test_trace_sheet_never_touches_the_fixed_thirteen_columns(self):
        """追溯表是派生只读表：前 13 列契约与列数都不受它影响。"""
        cases = [make_case("TC_TST_001")]
        sheets = extra_sheets({}, cases, draft=False)
        self.assertEqual([sheet["name"] for sheet in sheets], ["追溯"])
        self.assertEqual(sheets[0]["rows"][0], ["用例编号", "优先级", "关联需求", "验收标准",
                                               "场景类型", "设计方法", "关联测试点"])
        self.assertEqual(len(HEADERS), 13)


class CoverageDistributionTests(unittest.TestCase):
    def test_coverage_data_keys_stay_stable_for_cached_point_reviews(self):
        """回归护栏：coverage_data 的返回值被测试点评审结果逐字复核。

        往里加键会让所有既有 测试点评审结果.json 立刻被判为"与当前数据不一致"，
        等于强迫用户重评测试点。分布类信息只能放报告层。
        """
        data = coverage_data(make_spec(), [make_case("TC_TST_001")])
        self.assertEqual(sorted(data), ["acceptance_coverage", "case_count", "code", "excluded",
                                        "module", "requirement_coverage", "schema_version",
                                        "scope", "status"])

    def test_distribution_summary_lists_zero_scopes(self):
        cases = [make_case("TC_TST_001"), make_case("TC_TST_002", 场景类型="边界")]
        summary = distribution_summary(case_scope_counts(cases), SCOPES)
        self.assertEqual(summary["by_item"]["正常"], 1)
        self.assertEqual(summary["by_item"]["边界"], 1)
        self.assertIn("端到端", summary["without_cases"])
        self.assertNotIn("正常", summary["without_cases"])
        methods = distribution_summary(case_method_counts(cases), METHODS)
        self.assertEqual(methods["by_item"]["场景法"], 2)
        self.assertEqual(len(methods["by_item"]), len(METHODS))


class CompareDesignsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qa-compare-"))
        self.module = self.root / "模块"
        self.module.mkdir()
        (self.module / "REQ清单.json").write_text(json.dumps(make_spec(), ensure_ascii=False), encoding="utf-8")
        rich = [make_case("TC_TST_001", 关联需求=["REQ_TST_001"], 场景类型="正常",
                          操作步骤=["打开页面。", "点击保存。"], 预期结果=["提示成功。", "列表新增记录。"],
                          测试数据="名称=甲；价格=9/10/400/401。"),
                make_case("TC_TST_002", 关联需求=["REQ_TST_002"], 场景类型="异常",
                          操作步骤=["点击保存。"], 预期结果=["提示名称必填。"], 测试数据="名称为空。")]
        self.rich = self.module / "丰富设计.json"
        self.rich.write_text(json.dumps({"cases": rich}, ensure_ascii=False), encoding="utf-8")
        self.thin = self.module / "简略设计.json"
        thin = [make_case("TC_TST_101", 关联需求=["REQ_TST_001"], 场景类型="正常", 设计方法=["场景法"],
                          操作步骤=["检查结果。"], 预期结果=["功能正常。"], 测试数据="")]
        self.thin.write_text(json.dumps({"cases": thin}, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *args, ok=True):
        completed = subprocess.run([sys.executable, str(SCRIPTS / "compare_designs.py"), *args],
                                   capture_output=True, text=True, encoding="utf-8")
        if ok and completed.returncode:
            self.fail(f"compare_designs 失败：{completed.stderr or completed.stdout}")
        return completed

    def test_cli_compares_two_designs_and_reports_both_scopes(self):
        self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich),
                     "--alternative", str(self.thin), "--out-dir", str(self.module))
        report = json.loads((self.module / "设计对比数据.json").read_text(encoding="utf-8"))
        self.assertEqual(report["module"], "追溯样例")
        self.assertEqual(len(report["primary"]["designs"]), 2)
        rich_total = report["primary"]["designs"][0]["total"]
        thin_total = report["primary"]["designs"][1]["total"]
        self.assertGreater(rich_total, thin_total)
        # 两份 JSON 都带声明字段，因此不需要同源敏感性对照。
        self.assertIsNone(report["sensitivity"])
        self.assertIn("不评审单份设计能否发布", report["not_a_review"])

    def test_inputs_are_never_modified(self):
        before = (self.rich.read_bytes(), self.thin.read_bytes())
        self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich),
                     "--alternative", str(self.thin), "--out-dir", str(self.module))
        self.assertEqual((self.rich.read_bytes(), self.thin.read_bytes()), before)

    def test_missing_scope_fields_add_a_same_source_sensitivity_reading(self):
        """候选缺 场景类型/设计方法 时必须另给同源口径，而不是只给一个混合总分。"""
        bare = self.module / "裸工作簿设计.json"
        rows = []
        for case in json.loads(self.rich.read_text(encoding="utf-8"))["cases"]:
            item = dict(case)
            item.pop("场景类型")
            item.pop("设计方法")
            rows.append(item)
        bare.write_text(json.dumps({"cases": rows}, ensure_ascii=False), encoding="utf-8")
        self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich),
                     "--alternative", str(bare), "--out-dir", str(self.module))
        report = json.loads((self.module / "设计对比数据.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(report["sensitivity"])
        self.assertEqual(report["sensitivity"]["classification"], "启发式估算")
        text = (self.module / "设计对比报告.md").read_text(encoding="utf-8")
        self.assertIn("口径 A", text)
        self.assertIn("口径 B", text)

    def test_declared_scope_source_refuses_mixed_metadata(self):
        bare = self.module / "裸工作簿设计.json"
        rows = []
        for case in json.loads(self.rich.read_text(encoding="utf-8"))["cases"]:
            item = dict(case)
            item.pop("场景类型")
            item.pop("设计方法")
            rows.append(item)
        bare.write_text(json.dumps({"cases": rows}, ensure_ascii=False), encoding="utf-8")
        completed = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich),
                                 "--alternative", str(bare), "--scope-source", "declared", ok=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("无法保证同口径", completed.stderr + completed.stdout)

    def test_bad_weights_and_missing_alternative_are_rejected(self):
        missing = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich), ok=False)
        self.assertIn("至少提供一个", missing.stderr + missing.stdout)
        weights = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.rich),
                               "--alternative", str(self.thin), "--weights", "0.5,0.5", ok=False)
        self.assertIn("6 个数值", weights.stderr + weights.stdout)

    def test_workbook_alternative_is_read_and_marked_as_estimated(self):
        """13＋1 列工作簿没有 AC/场景/方法列，必须按估算标注而不是当作已登记。"""
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.title = "用例"
        sheet.append(HEADERS)
        sheet.append(["TC_TST_201", "P1", "等级不足被拒绝", "玩家等级 19。", "1. 点击报名。",
                      "等级=19。", "1. 提示等级不足。", "", "REQ_TST_001", "是",
                      "仅api自动化可用", "理由：断言在服务端。\n缺失能力：无。\n补齐办法：无。", ""])
        workbook = self.module / "候选工作簿.xlsx"
        book.save(workbook)
        design = compare.load_design(workbook)
        self.assertEqual(design["cases"][0]["关联需求"], ["REQ_TST_001"])
        self.assertEqual(design["cases"][0]["验收标准"], [])
        self.assertIn("启发式估算", design["field_source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
