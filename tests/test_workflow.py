"""Run from any directory: python tests/test_workflow.py [--xlsx]."""

import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from qa_cases import (AUTOMATION_ADVICES, HEADERS, ValidationError, coverage_data,
                      merged_precondition, validate_cases)
from render_xlsx import extra_sheets
from update_review import render_report, score_review, validate_review
from review_context import review_fingerprints
XLSX = "--xlsx" in sys.argv
if XLSX:
    sys.argv.remove("--xlsx")
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_cells(path):
    """Read actual exported values independently of the authoring library."""
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            tree = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(n.itertext()) for n in tree.findall("s:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        result = {}
        for cell in sheet.findall(".//s:sheetData/s:row/s:c", NS):
            value = cell.find("s:v", NS)
            text = value.text if value is not None else ""
            if cell.get("t") == "s":
                text = strings[int(text)]
            elif cell.get("t") == "inlineStr":
                text = "".join(n.text or "" for n in cell.findall("s:is//s:t", NS))
            result[cell.get("r")] = (text or "", cell.find("s:f", NS) is not None)
        return result, sheet


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-skills-test-")
        self.example = Path(self.temp.name) / "example"
        shutil.copytree(PACKAGE / "example", self.example)
        self.module = self.example / "output" / "商品管理"

    def tearDown(self):
        self.temp.cleanup()

    def edit_json(self, name, callback):
        path = self.module / name
        data = json.loads(path.read_text(encoding="utf-8"))
        callback(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_cli(self, skill, script, *args, ok=True):
        command = [sys.executable, str(PACKAGE / "modules" / skill / "scripts" / script), *map(str, args)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=90)
        message = result.stdout + result.stderr
        self.assertNotIn("Traceback", message, message)
        if ok:
            self.assertEqual(result.returncode, 0, message)
        else:
            self.assertNotEqual(result.returncode, 0, message)
        return message

    def spec(self, ok=True):
        return self.run_cli("req-processor", "validate_spec.py", "--module-dir", self.module, ok=ok)

    def coverage(self, ok=True):
        return self.run_cli("testcase-builder", "check_coverage.py", "--module-dir", self.module, ok=ok)

    def test_example_complete_workflow(self):
        original = (self.example / "input" / "新增商品.md").read_bytes()
        self.run_cli("req-processor", "check_parse.py", "--md", self.module / "解析.md")
        self.run_cli("req-processor", "check_split.py", "--root", self.example / "output")
        self.spec()
        self.coverage()
        report = (self.module / "需求覆盖率报告.md").read_text(encoding="utf-8")
        self.assertIn("100", report)
        self.assertIn("验收", report)
        self.assertEqual(original, (self.example / "input" / "新增商品.md").read_bytes())

    def test_duplicate_requirement_rejected(self):
        self.edit_json("REQ清单.json", lambda data: data["requirements"].append(data["requirements"][0]))
        self.spec(ok=False)

    def test_broken_sibling_module_does_not_block_validation(self):
        """同级里半写坏的模块只影响它自己，不能锁死当前模块的校验。"""
        sibling = self.example / "output" / "订单"
        sibling.mkdir()
        (sibling / "REQ清单.json").write_text("{ 半写坏", encoding="utf-8")
        self.spec()

    def test_all_requirements_deprecated_still_reports_not_designable(self):
        """需求全部作废后，模块里残留的旧评审不能把覆盖率门禁卡死。"""
        self.edit_json("REQ清单.json", lambda data: [req.update(status="deprecated") for req in data["requirements"]])
        (self.module / "评审结果.json").write_text("{}", encoding="utf-8")
        message = self.run_cli("testcase-builder", "check_coverage.py", "--module-dir", self.module, ok=False)
        self.assertIn("not_designable", message)
        self.assertIn("不可设计", (self.module / "需求覆盖率报告.md").read_text(encoding="utf-8"))

    def test_bundle_reference_targets_exist(self):
        for path in (PACKAGE / "modules").rglob("*.md"):
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if "://" not in link and not link.startswith("#"):
                    self.assertTrue((path.parent / link.split("#", 1)[0]).exists(), f"{path}: {link}")

    def test_documented_column_contract_matches_code(self):
        """列契约变动后必须同步文档，堵住“代码改了文档没改”的漂移。"""
        self.assertEqual(len(HEADERS), 13, "用例表列数变化后需同步文档、契约与本节断言")
        contract = (PACKAGE / "modules/req-processor/references/data-contract.md").read_text(encoding="utf-8")
        template = (PACKAGE / "modules/testcase-builder/references/case-template.md").read_text(encoding="utf-8")
        skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
        readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
        for name in HEADERS:
            self.assertIn(name, contract, f"数据契约未登记列：{name}")
            self.assertIn(name, template, f"用例模板未登记列：{name}")
        # README 曾被漏扫：正文残留“六值枚举”，而代码已八值，照 README 填会被校验器拒绝。
        for advice in AUTOMATION_ADVICES:
            self.assertIn(advice, readme, f"README 未登记自动化建议取值：{advice}")
        numerals = {2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
        count_word = numerals.get(len(AUTOMATION_ADVICES))
        self.assertIsNotNone(count_word, "自动化建议取值个数超出本测试的中文数字表，请同步")
        # 取值必须按代码顺序集中出现在“值枚举”标记之后：只要求串出现会被文档里
        # 别处的顺带提及蒙混过关（曾实测：删掉枚举列表仍能通过），故做有序子序列检查。
        for label, text in (("数据契约", contract), ("SKILL.md", skill), ("README.md", readme)):
            self.assertIn(f"{count_word}值枚举", text,
                          f"{label} 未声明自动化建议有 {len(AUTOMATION_ADVICES)} 个取值")
            span = text[text.find("值枚举"):]
            positions = [span.find(advice) for advice in AUTOMATION_ADVICES]
            self.assertNotIn(-1, positions,
                             f"{label} 在“值枚举”之后未列全取值，缺："
                             f"{[a for a, i in zip(AUTOMATION_ADVICES, positions) if i < 0]}")
            self.assertEqual(positions, sorted(positions),
                             f"{label} 的取值枚举顺序与代码不一致")
        review_column = len(HEADERS) + 1
        for relative in ("modules/req-processor/references/data-contract.md",
                         "modules/testcase-builder/references/review-rules.md",
                         "modules/testcase-builder/instructions.md",
                         "SKILL.md"):
            text = (PACKAGE / relative).read_text(encoding="utf-8")
            self.assertRegex(text, rf"第\s*{review_column}\s*列",
                             f"{relative} 未写明评审列是第 {review_column} 列")
        stale = ("前 11 列", "前11列", "为 11 列", "为11列", "11列用例")
        # 枚举个数变更后，旧的中文“N值枚举”写法同样要扫出来：README 曾残留“六值枚举”。
        stale_words = tuple(f"{word}值枚举" for word in numerals.values() if word != count_word)
        # “由第 12 列顺延为第 14 列”这类迁移说明是有效的，要禁的是把旧列号当成现状，故用正则而非子串。
        stale_review = re.compile(r"评审[^。\n]{0,10}第\s*12\s*列|第\s*12\s*列[^。\n]{0,10}评审")
        for path in sorted((PACKAGE / "modules").rglob("*.md")) + [PACKAGE / "SKILL.md", PACKAGE / "README.md"]:
            text = path.read_text(encoding="utf-8")
            for needle in stale + stale_words:
                self.assertNotIn(needle, text,
                                 f"{path.relative_to(PACKAGE)} 残留旧列契约或旧枚举个数：{needle}")
            if f"第 {review_column} 列" in text:
                continue  # 已写明现行评审列号，其余提及属于迁移说明
            match = stale_review.search(text)
            self.assertIsNone(match,
                              f"{path.relative_to(PACKAGE)} 把旧列号当成现行评审列：{match.group(0) if match else ''}")

    def test_shared_preconditions_are_additive_only(self):
        """公共段只追加说明表与合并列，前 13 列的字面内容必须保持逐字不变。"""
        data = json.loads((self.module / "用例数据.json").read_text(encoding="utf-8"))
        # 示例模块本身已登记公共段；先还原成“未登记”的旧资料形态再断言兼容性。
        for key in ("module_preconditions", "evidence_setup"):
            data.pop(key, None)
        cases = validate_cases(data, None, draft=True)
        # 未登记公共段时不再产生公共段说明表，但追溯表始终生成：只交付工作簿时
        # AC、场景类型、设计方法和测试点关联必须仍可读出。
        self.assertEqual([sheet["name"] for sheet in extra_sheets(data, cases, draft=True)], ["追溯"])
        data = dict(data, module_preconditions=["隔离测试服，各用例独立重置账号状态。"],
                    evidence_setup=["事件取证：各观察端独立保存批次。"])
        cases = validate_cases(data, None, draft=True)
        sheets = extra_sheets(data, cases, draft=True)
        self.assertEqual([sheet["name"] for sheet in sheets],
                         ["追溯", "公共前置条件", "取证要求", "合并前置条件"])
        merged = sheets[-1]["rows"]
        self.assertEqual(merged[0], ["用例编号", "用例名称", "合并前置条件（公共段＋本条独有）"])
        self.assertEqual(len(merged), len(cases) + 1)
        for row, case in zip(merged[1:], cases):
            self.assertEqual(row[0], case["用例编号"])
            # 原“前置条件”一字不改地出现在合并结果里，公共段只在前面追加。
            self.assertIn(case["前置条件"].rstrip("。"), row[2])
            self.assertTrue(row[2].startswith("1. 隔离测试服，各用例独立重置账号状态。"))
        self.assertEqual(merged_precondition(data, cases[0]), merged[1][2])

    def test_project_without_automation_framework_can_be_stated(self):
        """没有自动化框架的项目必须能如实填写，不能被枚举逼着谎称通道可用。"""
        self.edit_json("用例数据.json", lambda data: data["cases"][0].update(
            自动化建议="本项目尚无自动化框架",
            自动化建议明细="理由：本项目尚未引入任何自动化框架，无法判定通道。"))
        self.coverage()

    def test_automation_advice_consistency_is_enforced(self):
        self.edit_json("用例数据.json", lambda data: data["cases"][0].update(自动化建议="不建议自动化"))
        self.coverage(ok=False)

    def test_automation_advice_detail_format_is_enforced(self):
        self.edit_json("用例数据.json", lambda data: data["cases"][0].update(自动化建议明细="随便写一句话"))
        self.coverage(ok=False)

    def test_wrong_schema_version_rejected(self):
        self.edit_json("REQ清单.json", lambda data: data.update(schema_version="2.0"))
        self.spec(ok=False)

    def test_unknown_requirement_kind_rejected(self):
        self.edit_json("REQ清单.json", lambda data: data["requirements"][0].update(kind="invented-kind"))
        self.spec(ok=False)

    def test_module_code_must_match_split_mapping(self):
        path = self.example / "output" / "_公共" / "拆分清单.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["modules"][0]["code"] = "OTH"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.spec(ok=False)

    def test_duplicate_case_rejected(self):
        self.edit_json("用例数据.json", lambda data: data["cases"].append(data["cases"][0]))
        self.coverage(ok=False)

    def test_duplicate_acceptance_criterion_rejected(self):
        def change(data):
            criteria = data["requirements"][0]["acceptance_criteria"]
            criteria.append(criteria[0])
        self.edit_json("REQ清单.json", change)
        self.spec(ok=False)

    def test_false_source_quote_rejected(self):
        self.edit_json("REQ清单.json", lambda data: data["requirements"][0]["source"].update(quote="名称必须唯一。"))
        self.spec(ok=False)

    def test_missing_image_rejected(self):
        path = self.module / "解析.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n![流程图](images/missing.png)\n", encoding="utf-8")
        self.run_cli("req-processor", "check_parse.py", "--md", path, ok=False)

    def test_split_dropped_lines_rejected(self):
        path = self.example / "output" / "_公共" / "拆分清单.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sources"][0]["segments"][0]["lines"] = [1, 17]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.run_cli("req-processor", "check_split.py", "--root", self.example / "output", ok=False)

    def test_split_changed_business_rule_rejected(self):
        path = self.module / "解析.md"
        path.write_text(path.read_text(encoding="utf-8").replace("大于0", "大于1"), encoding="utf-8")
        self.run_cli("req-processor", "check_split.py", "--root", self.example / "output", ok=False)

    def test_uncovered_requirement_rejected(self):
        self.edit_json("用例数据.json", lambda data: data.update(cases=[c for c in data["cases"] if c["用例编号"] != "TC_PRO_002"]))
        self.coverage(ok=False)

    def test_uncovered_acceptance_criterion_rejected(self):
        def change(data):
            for case in data["cases"]:
                case["验收标准"] = [ac for ac in case["验收标准"] if ac != "AC_PRO_005_02"]
        self.edit_json("用例数据.json", change)
        self.coverage(ok=False)

    def test_unknown_or_wrong_requirement_criterion_rejected(self):
        self.edit_json("用例数据.json", lambda data: data["cases"][1].update(验收标准=["AC_PRO_002_01"]))
        self.coverage(ok=False)

    def test_p0_requires_blocked_status(self):
        def change(data):
            data["questions"].append({"id": "Q_PRO_002", "priority": "P0", "status": "open", "text": "名称校验规则冲突。", "affected_requirements": ["REQ_PRO_001"], "resolution": ""})
        self.edit_json("REQ清单.json", change)
        self.spec(ok=False)

    def test_p0_only_blocks_affected_requirement(self):
        def change(data):
            data["requirements"][0]["status"] = "blocked"
            data["questions"].append({"id": "Q_PRO_002", "priority": "P0", "status": "open", "text": "名称校验规则冲突。", "affected_requirements": ["REQ_PRO_001"], "resolution": ""})
        self.edit_json("REQ清单.json", change)
        self.spec()
        self.coverage(ok=False)
        self.edit_json("用例数据.json", lambda data: data.update(cases=[c for c in data["cases"] if c["用例编号"] != "TC_PRO_002"]))
        self.coverage()
        report = (self.module / "需求覆盖率报告.md").read_text(encoding="utf-8")
        self.assertIn("REQ_PRO_001", report)

    def test_no_active_scope_does_not_claim_full_coverage(self):
        def change(data):
            for req in data["requirements"]:
                req["status"] = "blocked"
            data["questions"].append({"id": "Q_PRO_002", "priority": "P0", "status": "open", "text": "整个模块的业务规则冲突。", "affected_requirements": [], "resolution": ""})
        self.edit_json("REQ清单.json", change)
        self.spec()
        self.coverage(ok=False)

    def test_blocked_scope_reports_without_a_case_file(self):
        self.edit_json("REQ清单.json", lambda data: [req.update(status="blocked") for req in data["requirements"]])
        (self.module / "用例数据.json").unlink()
        self.coverage(ok=False)
        data = json.loads((self.module / "覆盖率数据.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "not_designable")
        self.assertIsNone(data["requirement_coverage"]["rate"])
        self.assertEqual(data["case_count"], 0)

    def review_fixture(self):
        spec = json.loads((self.module / "REQ清单.json").read_text(encoding="utf-8"))
        cases = validate_cases(json.loads((self.module / "用例数据.json").read_text(encoding="utf-8")), spec)
        review = json.loads((self.module / "评审数据.json").read_text(encoding="utf-8"))
        review["reviewed_source"] = review_fingerprints(spec, cases, self.module)
        review['reviewer'] = 'Workflow test author reviewing the current fixture'
        return spec, cases, review

    def test_high_score_does_not_hide_unconfirmed_case(self):
        spec, cases, review = self.review_fixture()
        reviews, scopes, methods = validate_review(review, cases, spec, self.module)
        coverage = coverage_data(spec, cases)
        score = score_review(coverage, reviews, scopes, methods)
        self.assertEqual(score["total"], 97)
        self.assertGreaterEqual(score["total"], 90)
        self.assertNotIn("grade", score, "不应再产出字母等级")
        self.assertFalse(score["ready"])
        self.assertIn("不能宣称全部用例可执行", render_report(spec["module"], coverage, reviews, scopes, methods, score))

    def test_review_requires_exactly_one_verdict_per_case(self):
        spec, cases, review = self.review_fixture()
        review["reviews"].append(review["reviews"][0])
        with self.assertRaisesRegex(ValidationError, "重复评审"):
            validate_review(review, cases, spec, self.module)

    def test_review_cannot_skip_a_case(self):
        spec, cases, review = self.review_fixture()
        review["reviews"].pop()
        with self.assertRaisesRegex(ValidationError, "缺少"):
            validate_review(review, cases, spec, self.module)

    def test_review_evidence_must_match_scene(self):
        spec, cases, review = self.review_fixture()
        review["scope_checks"][2]["evidence"] = ["TC_PRO_001"]
        with self.assertRaisesRegex(ValidationError, "元数据不匹配"):
            validate_review(review, cases, spec, self.module)

    def test_review_cannot_claim_present_scene_not_applicable(self):
        spec, cases, review = self.review_fixture()
        review["scope_checks"][0].update(status="not_applicable", evidence=[], reason="没有设计。")
        with self.assertRaisesRegex(ValidationError, "不能标记not_applicable"):
            validate_review(review, cases, spec, self.module)

    @unittest.skipUnless(XLSX, "Excel integration: pass --xlsx with the artifact-tool runtime available")
    def test_xlsx_review_preserves_source_and_literal_text(self):
        self.edit_json("用例数据.json", lambda data: data["cases"][0].update(用例名称="=1+1"))
        original = self.module / "详细用例.xlsx"
        self.run_cli("testcase-builder", "render_xlsx.py", "--module-dir", self.module)
        digest = hashlib.sha256(original.read_bytes()).digest()
        before, sheet = read_cells(original)
        self.assertIn(before["C2"][0], ("=1+1", "'=1+1"))
        self.assertFalse(before["C2"][1], "Literal case title must not become a formula")
        self.assertEqual(before["M1"][0], "自动化脚本")
        pane = sheet.find("s:sheetViews/s:sheetView/s:pane", NS)
        self.assertIsNotNone(pane, "Header must stay visible while scrolling")
        # This test authors a fresh review of the current literal title, rather than
        # silently publishing an old packaged review after changing the design.
        spec, cases, review = self.review_fixture()
        (self.module / "评审数据.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        self.run_cli("testcase-builder", "update_review.py", "--module-dir", self.module, "--review", self.module / "评审数据.json")
        self.assertEqual(digest, hashlib.sha256(original.read_bytes()).digest())
        after, _ = read_cells(self.module / "详细用例_已评审.xlsx")
        for address, value in before.items():
            column = "".join(c for c in address if c.isalpha())
            if len(column) == 1 and "A" <= column <= "M":
                self.assertEqual(value, after.get(address, ("", False)), address)
        self.assertEqual(after["N1"][0], "AI评审")
        self.assertIn("需确认", after["N3"][0])
        report = (self.module / "评审报告.md").read_text(encoding="utf-8")
        self.assertIn("TC_PRO_002", report)
        self.assertIn("需确认", report)
        # 输出顺序固定：关联覆盖门禁 → 阻塞项 → 结构自检得分；且不得出现字母等级
        self.assertLess(report.index("关联覆盖门禁"), report.index("结构自检得分"))
        self.assertLess(report.index("阻塞项"), report.index("结构自检得分"))
        self.assertNotIn("等级", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
