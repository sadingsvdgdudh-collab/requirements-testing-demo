"""关联编号门禁的回归测试：编号存在性、追溯表反向读取与硬门禁。

Run from any directory: python tests/test_workbook_links.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "modules" / "testcase-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from openpyxl import Workbook
from qa_cases import (HEADERS, METRICS_HEADERS, ValidationError, audit_case_links, link_list,
                      load_link_targets, read_trace_sheet, read_xlsx)
import compare_designs as compare


def spec_payload() -> dict:
    return {
        "schema_version": "1.0",
        "module": "编号样例",
        "code": "TST",
        "requirements": [
            {"id": "REQ_TST_001", "title": "甲", "status": "active", "kind": "functional",
             "acceptance_criteria": [{"id": "AC_TST_001_01", "text": "保存成功。"},
                                     {"id": "AC_TST_001_02", "text": "重名被拒。"}]},
            {"id": "REQ_TST_002", "title": "乙", "status": "active", "kind": "functional",
             "acceptance_criteria": [{"id": "AC_TST_002_01", "text": "上限生效。"}]},
            {"id": "REQ_TST_003", "title": "丙", "status": "blocked", "kind": "functional",
             "acceptance_criteria": []},
        ],
    }


def point_payload() -> dict:
    return {"schema_version": "1.0", "module": "编号样例", "code": "TST",
            "testpoints": [{"测试点编号": "TP_TST_001"}, {"测试点编号": "TP_TST_002"}]}


def write_workbook(path: Path, cases: list[dict], *, with_trace: bool = True) -> None:
    """cases: 用例编号/关联需求/验收标准/关联测试点/场景类型/设计方法。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "用例"
    sheet.append(HEADERS)
    for case in cases:
        sheet.append([case["用例编号"], "P1", case.get("用例名称", "名称"), "已登录。",
                      "1. 点击保存。", "名称=甲。", "1. 提示成功。", "",
                      "\n".join(case.get("关联需求") or []), "是", "仅api自动化可用",
                      "理由：断言在服务端。\n已有函数/资源：inspect()。", ""])
    if with_trace:
        trace = book.create_sheet("追溯")
        trace.append(METRICS_HEADERS)
        for case in cases:
            blank = "未登记"
            trace.append([case["用例编号"], "P1",
                          "\n".join(case.get("关联需求") or []) or blank,
                          "\n".join(case.get("验收标准") or []) or blank,
                          case.get("场景类型") or blank,
                          "、".join(case.get("设计方法") or []),
                          "\n".join(case.get("关联测试点") or []) or blank])
    book.save(path)


class LinkAuditUnitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qa-links-"))
        (self.root / "REQ清单.json").write_text(json.dumps(spec_payload(), ensure_ascii=False), encoding="utf-8")
        (self.root / "测试点数据.json").write_text(json.dumps(point_payload(), ensure_ascii=False), encoding="utf-8")
        self.targets = load_link_targets(self.root)

    def test_clean_links_produce_no_issues(self):
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                  "验收标准": ["AC_TST_001_01"], "关联测试点": ["TP_TST_001"]}]
        self.assertEqual(audit_case_links(cases, self.targets), [])

    def test_out_of_range_shared_string_reports_readable_error(self):
        """外部工作簿 t="s" 越界引用共享字符串表时必须报错，而不是 IndexError 栈。"""
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        path = self.root / "越界.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}">'
                                                f'<sheets><sheet name="用例" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}" count="1" uniqueCount="1"><si><t>甲</t></si></sst>')
            archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>'
                                                         f'<row r="1"><c r="A1" t="s"><v>9</v></c></row></sheetData></worksheet>')
        with self.assertRaises(ValidationError) as caught:
            read_xlsx(path)
        self.assertIn("无法读取用例Excel", str(caught.exception))

    def test_nonexistent_acceptance_criterion_is_reported(self):
        """引用一个 REQ清单里没有的 AC——正是让覆盖率虚高的那种错误。"""
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                  "验收标准": ["AC_TST_001_09"], "关联测试点": []}]
        issues = audit_case_links(cases, self.targets)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["字段"], "验收标准")
        self.assertEqual(issues[0]["编号"], "AC_TST_001_09")
        self.assertIn("不存在", issues[0]["问题"])

    def test_acceptance_criterion_under_another_requirement_is_reported(self):
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_002"],
                  "验收标准": ["AC_TST_001_01"], "关联测试点": []}]
        issues = audit_case_links(cases, self.targets)
        self.assertEqual(len(issues), 1)
        self.assertIn("REQ_TST_001", issues[0]["问题"])

    def test_nonexistent_test_point_is_reported(self):
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                  "验收标准": [], "关联测试点": ["TP_TST_009"]}]
        issues = audit_case_links(cases, self.targets)
        self.assertEqual([item["字段"] for item in issues], ["关联测试点"])

    def test_blocked_requirement_is_not_a_valid_basis(self):
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_003"],
                  "验收标准": [], "关联测试点": []}]
        issues = audit_case_links(cases, self.targets)
        self.assertEqual(len(issues), 1)
        self.assertIn("blocked", issues[0]["问题"])

    def test_missing_point_baseline_skips_only_the_point_check(self):
        """没有 测试点数据.json 时不能把"无法核对"当作"核对通过"，但也不能误报。"""
        (self.root / "测试点数据.json").unlink()
        targets = load_link_targets(self.root)
        self.assertFalse(targets["has_points"])
        cases = [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                  "验收标准": ["AC_TST_001_09"], "关联测试点": ["TP_TST_999"]}]
        issues = audit_case_links(cases, targets)
        self.assertEqual([item["字段"] for item in issues], ["验收标准"])

    def test_link_list_accepts_strings_and_rejects_bad_types(self):
        self.assertEqual(link_list("AC_TST_001_01、AC_TST_001_02"),
                         ["AC_TST_001_01", "AC_TST_001_02"])
        self.assertEqual(link_list(None), [])
        with self.assertRaises(ValidationError):
            link_list(3)


class TraceSheetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qa-trace-"))

    def test_trace_sheet_supplies_json_only_fields(self):
        """AC/测试点在契约上不在前 13 列，必须能从 `追溯` 表读回来。"""
        path = self.root / "候选.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                               "验收标准": ["AC_TST_001_01", "AC_TST_001_02"],
                               "关联测试点": ["TP_TST_001"],
                               "场景类型": "边界", "设计方法": ["边界值"]}])
        trace = read_trace_sheet(path)
        self.assertEqual(trace["TC_TST_001"]["验收标准"], ["AC_TST_001_01", "AC_TST_001_02"])
        self.assertEqual(trace["TC_TST_001"]["关联测试点"], ["TP_TST_001"])
        self.assertEqual(trace["TC_TST_001"]["场景类型"], "边界")
        self.assertEqual(trace["TC_TST_001"]["设计方法"], ["边界值"])

    def test_unregistered_marker_is_not_read_as_an_id(self):
        path = self.root / "候选.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": [], "验收标准": [],
                               "关联测试点": [], "场景类型": "", "设计方法": []}])
        trace = read_trace_sheet(path)
        self.assertEqual(trace["TC_TST_001"]["验收标准"], [])
        self.assertEqual(trace["TC_TST_001"]["场景类型"], "")

    def test_missing_trace_sheet_returns_empty_mapping(self):
        path = self.root / "候选.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"]}], with_trace=False)
        self.assertEqual(read_trace_sheet(path), {})


class CheckWorkbookLinksCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qa-workbook-links-"))
        (self.root / "REQ清单.json").write_text(json.dumps(spec_payload(), ensure_ascii=False), encoding="utf-8")
        (self.root / "测试点数据.json").write_text(json.dumps(point_payload(), ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "check_workbook_links.py"), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_valid_workbook_passes(self):
        path = self.root / "合格.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                               "验收标准": ["AC_TST_001_01"], "关联测试点": ["TP_TST_001"],
                               "场景类型": "正常", "设计方法": ["场景法"]}])
        completed = self.run_cli("--module-dir", str(self.root), "--input", str(path))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = (self.root / "编号校验报告.md").read_text(encoding="utf-8")
        self.assertIn("**通过**", report)
        self.assertIn("编号校验数据.json", str(self.root / "编号校验数据.json"))

    def test_workbook_with_nonexistent_ids_fails_loudly(self):
        """核心回归：只有 xlsx、没有 JSON 时，编号也必须被拦住。"""
        path = self.root / "有假编号.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                               "验收标准": ["AC_TST_005_03"], "关联测试点": ["TP_TST_080"],
                               "场景类型": "正常", "设计方法": ["场景法"]}])
        completed = self.run_cli("--module-dir", str(self.root), "--input", str(path))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("编号校验不通过", completed.stderr)
        payload = json.loads((self.root / "编号校验数据.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(sorted(item["编号"] for item in payload["issues"]),
                         ["AC_TST_005_03", "TP_TST_080"])

    def test_workbook_without_trace_sheet_passes_but_reports_unregistered(self):
        path = self.root / "无追溯.xlsx"
        write_workbook(path, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"]}], with_trace=False)
        completed = self.run_cli("--module-dir", str(self.root), "--input", str(path))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads((self.root / "编号校验数据.json").read_text(encoding="utf-8"))
        self.assertFalse(payload["has_trace_sheet"])
        self.assertEqual(payload["unregistered_case_count"], 1)

    def test_json_input_is_audited_too(self):
        path = self.root / "用例数据.json"
        path.write_text(json.dumps({"cases": [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                                               "验收标准": ["AC_TST_001_99"], "关联测试点": []}]},
                                   ensure_ascii=False), encoding="utf-8")
        completed = self.run_cli("--module-dir", str(self.root), "--input", str(path))
        self.assertEqual(completed.returncode, 1)


class CompareDesignsGateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qa-compare-gate-"))
        self.module = self.root / "模块"
        self.module.mkdir()
        (self.module / "REQ清单.json").write_text(json.dumps(spec_payload(), ensure_ascii=False), encoding="utf-8")
        (self.module / "测试点数据.json").write_text(json.dumps(point_payload(), ensure_ascii=False), encoding="utf-8")
        self.bad = self.module / "假编号.xlsx"
        write_workbook(self.bad, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                                   "验收标准": ["AC_TST_001_09"], "关联测试点": ["TP_TST_001"],
                                   "场景类型": "正常", "设计方法": ["场景法"]}])
        self.good = self.module / "合格.xlsx"
        write_workbook(self.good, [{"用例编号": "TC_TST_001", "关联需求": ["REQ_TST_001"],
                                    "验收标准": ["AC_TST_001_01"], "关联测试点": ["TP_TST_001"],
                                    "场景类型": "正常", "设计方法": ["场景法"]}])
        self.good2 = self.module / "合格2.xlsx"
        write_workbook(self.good2, [{"用例编号": "TC_TST_002", "关联需求": ["REQ_TST_002"],
                                     "验收标准": ["AC_TST_002_01"], "关联测试点": ["TP_TST_002"],
                                     "场景类型": "异常", "设计方法": ["错误推测"]}])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "compare_designs.py"), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_xlsx_candidate_is_loaded_with_trace_fields(self):
        """候选工作簿带追溯表时，AC/测试点/场景/方法都要读到，不能一律置空。"""
        design = compare.load_design(self.good, load_link_targets(self.module))
        self.assertEqual(design["cases"][0]["验收标准"], ["AC_TST_001_01"])
        self.assertEqual(design["cases"][0]["关联测试点"], ["TP_TST_001"])
        self.assertEqual(design["cases"][0]["场景类型"], "正常")
        self.assertEqual(design["link_issues"], [])

    def test_candidate_with_nonexistent_ids_refuses_to_compare_by_default(self):
        completed = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.bad),
                                 "--alternative", str(self.good), "--out-dir", str(self.module))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("不存在的编号", completed.stderr + completed.stdout)

    def test_allow_link_issues_reports_the_gate_first(self):
        completed = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.bad),
                                 "--alternative", str(self.good), "--out-dir", str(self.module),
                                 "--allow-link-issues")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = (self.module / "设计对比报告.md").read_text(encoding="utf-8")
        self.assertIn("关联编号门禁", report)
        self.assertIn("**不通过**", report)
        self.assertIn("AC_TST_001_09", report)
        # 门禁必须排在所有得分之前。
        self.assertLess(report.index("关联编号门禁"), report.index("加权总分"))
        data = json.loads((self.module / "设计对比数据.json").read_text(encoding="utf-8"))
        self.assertTrue(data["link_gate_overridden"])

    def test_clean_candidate_has_no_issues_and_passes_the_gate(self):
        completed = self.run_cli("--module-dir", str(self.module), "--baseline", str(self.good),
                                 "--alternative", str(self.good2), "--out-dir", str(self.module))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = (self.module / "设计对比报告.md").read_text(encoding="utf-8")
        self.assertIn("关联编号门禁", report)
        self.assertNotIn("**不通过**", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
