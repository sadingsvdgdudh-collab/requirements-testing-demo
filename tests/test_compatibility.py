"""Real legacy-schema and spreadsheet import checks; original files stay intact."""

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "modules" / "compatibility" / "scripts" / "import_material.py"


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-import-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        shutil.copytree(PACKAGE / "example", self.project)
        self.module = self.project / "output" / "商品管理"
        self.new_module = self.root / "new-project" / "output" / "商品管理"

    def tearDown(self):
        self.temp.cleanup()

    def json_file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def cli(self, path, kind, *extra, module=None, ok=True):
        module = module or self.module
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path), "--module-dir", str(module),
                                 "--kind", kind, "--code", "PRO", *map(str, extra)], capture_output=True, text=True, timeout=30)
        message = result.stdout + result.stderr
        self.assertNotIn("Traceback", message, message)
        self.assertEqual(result.returncode, 0 if ok else 1, message)
        return message

    def batch(self, module=None):
        return sorted(path for path in ((module or self.module) / "imports").iterdir() if path.is_dir())[-1]

    def legacy_req(self):
        return {"chapter": {"chapter_number": 3, "chapter_title": "商品管理", "total_requirements": 1},
                "modules": [{"module_name": "商品管理", "requirement_count": 1, "requirements": [
                    {"requirement_id": "REQ-CH3-001", "requirement_name": "新增商品", "type": "功能需求 (FR)",
                     "source_page": "第 14 页", "source_paragraphs": ["Para-014-001"],
                     "description": "管理员可以在商品管理页面新增商品。",
                     "detailed_description": "管理员打开新增商品页面。\n名称为空时阻止保存并提示名称必填。",
                     "acceptance_criteria": ["名称为空时阻止保存并提示名称必填。"], "related_images": []}]}]}

    def import_legacy_req(self):
        source = self.json_file("requirements.json", self.legacy_req())
        mapping = self.json_file("req-mapping.json", {"defaults": {"status": "active"}})
        self.cli(source, "requirements", "--mapping", mapping, "--apply", module=self.new_module)
        return source

    def legacy_point(self):
        return {"testpoints": [{"testpoint_id": "TP-CH3-001-001", "testpoint_name": "名称为空阻止保存",
                                "requirement_id": "REQ-CH3-001", "test_type": "功能测试 (FT)", "priority": "P3",
                                "mapped_criteria": "名称为空时阻止保存并提示名称必填。",
                                "preconditions": ["管理员已打开新增商品页面"], "test_steps": ["清空名称后保存"],
                                "expected_results": ["提示名称必填", "不创建商品"], "test_data": ["名称：空"],
                                "notes": "原测试点备注", "related_images": []}]}

    def test_legacy_chapter_requirements_retain_description_and_need_status(self):
        source = self.json_file("requirements.json", self.legacy_req())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.cli(source, "requirements", module=self.new_module)
        batch = self.batch(self.new_module)
        data = self.read(batch / "REQ清单.json")
        self.assertTrue(data["draft"])
        self.assertEqual(data["requirements"][0]["id"], "REQ_PRO_001")
        self.assertEqual(data["requirements"][0]["kind"], "functional")
        self.assertEqual(data["requirements"][0]["status"], "")
        transcript = (batch / "input" / "导入底稿.md").read_text(encoding="utf-8")
        self.assertIn("管理员打开新增商品页面。\n名称为空时阻止保存并提示名称必填。", transcript)
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertFalse((self.new_module / "REQ清单.json").exists())

    def test_complete_legacy_requirement_publishes_spec_and_real_source_lines(self):
        self.import_legacy_req()
        data = self.read(self.new_module / "REQ清单.json")
        self.assertFalse(data["draft"])
        source = data["requirements"][0]["source"]
        content = (self.new_module.parent / source["file"]).read_text(encoding="utf-8").splitlines()
        self.assertIn(source["quote"], "\n".join(content[source["lines"][0] - 1:source["lines"][1]]))
        self.assertEqual((self.new_module / "需求规范.md").read_text(encoding="utf-8").count("\n## "), 9)

    def test_original_tp_schema_exact_ac_matching_and_priority_p3_kept(self):
        self.import_legacy_req()
        point = self.json_file("testpoints.json", self.legacy_point())
        self.cli(point, "testpoints", module=self.new_module)
        data = self.read(self.batch(self.new_module) / "测试点数据.json")
        row = data["testpoints"][0]
        self.assertEqual(row["优先级"], "P3")
        self.assertEqual(row["验收标准"], ["AC_PRO_001_01"])
        self.assertEqual(row["关联需求"], ["REQ_PRO_001"])
        self.assertEqual(row["场景类型"], "")
        self.assertIn("test_type", row["legacy"]["record"])
        self.assertTrue(data["missing_fields"])

    def test_complete_tp_explicit_design_fields_apply(self):
        self.import_legacy_req()
        raw = self.legacy_point()
        raw["testpoints"][0]["priority"] = "P1"
        point = self.json_file("testpoints.json", raw)
        mapping = self.json_file("tp-mapping.json", {"defaults": {"场景类型": "异常", "设计方法": ["错误推测"]}})
        self.cli(point, "testpoints", "--mapping", mapping, "--apply", module=self.new_module)
        self.assertFalse(self.read(self.new_module / "测试点数据.json")["draft"])

    def test_scalar_criteria_sentence_keeps_delimiters_before_exact_matching(self):
        sentence = "名称为空时，阻止保存；提示名称必填、不能创建商品。"
        requirement = self.legacy_req()
        requirement["modules"][0]["requirements"][0]["acceptance_criteria"] = [sentence]
        source = self.json_file("requirements.json", requirement)
        req_mapping = self.json_file("req-mapping.json", {"defaults": {"status": "active"}})
        self.cli(source, "requirements", "--mapping", req_mapping, "--apply", module=self.new_module)
        raw = self.legacy_point()
        raw["testpoints"][0]["mapped_criteria"] = "  " + sentence + "  "
        point = self.json_file("testpoints.json", raw)
        mapping = self.json_file("tp-mapping.json", {"defaults": {"场景类型": "异常", "设计方法": ["错误推测"]}})
        self.cli(point, "testpoints", "--mapping", mapping, "--apply", module=self.new_module)
        self.assertEqual(self.read(self.new_module / "测试点数据.json")["testpoints"][0]["验收标准"], ["AC_PRO_001_01"])

    def test_scalar_criteria_explicit_mapping_precedes_delimiter_splitting(self):
        self.import_legacy_req()
        raw = self.legacy_point()
        raw["testpoints"][0]["mapped_criteria"] = "旧编号，特殊;标签"
        point = self.json_file("testpoints.json", raw)
        mapping = self.json_file("tp-mapping.json", {"defaults": {"场景类型": "异常", "设计方法": ["错误推测"]},
                                                   "ids": {"AC": {"旧编号，特殊;标签": "AC_PRO_001_01"}}})
        self.cli(point, "testpoints", "--mapping", mapping, "--apply", module=self.new_module)
        self.assertEqual(self.read(self.new_module / "测试点数据.json")["testpoints"][0]["验收标准"], ["AC_PRO_001_01"])

    def test_scalar_req_explicit_mapping_precedes_delimiter_splitting(self):
        """旧 REQ 编号自带标点时，显式 mapping 的整串条目必须优先于拆分。"""
        self.import_legacy_req()
        raw = self.legacy_point()
        raw["testpoints"][0]["requirement_id"] = "旧需求，特殊;标签"
        point = self.json_file("testpoints.json", raw)
        mapping = self.json_file("tp-mapping.json", {"defaults": {"场景类型": "异常", "设计方法": ["错误推测"]},
                                                     "ids": {"REQ": {"旧需求，特殊;标签": "REQ_PRO_001"}}})
        self.cli(point, "testpoints", "--mapping", mapping, "--apply", module=self.new_module)
        self.assertEqual(self.read(self.new_module / "测试点数据.json")["testpoints"][0]["关联需求"], ["REQ_PRO_001"])

    def test_scalar_criteria_id_list_still_splits_without_whole_match(self):
        raw = self.read(self.module / "用例数据.json")["cases"][0]
        raw["验收标准"] = "AC_PRO_005_01，AC_PRO_005_02"
        source = self.json_file("criteria-id-list.json", [raw])
        self.cli(source, "cases")
        data = self.read(self.batch() / "用例数据.json")
        self.assertEqual(data["cases"][0]["验收标准"], ["AC_PRO_005_01", "AC_PRO_005_02"])

    def test_p3_legacy_tp_is_supported_in_formal_data(self):
        self.import_legacy_req()
        point = self.json_file("testpoints.json", self.legacy_point())
        mapping = self.json_file("tp-mapping.json", {"defaults": {"场景类型": "异常", "设计方法": ["错误推测"]}})
        self.cli(point, "testpoints", "--mapping", mapping, "--apply", module=self.new_module)
        self.assertEqual(self.read(self.new_module / "测试点数据.json")["testpoints"][0]["优先级"], "P3")

    def test_only_req_no_ac_is_draft_never_fills_every_ac(self):
        raw = self.read(self.module / "用例数据.json")["cases"][2]
        raw.pop("验收标准")
        source = self.json_file("cases-array.json", [raw])
        self.cli(source, "cases")
        data = self.read(self.batch() / "用例数据.json")
        self.assertEqual(data["cases"][0]["验收标准"], [])
        self.cli(source, "cases", "--apply", "--replace", ok=False)
        self.assertEqual(len(self.read(self.module / "用例数据.json")["cases"]), 10)

    def test_original_object_array_cases_preserve_optional_execution_record(self):
        raw = self.read(self.module / "用例数据.json")["cases"][0]
        raw["实际结果"] = "人工执行：未通过，BUG-001"
        source = self.json_file("cases-array.json", [raw])
        self.cli(source, "cases", "--apply", "--replace")
        self.assertEqual(self.read(self.module / "用例数据.json")["cases"][0]["实际结果"], raw["实际结果"])
        self.assertTrue((self.batch() / "备份" / "用例数据.json").is_file())
        self.assertTrue((self.module / "测试点评审结果.json").exists())
        self.assertFalse((self.module / "详细用例.xlsx").exists())

    def test_case_import_retains_canonical_point_traceability_and_old_alias(self):
        raw = self.read(self.module / "用例数据.json")["cases"][0]
        links = raw["关联测试点"]
        source = self.json_file("canonical-links.json", [raw])
        self.cli(source, "cases", "--apply", "--replace")
        row = self.read(self.module / "用例数据.json")["cases"][0]
        self.assertEqual(row["关联测试点"], links)
        self.assertNotIn("测试点", row)
        raw["测试点"] = raw.pop("关联测试点")
        source = self.json_file("legacy-links.json", [raw])
        self.cli(source, "cases", "--apply", "--replace")
        row = self.read(self.module / "用例数据.json")["cases"][0]
        self.assertEqual(row["关联测试点"], links)

    def test_case_point_unknown_link_does_not_publish(self):
        raw = self.read(self.module / "用例数据.json")["cases"][0]
        raw["关联测试点"] = ["TP_PRO_999"]
        source = self.json_file("unknown-point.json", [raw])
        self.cli(source, "cases", "--apply", "--replace", ok=False)
        self.assertIn("关联未知测试点", (self.batch() / "导入报告.md").read_text(encoding="utf-8"))

    def test_replace_required_and_original_target_never_overwritten(self):
        source = self.json_file("cases.json", self.read(self.module / "用例数据.json"))
        self.cli(source, "cases", "--apply", ok=False)
        original = (self.module / "用例数据.json").read_bytes()
        self.cli(self.module / "用例数据.json", "cases", "--apply", "--replace", ok=False)
        self.assertEqual(original, (self.module / "用例数据.json").read_bytes())

    def test_unknown_criteria_and_wrong_owner_not_formal(self):
        raw = self.read(self.module / "用例数据.json")["cases"][0]
        raw["关联需求"] = ["REQ_PRO_001"]
        raw["验收标准"] = ["AC_PRO_002_01", "AC_PRO_999_01"]
        source = self.json_file("wrong-ac.json", [raw])
        self.cli(source, "cases", "--apply", "--replace", ok=False)
        draft = self.read(self.batch() / "用例数据.json")
        self.assertEqual(draft["cases"][0]["验收标准"], [])
        self.assertIn("不属于", (self.batch() / "导入报告.md").read_text(encoding="utf-8"))

    def test_duplicate_ids_fail_with_clear_report(self):
        raw = self.legacy_req()
        raw["modules"][0]["requirements"] *= 2
        source = self.json_file("duplicate.json", raw)
        self.cli(source, "requirements", module=self.new_module, ok=False)
        self.assertIn("重复原编号", (self.batch(self.new_module) / "导入报告.md").read_text(encoding="utf-8"))

    def test_multi_module_scope_must_match(self):
        raw = self.legacy_req()
        raw["modules"][0]["module_name"] = "其他模块"
        raw["modules"].append({"module_name": "另一个模块", "requirements": []})
        source = self.json_file("multi.json", raw)
        self.cli(source, "requirements", module=self.new_module, ok=False)

    def test_id_mapping_stays_identical_across_draft_batches(self):
        source = self.json_file("requirements.json", self.legacy_req())
        self.cli(source, "requirements", module=self.new_module)
        first = self.read(self.batch(self.new_module) / "编号映射.json")
        self.cli(source, "requirements", module=self.new_module)
        self.assertEqual(first, self.read(self.batch(self.new_module) / "编号映射.json"))
        bad = self.json_file("bad-map.json", {"ids": {"REQ": {"REQ-CH3-001": "REQ_PRO_002"}}})
        self.cli(source, "requirements", "--mapping", bad, module=self.new_module, ok=False)

    def test_nine_column_csv_retains_rows_and_marks_missing_fields(self):
        source = self.root / "nine-columns.csv"
        headers = ["用例编号", "优先级", "用例名称", "前置条件", "操作步骤", "测试数据", "预期结果", "实际结果", "关联需求"]
        row = ["OLD-001", "P3", "名称必填", "已登录", "清空名称后保存", "名称为空", "名称必填且不创建", "BUG-012", "REQ_PRO_001"]
        with source.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([headers, row])
        self.cli(source, "cases")
        data = self.read(self.batch() / "用例数据.json")
        self.assertEqual(data["cases"][0]["实际结果"], "BUG-012")
        self.assertEqual(data["cases"][0]["是否可自动化"], "")
        self.assertEqual(data["cases"][0]["验收标准"], [])
        self.assertEqual(data["cases"][0]["legacy"]["location"], "nine-columns.csv:row:2")
        self.assertTrue(data["draft"])

    def test_english_tsv_can_publish_using_explicit_missing_field_defaults(self):
        source = self.root / "cases.tsv"
        with source.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream, delimiter="\t").writerows([
                ["case_id", "priority", "case_name", "preconditions", "steps", "test_data", "expected", "actual_result", "requirement_id"],
                ["OLD-001", "P1", "名称必填", "已登录", "清空名称后保存", "名称为空", "提示名称必填，不创建商品", "", "REQ_PRO_001"]])
        mapping = self.json_file("case-map.json", {"defaults": {
            "是否可自动化": "待定", "自动化建议": "仅api自动化可用",
            "自动化建议明细": "理由：名称为空的校验结果可由接口断言。\n已有函数/资源：按项目已导入框架填写。",
            "验收标准": ["AC_PRO_001_01"], "场景类型": "异常", "设计方法": ["错误推测"]}})
        self.cli(source, "cases", "--mapping", mapping, "--apply", "--replace")
        self.assertEqual(self.read(self.module / "用例数据.json")["cases"][0]["验收标准"], ["AC_PRO_001_01"])

    def test_ambiguous_headers_and_duplicate_headers_do_not_guess(self):
        source = self.root / "ambiguous.csv"
        source.write_text("case_id,testcase_id,priority\nA,B,P1\n", encoding="utf-8")
        self.cli(source, "cases", ok=False)
        self.assertIn("多个候选", (self.batch() / "导入报告.md").read_text(encoding="utf-8"))
        source.write_text("用例编号,用例编号,优先级\nA,B,P1\n", encoding="utf-8")
        self.cli(source, "cases", ok=False)
        mapping = self.json_file("numeric-map.json", {"fields": {"用例编号": 1, "优先级": 3}})
        self.cli(source, "cases", "--mapping", mapping)
        self.assertEqual(self.read(self.batch() / "用例数据.json")["cases"][0]["legacy"]["record"]["@column2"], "B")

    def test_canonical_and_alias_column_collision_requires_mapping(self):
        source = self.root / "collision.csv"
        source.write_text("用例编号,case_id,优先级\nA,B,P1\n", encoding="utf-8")
        self.cli(source, "cases", ok=False)
        mapping = self.json_file("collision-map.json", {"fields": {"用例编号": "case_id"}})
        self.cli(source, "cases", "--mapping", mapping)
        data = self.read(self.batch() / "用例数据.json")
        self.assertEqual(data["cases"][0]["legacy"]["record"]["用例编号"], "A")

    def test_unknown_schema_version_is_not_silently_interpreted(self):
        data = self.read(self.module / "用例数据.json")
        data["schema_version"] = "9.0"
        source = self.json_file("future.json", data)
        self.assertIn("不支持 schema_version", self.cli(source, "cases", ok=False))

    def test_requirement_images_stay_draft_without_transcription(self):
        raw = self.legacy_req()
        (self.root / "screen.png").write_bytes(b"image-payload-test")
        raw["modules"][0]["requirements"][0]["related_images"] = [{"path": "screen.png", "description": "新增商品截图"}]
        source = self.json_file("req-images.json", raw)
        mapping = self.json_file("req-map.json", {"defaults": {"status": "active"}})
        self.cli(source, "requirements", "--mapping", mapping, "--apply", module=self.new_module, ok=False)
        data = self.read(self.batch(self.new_module) / "REQ清单.json")
        self.assertIn("缺少描述或转录", " ".join(data["requirements"][0]["missing_fields"]))
        self.assertEqual(len(list((self.batch(self.new_module) / "assets").iterdir())), 1)

    def test_canonical_requirement_replace_archives_dependent_business_files(self):
        original_cases = (self.module / "用例数据.json").read_bytes()
        source = self.json_file("canonical-req.json", self.read(self.module / "REQ清单.json"))
        self.cli(source, "requirements", "--apply", "--replace")
        backup = self.batch() / "备份"
        self.assertEqual(original_cases, (backup / "用例数据.json").read_bytes())
        self.assertTrue((backup / "测试点数据.json").exists())
        self.assertFalse((self.module / "用例数据.json").exists())
        self.assertFalse(self.read(self.module / "REQ清单.json")["draft"])

    def test_requirement_import_staging_keeps_existing_parser_review_gate(self):
        evidence = self.project / "input" / "新增商品.md"
        source_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
        manifest = {"schema_version": "1.0", "module": "商品管理",
                    "original": {"file": "input/新增商品.md", "sha256": source_sha, "format": ".md"},
                    "draft": "input/新增商品.md", "draft_sha256": source_sha, "needs_review": True}
        (self.module / "解析来源.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        original = (self.module / "REQ清单.json").read_bytes()
        source = self.json_file("canonical-req.json", self.read(self.module / "REQ清单.json"))
        self.cli(source, "requirements", "--apply", "--replace", ok=False)
        self.assertEqual(original, (self.module / "REQ清单.json").read_bytes())
        self.assertFalse((self.batch() / "备份").exists())
        review = {"schema_version": "1.0", "original_sha256": source_sha, "draft_sha256": source_sha,
                  "status": "confirmed", "reviewer": "测试夹具核对者", "notes": "确认隔离校验读取实际项目来源；非业务评审。", "findings": []}
        (self.module / "解析核对.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        self.cli(source, "requirements", "--apply", "--replace")
        self.assertFalse(self.read(self.module / "REQ清单.json")["draft"])

    def test_requirement_image_markdown_keeps_exact_evidence_and_valid_display(self):
        import base64
        raw = self.legacy_req()
        (self.root / "screen.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="))
        req = raw["modules"][0]["requirements"][0]
        req["detailed_description"] += "\n![新增界面](images/old.png)"
        req["related_images"] = [{"path": "screen.png", "description": "新增商品截图", "transcription": "商品名称输入框和保存按钮。"}]
        source = self.json_file("req-images.json", raw)
        mapping = self.json_file("req-map.json", {"defaults": {"status": "active"}})
        self.cli(source, "requirements", "--mapping", mapping, "--apply", module=self.new_module)
        current = self.read(self.new_module / "REQ清单.json")
        self.assertIn("![新增界面](images/old.png)", current["requirements"][0]["source"]["quote"])
        self.assertEqual(len(list((self.new_module / "images").iterdir())), 1)

    def test_incoming_draft_or_redesign_marker_cannot_be_laundered_by_import(self):
        data = self.read(self.module / "用例数据.json")
        data["draft"] = True
        source = self.json_file("incoming-draft.json", data)
        self.cli(source, "cases", "--apply", "--replace", ok=False)
        data["draft"] = False
        data["cases"][0]["needs_redesign"] = True
        source = self.json_file("incoming-redesign.json", data)
        self.cli(source, "cases", "--apply", "--replace", ok=False)

    def test_row_draft_or_pending_evidence_cannot_be_laundered_into_legacy(self):
        for marker in ({"draft": True}, {"update_state": "needs_evidence_check"}, {"update_state": "pending"},
                       {"missing_fields": ["尚未核对原文"]}):
            with self.subTest(marker=marker):
                raw = self.read(self.module / "用例数据.json")["cases"][0]
                raw.update(marker)
                source = self.json_file("row-pending.json", [raw])
                self.cli(source, "cases", "--apply", "--replace", ok=False)
                draft = self.read(self.batch() / "用例数据.json")
                self.assertTrue(draft["missing_fields"])
                self.assertEqual(len(self.read(self.module / "用例数据.json")["cases"]), 10)

    def test_root_pending_and_requirement_row_draft_require_resolution(self):
        raw = self.read(self.module / "用例数据.json")
        raw.update(draft=False, update_state="pending")
        source = self.json_file("root-pending.json", raw)
        self.cli(source, "cases", "--apply", "--replace", ok=False)
        requirement = self.legacy_req()
        requirement["modules"][0]["requirements"][0]["draft"] = True
        source = self.json_file("req-draft-row.json", requirement)
        mapping = self.json_file("req-map.json", {"defaults": {"status": "active"}})
        self.cli(source, "requirements", "--mapping", mapping, "--apply", module=self.new_module, ok=False)
        self.assertFalse((self.new_module / "REQ清单.json").exists())

    def test_unreadable_xlsx_returns_clear_error_not_stack_trace(self):
        source = self.root / "broken.xlsx"
        source.write_bytes(b"not-a-zip")
        self.cli(source, "cases", ok=False)
        self.assertTrue((self.batch() / "导入报告.md").exists())

    def test_draft_requirement_is_not_usable_test_import_context(self):
        data = self.read(self.module / "REQ清单.json")
        data["draft"] = True
        (self.module / "REQ清单.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        source = self.json_file("points.json", self.legacy_point())
        self.cli(source, "testpoints", ok=False)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "工作区Python需提供openpyxl进行只读XLSX验收")
    def test_real_xlsx_sheet_selection_and_column_mapping(self):
        source = self.root / "old.xlsx"
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        worksheet = ET.Element("worksheet", xmlns=ns)
        body = ET.SubElement(worksheet, "sheetData")
        for row_number, values in enumerate([["Point ID", "Priority", "Name"], ["POINT-001", "P3", "价格为0"]], 1):
            row = ET.SubElement(body, "row", r=str(row_number))
            for col, value in enumerate(values):
                cell = ET.SubElement(row, "c", r=f"{chr(65 + col)}{row_number}", t="inlineStr")
                ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="旧测试点" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(worksheet))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        mapping = self.json_file("xlsx-map.json", {"fields": {"测试点编号": "Point ID", "优先级": "Priority", "测试点名称": "Name"}})
        self.cli(source, "testpoints", "--sheet", "旧测试点", "--mapping", mapping)
        row = self.read(self.batch() / "测试点数据.json")["testpoints"][0]
        self.assertEqual(row["优先级"], "P3")
        self.assertEqual(row["测试点名称"], "价格为0")
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
