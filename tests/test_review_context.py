"""Review bindings must track design/evidence changes and preserve source data."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from qa_cases import ValidationError
from prepare_review import create_template
from review_context import (build_review_template, review_fingerprints,
                            validate_review_context)


class ReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-review-context-")
        self.project = Path(self.temp.name) / "example"
        shutil.copytree(PACKAGE / "example", self.project)
        self.module = self.project / "output" / "商品管理"
        self.spec = json.loads((self.module / "REQ清单.json").read_text(encoding="utf-8"))
        self.cases = json.loads((self.module / "用例数据.json").read_text(encoding="utf-8"))["cases"]
        self.environment = patch.dict(os.environ)
        self.environment.start()
        os.environ.pop("QA_FRAMEWORK_ROOT", None)

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def review(self, module_dir=None):
        data = build_review_template(self.spec, self.cases, module_dir)
        data["draft"] = False
        data["reviews"] = [{"用例编号": row["用例编号"], "结论": "通过",
                            "原因": "已核对当前规则与步骤。", "建议": "无。"}
                           for row in self.cases]
        return data

    def test_changed_expected_with_same_id_rejects_old_judgments(self):
        review = self.review()
        changed = copy.deepcopy(self.cases)
        changed[0]["预期结果"] = ["已改变的预期"]
        self.assertEqual(changed[0]["用例编号"], self.cases[0]["用例编号"])
        with self.assertRaisesRegex(ValidationError, "过期"):
            validate_review_context(review, self.spec, changed)

    def test_changed_requirement_question_and_source_state_are_bound(self):
        review = self.review()
        for mutate in (
            lambda data: data["requirements"][0].update(description="新业务规则"),
            lambda data: data["requirements"][0]["source"].update(lines=[1, 2]),
            lambda data: data["questions"][0].update(status="resolved", resolution="长度为20。"),
        ):
            changed = copy.deepcopy(self.spec)
            mutate(changed)
            with self.assertRaises(ValidationError):
                validate_review_context(review, changed, self.cases)

    def test_actual_source_content_changes_even_when_req_json_unchanged(self):
        review = self.review(self.module)
        source = self.project / "input" / "新增商品.md"
        source.write_text(source.read_text(encoding="utf-8") + "\n新规则。\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "过期"):
            validate_review_context(review, self.spec, self.cases, self.module)

    def test_source_image_and_original_document_contents_are_bound(self):
        image = self.module / "images" / "rule.png"
        image.parent.mkdir()
        image.write_bytes(b"original rule image")
        self.spec["images"] = [{"path": "images/rule.png", "description": "规则截图",
                                "transcription": "名称必填。"}]
        self.spec["requirements"][0]["source"]["images"] = ["images/rule.png"]
        original = self.project / "input" / "original.docx"
        original.write_bytes(b"original document content")
        manifest = {"schema_version": "1.0", "original": {"file": "input/original.docx"},
                    "draft": "input/新增商品.md"}
        (self.module / "解析来源.json").write_text(json.dumps(manifest), encoding="utf-8")
        review = self.review(self.module)
        original.write_bytes(b"changed original document content")
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)
        review = self.review(self.module)
        image.write_bytes(b"changed rule image")
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)

    def test_json_formatting_and_stable_id_order_do_not_expire_review(self):
        review = self.review(self.module)
        spec = json.loads(json.dumps(self.spec, ensure_ascii=False, indent=7, sort_keys=True))
        spec["requirements"].reverse()
        spec["requirements"][1]["acceptance_criteria"].reverse()
        cases = list(reversed(copy.deepcopy(self.cases)))
        for row in cases:
            row["关联需求"].reverse()
            row["验收标准"].reverse()
            row["设计方法"].reverse()
        validate_review_context(review, spec, cases, self.module)

    def test_execution_and_script_records_may_be_filled(self):
        review = self.review()
        changed = copy.deepcopy(self.cases)
        changed[0]["实际结果"] = "执行后由人员填写。"
        changed[0]["自动化脚本"] = "tests/example.py"
        changed[0]["_computed_ready"] = True
        validate_review_context(review, self.spec, changed)

    def test_unbound_completed_review_is_rejected_without_mutation(self):
        review = self.review()
        del review["reviewed_source"]
        before = copy.deepcopy(review)
        with self.assertRaisesRegex(ValidationError, "不能给旧结论补签"):
            validate_review_context(review, self.spec, self.cases)
        self.assertEqual(review, before)

    def test_missing_source_and_escaping_provenance_are_rejected(self):
        outside = Path(self.temp.name) / "external.md"
        outside.write_text("商品名称不能为空。", encoding="utf-8")
        for source in ("../../external.md", str(outside), "../input/missing.md"):
            changed = copy.deepcopy(self.spec)
            changed["requirements"][0]["source"]["file"] = source
            with self.assertRaises(ValidationError):
                review_fingerprints(changed, self.cases, self.module)
        link = self.project / "input" / "outside-link.md"
        link.symlink_to(outside)
        changed["requirements"][0]["source"]["file"] = "../input/outside-link.md"
        with self.assertRaises(ValidationError):
            review_fingerprints(changed, self.cases, self.module)

    def framework(self):
        root = self.project / "framework"
        root.mkdir()
        code = root / "api.py"
        code.write_text("def observe():\n    return 1\n", encoding="utf-8")
        asset = root / "button.png"
        asset.write_bytes(b"referenced image")
        manifest = {"schema_version": "1.0", "framework_root": str(root),
                    "reviewer": "reviewer", "checked_at": "2026-10-08",
                    "capabilities": [
                        {"id": "api.observe", "kind": "function", "file": "api.py",
                         "symbol": "observe", "sha256": hashlib.sha256(code.read_bytes()).hexdigest(), "notes": "只读观测。"},
                        {"id": "ui.button", "kind": "asset", "file": "button.png",
                         "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(), "notes": "按钮参考。"}]}
        path = self.module / "框架能力清单.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self.cases[0]["automation_evidence"] = ["api.observe", "ui.button"]
        return root, path, manifest

    def test_changed_manifest_and_actual_framework_files_expire_review(self):
        root, manifest_path, manifest = self.framework()
        review = self.review(self.module)
        manifest["capabilities"][0]["notes"] = "改变了能力说明。"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)
        review = self.review(self.module)
        (root / "api.py").write_text("def observe():\n    return 2\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)
        review = self.review(self.module)
        (root / "button.png").write_bytes(b"changed referenced image")
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)

    def test_framework_override_hashes_real_files_in_selected_root(self):
        root, _, _ = self.framework()
        review = self.review(self.module)
        new_root = self.project / "new-framework"
        shutil.copytree(root, new_root)
        (new_root / "api.py").write_text("def observe():\n    return 3\n", encoding="utf-8")
        os.environ["QA_FRAMEWORK_ROOT"] = str(new_root)
        with self.assertRaises(ValidationError):
            validate_review_context(review, self.spec, self.cases, self.module)

    def test_template_contains_only_current_binding_and_empty_judgments(self):
        form = build_review_template(self.spec, self.cases, self.module)
        self.assertTrue(form["draft"])
        self.assertEqual(form["reviews"], [])
        self.assertEqual(form["scope_checks"], [])
        self.assertEqual(form["method_checks"], [])
        self.assertEqual(form["reviewer"], "")
        self.assertEqual(form["reviewed_source"], review_fingerprints(self.spec, self.cases, self.module))

    def test_prepare_creates_fresh_form_and_preserves_input_bytes(self):
        names = ("REQ清单.json", "用例数据.json", "评审数据.json")
        before = {name: (self.module / name).read_bytes() for name in names}
        output = create_template(self.module)
        form = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(form["reviews"], [])
        self.assertTrue(form["draft"])
        self.assertEqual(before, {name: (self.module / name).read_bytes() for name in names})

    def test_prepare_refuses_existing_or_protected_output_and_keeps_it(self):
        create_template(self.module)
        targets = [self.module / "评审待填写.json", self.module / "REQ清单.json",
                   self.module / "用例数据.json", self.module / "需求规范.md",
                   self.project / "input" / "新增商品.md"]
        for target in targets:
            before = target.read_bytes()
            with self.assertRaises(ValidationError):
                create_template(self.module, target)
            self.assertEqual(target.read_bytes(), before)

    def test_publish_race_does_not_overwrite_another_file(self):
        target = self.module / "new-review.json"
        original_link = os.link

        def concurrent_create(source, destination):
            Path(destination).write_text("another writer", encoding="utf-8")
            return original_link(source, destination)

        with patch("prepare_review.os.link", side_effect=concurrent_create):
            with self.assertRaisesRegex(ValidationError, "未覆盖"):
                create_template(self.module, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "another writer")
        self.assertEqual(list(target.parent.glob(".qa-review-*")), [])


if __name__ == "__main__":
    unittest.main()
