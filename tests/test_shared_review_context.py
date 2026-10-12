"""Public setup is part of the reviewed design, including at publication time."""

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules/testcase-builder/scripts"))
import check_coverage
import prepare_review
import update_review
from qa_cases import ValidationError, case_matrix, validate_cases, validate_spec
from review_context import (build_review_template, review_fingerprints,
                            validate_review_context)


class SharedReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-shared-review-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(PACKAGE / "example", self.project)
        self.module = self.project / "output/商品管理"
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("QA_FRAMEWORK_ROOT", None)
        self.spec = validate_spec(self.module)
        self.data = self.read("用例数据.json")
        self.cases = validate_cases(self.data, self.spec, module_dir=self.module)

    def read(self, name):
        return json.loads((self.module / name).read_text(encoding="utf-8"))

    def write(self, name, data):
        (self.module / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def fresh_review(self):
        form = build_review_template(self.spec, self.cases, self.module)
        old = self.read("评审数据.json")
        form.update({key: old[key] for key in ("reviews", "scope_checks", "method_checks")})
        form.update(draft=False, reviewer="Regression author reviewing the current fixture")
        self.write("评审数据.json", form)
        return form

    def change_shared_setup(self, field):
        data = self.read("用例数据.json")
        data[field] = (["未登录后台，直接打开新增商品页面。"] if field == "module_preconditions"
                       else ["仅核对数据库，不观察页面提示。"])
        self.write("用例数据.json", data)

    def publication_args(self):
        return ["update_review.py", "--module-dir", str(self.module),
                "--review", str(self.module / "评审数据.json")]

    def test_each_public_setup_change_expires_fresh_review(self):
        for field in ("module_preconditions", "evidence_setup"):
            with self.subTest(field=field):
                self.write("用例数据.json", self.data)
                review = self.fresh_review()
                self.change_shared_setup(field)
                with self.assertRaisesRegex(ValidationError, "过期"):
                    validate_review_context(review, self.spec, self.cases, self.module)

    def test_removing_public_setup_expires_fresh_review(self):
        review = self.fresh_review()
        data = copy.deepcopy(self.data)
        data.pop("module_preconditions")
        data.pop("evidence_setup")
        self.write("用例数据.json", data)
        with self.assertRaisesRegex(ValidationError, "过期"):
            validate_review_context(review, self.spec, self.cases, self.module)

    def test_full_in_memory_design_binds_each_public_setup_field(self):
        review = build_review_template(self.spec, self.data)
        for field in ("module_preconditions", "evidence_setup"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.data)
                changed[field] = ["与评审时不同的公共条件。"]
                with self.assertRaisesRegex(ValidationError, "过期"):
                    validate_review_context(review, self.spec, changed)

    def test_coverage_entry_rejects_old_public_setup_review(self):
        self.fresh_review()
        self.change_shared_setup("module_preconditions")
        args = ["check_coverage.py", "--module-dir", str(self.module),
                "--review", str(self.module / "评审数据.json")]
        with patch.object(sys, "argv", args), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_coverage.main(), 1)

    def test_publication_rejects_old_public_setup_before_export(self):
        self.fresh_review()
        self.change_shared_setup("evidence_setup")
        with patch.object(sys, "argv", self.publication_args()), \
                patch.object(update_review, "run_xlsx") as export, \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update_review.main(), 1)
            export.assert_not_called()

    def test_public_setup_change_during_export_is_rejected(self):
        self.fresh_review()
        snapshot = {"name": "用例", "rows": case_matrix(self.cases)}

        def raced_export(payload, output, *, publication_guard):
            self.change_shared_setup("module_preconditions")
            publication_guard()

        with patch.object(sys, "argv", self.publication_args()), \
                patch.object(update_review, "read_xlsx", return_value=snapshot), \
                patch.object(update_review, "run_xlsx", side_effect=raced_export), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update_review.main(), 1)

    def test_changed_setup_is_not_adopted_as_new_publication_baseline(self):
        self.fresh_review()
        snapshot = {"name": "用例", "rows": case_matrix(self.cases)}
        original_score = update_review.score_review

        def change_after_validation(*args, **kwargs):
            self.change_shared_setup("evidence_setup")
            return original_score(*args, **kwargs)

        def guarded_export(payload, output, *, publication_guard):
            publication_guard()

        with patch.object(sys, "argv", self.publication_args()), \
                patch.object(update_review, "read_xlsx", return_value=snapshot), \
                patch.object(update_review, "score_review", side_effect=change_after_validation), \
                patch.object(update_review, "run_xlsx", side_effect=guarded_export), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update_review.main(), 1)

    def test_prepare_withdraws_form_if_public_setup_changes_during_publish(self):
        destination = self.module / "评审待填写.json"
        original_link = os.link

        def raced_link(source, target):
            original_link(source, target)
            self.change_shared_setup("module_preconditions")

        with patch.object(prepare_review.os, "link", side_effect=raced_link):
            with self.assertRaisesRegex(ValidationError, "输入已改变"):
                prepare_review.create_template(self.module)
        self.assertFalse(destination.exists())

    def test_no_public_setup_retains_legacy_binding(self):
        data = copy.deepcopy(self.data)
        data.pop("module_preconditions")
        data.pop("evidence_setup")
        self.write("用例数据.json", data)
        without_module = review_fingerprints(self.spec, self.cases)
        with_module = review_fingerprints(self.spec, self.cases, self.module)
        self.assertEqual(with_module["cases_sha256"], without_module["cases_sha256"])
        self.assertEqual(set(with_module), {"requirements_sha256", "cases_sha256",
                                            "provenance_sha256", "framework_evidence_sha256"})

    def test_execution_records_do_not_expire_public_setup_binding(self):
        review = self.fresh_review()
        data = copy.deepcopy(self.data)
        data["cases"][0].update(实际结果="已由测试人员填写。", 自动化脚本="tests/product.py")
        self.write("用例数据.json", data)
        cases = validate_cases(data, self.spec, module_dir=self.module)
        validate_review_context(review, self.spec, cases, self.module)

    def test_structured_execution_fields_do_not_expire_design_review(self):
        records = {
            "execution_records": [{"status": "passed", "executed_at": "2026-10-12T08:00:00Z"}],
            "execution_history": [{"result": "passed", "run_id": "run-2"}],
            "runs": [{"id": "run-2", "result": "failed"}],
            "执行记录": [{"结果": "通过", "执行人": "测试人员"}],
            "执行历史": [{"批次": "第二轮", "结果": "失败"}],
            "执行状态": "通过",
            "执行人": "本轮测试人员",
            "执行时间": "2026-10-12T08:00:00Z",
        }
        review = self.fresh_review()
        memory_review = build_review_template(self.spec, self.data)
        for field, value in records.items():
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data["cases"][0][field] = value
                self.write("用例数据.json", data)
                cases = validate_cases(data, self.spec, module_dir=self.module)
                validate_review_context(review, self.spec, cases, self.module)
                validate_review_context(memory_review, self.spec, data)

    def test_execution_exemption_does_not_hide_preparation_changes(self):
        review = build_review_template(self.spec, self.data)
        changed = copy.deepcopy(self.data)
        changed["cases"][0]["preparation"] = [{"id": "PREP_ACCOUNT", "category": "账号",
            "description": "需要新的测试账号。", "action": "创建有商品管理权限的账号。",
            "status": "open", "affects": ["manual", "automation"], "resolution": ""}]
        with self.assertRaisesRegex(ValidationError, "过期"):
            validate_review_context(review, self.spec, changed)


if __name__ == "__main__":
    unittest.main()
