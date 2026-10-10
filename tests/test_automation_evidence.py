"""Read-only capability evidence checks: python tests/test_automation_evidence.py."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from automation_evidence import (evaluate_automation, load_manifest, validate_manifest)
from qa_cases import HEADERS, ValidationError, run_xlsx, validate_cases


class AutomationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-capabilities-")
        self.module = Path(self.temp.name) / "output" / "模块"
        self.module.mkdir(parents=True)
        self.root = self.module / "framework"
        self.root.mkdir()
        self.source = self.root / "client.py"
        # Importing this source would fail, verifying it must never execute it.
        self.source.write_text("raise RuntimeError('must not execute')\nclass Client:\n    def send(self):\n        pass\ndef status():\n    pass\n", encoding="utf-8")
        self.asset = self.root / "assets" / "chat.png"
        self.asset.parent.mkdir()
        self.asset.write_bytes(b"real local asset bytes")
        self.manifest = {
            "schema_version": "1.0", "framework_root": "framework", "reviewer": "QA",
            "checked_at": "2026-10-08T10:00:00+08:00",
            "capabilities": [self.entry("send", "function", "client.py", "Client.send"),
                             self.entry("chat", "asset", "assets/chat.png")],
        }
        example = PACKAGE / "example" / "output" / "商品管理"
        self.data = json.loads((example / "用例数据.json").read_text(encoding="utf-8"))
        self.data["cases"] = [self.data["cases"][0]]
        self.case = self.data["cases"][0]
        self.case.update({"是否可自动化": "是", "自动化建议": "UI自动化可用",
                          "自动化建议明细": "理由：验证聊天。已有函数/资源：Client.send与聊天截图。"})
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.old_override = os.environ.pop("QA_FRAMEWORK_ROOT", None)

    def tearDown(self):
        if self.old_override is not None:
            os.environ["QA_FRAMEWORK_ROOT"] = self.old_override
        self.env.stop()
        self.temp.cleanup()

    def entry(self, cap_id, kind, file, symbol=None):
        result = {"id": cap_id, "kind": kind, "file": file,
                  "sha256": hashlib.sha256((self.root / file).read_bytes()).hexdigest(), "notes": "已静态核对。"}
        if symbol:
            result["symbol"] = symbol
        return result

    def publish(self):
        (self.module / "框架能力清单.json").write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def test_legacy_prose_cannot_claim_verified_or_ready(self):
        self.case.update({"_automation_verified": True, "_automation_ready": True})
        checked = validate_cases(self.data, draft=True, module_dir=self.module)[0]
        self.assertFalse(checked["_automation_verified"])
        self.assertFalse(checked["_automation_ready"])
        self.assertEqual(len(HEADERS), 13)

    def test_real_python_method_asset_and_hash_verify_without_execution(self):
        self.case["automation_evidence"] = ["send", "chat"]
        self.publish()
        checked = validate_cases(self.data, draft=True, module_dir=self.module)[0]
        self.assertTrue(checked["_automation_verified"])
        self.assertTrue(checked["_automation_ready"])

    def test_fake_method_missing_file_and_hash_drift_are_rejected(self):
        self.case["automation_evidence"] = ["send"]
        for mutation in (lambda m: m["capabilities"][0].update(symbol="Client.fake"),
                         lambda m: m["capabilities"][1].update(file="assets/missing.png"),
                         lambda m: m["capabilities"][0].update(sha256="0" * 64),
                         lambda m: m["capabilities"][0].pop("sha256")):
            manifest = deepcopy(self.manifest)
            mutation(manifest)
            with self.assertRaises(ValueError):
                evaluate_automation(self.case, self.module, manifest)
        self.source.write_text("def changed():\n    pass\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要不一致"):
            evaluate_automation(self.case, self.module, self.manifest)

    def test_traversal_absolute_and_symlink_escape_are_rejected(self):
        for file in ("../outside.py", "/tmp/outside.py", "C:/outside.py", "assets/../../outside.py"):
            manifest = deepcopy(self.manifest)
            manifest["capabilities"][0]["file"] = file
            with self.assertRaises(ValueError):
                validate_manifest(manifest, self.module)
        outside = self.module / "outside.py"
        outside.write_text("def outside():\n    pass\n", encoding="utf-8")
        (self.root / "escape.py").symlink_to(outside)
        self.manifest["capabilities"][0]["file"] = "escape.py"
        with self.assertRaisesRegex(ValueError, "逃逸"):
            validate_manifest(self.manifest, self.module)

    def test_unknown_refs_and_missing_manifest_are_rejected(self):
        self.case["automation_evidence"] = ["invented"]
        with self.assertRaisesRegex(ValueError, "未知"):
            evaluate_automation(self.case, self.module, self.manifest)
        with self.assertRaisesRegex(ValueError, "缺少"):
            evaluate_automation(self.case, self.module)

    def test_ui_usable_needs_real_asset_and_missing_asset_needs_file_path(self):
        self.case["automation_evidence"] = ["send"]
        with self.assertRaisesRegex(ValueError, "场景资产"):
            evaluate_automation(self.case, self.module, self.manifest)
        self.case["自动化建议"] = "UI自动化可做，需采集新资产"
        self.case["自动化建议明细"] += "需采集新资产：聊天截图。"
        with self.assertRaisesRegex(ValueError, "文件路径"):
            evaluate_automation(self.case, self.module, self.manifest)
        self.case["自动化建议明细"] += "存放 assets/chat.png。"
        result = evaluate_automation(self.case, self.module, self.manifest)
        self.assertTrue(result["verified"])
        self.assertFalse(result["ready"])
        self.case["automation_evidence"] = ["chat"]
        with self.assertRaisesRegex(ValueError, "函数定义"):
            evaluate_automation(self.case, self.module, self.manifest)

    def test_new_statuses_require_pending_and_are_never_ready(self):
        for advice, detail in (("当前框架能力不足", "理由：缺能力。缺失能力：状态准备。补齐办法：补充接口。"),
                               ("待核对框架能力", "理由：未核对。待核对能力：发消息接口。核对办法：静态查代码。")):
            self.case.update({"自动化建议": advice, "自动化建议明细": detail, "是否可自动化": "是"})
            with self.assertRaisesRegex(ValidationError, "待定"):
                validate_cases(self.data, draft=True)
            self.case["是否可自动化"] = "待定"
            checked = validate_cases(self.data, draft=True)[0]
            self.assertFalse(checked["_automation_ready"])
        # Citing available functions under a shortage status still validates the
        # source evidence; a fake existing function must not be accepted.
        self.case["automation_evidence"] = ["send"]
        self.manifest["capabilities"][0]["symbol"] = "fake"
        with self.assertRaisesRegex(ValueError, "未在文件中定义"):
            evaluate_automation(self.case, self.module, self.manifest)

    def test_dual_channel_needs_existing_ui_assets(self):
        self.case.update({"自动化建议": "双通道均可用", "automation_evidence": ["send"]})
        with self.assertRaisesRegex(ValueError, "场景资产"):
            evaluate_automation(self.case, self.module, self.manifest)
        self.case["automation_evidence"].append("chat")
        self.assertTrue(evaluate_automation(self.case, self.module, self.manifest)["ready"])

    def test_publication_guard_runs_after_validation_and_preserves_target_on_error(self):
        output = self.module / "review.xlsx"
        rows = [[str(i) for i in range(13)]]
        validated = []
        called = []

        def author(*args, **kwargs):
            payload = json.loads(kwargs["input"])
            Path(payload["output"]).write_bytes(b"new export")
            return SimpleNamespace(returncode=0)

        def reader(path, **kwargs):
            validated.append(Path(path))
            return {"rows": rows, "cells": {}}

        for should_fail in (False, True):
            with self.subTest(guard_failure=should_fail):
                output.write_bytes(b"original workbook")
                validated.clear()
                called.clear()

                def guard():
                    called.append(True)
                    self.assertEqual(len(validated), 1)
                    self.assertTrue(validated[0].is_file())
                    self.assertEqual(output.read_bytes(), b"original workbook")
                    if should_fail:
                        raise ValidationError("source fingerprint changed")

                with patch.dict(os.environ, {"QA_NODE_EXECUTABLE": sys.executable}), \
                     patch("qa_cases.subprocess.run", side_effect=author), \
                     patch("qa_cases.read_xlsx", side_effect=reader):
                    if should_fail:
                        with self.assertRaisesRegex(ValidationError, "fingerprint changed"):
                            run_xlsx({"operation": "cases", "rows": rows}, output, publication_guard=guard)
                    else:
                        run_xlsx({"operation": "cases", "rows": rows}, output, publication_guard=guard)
                self.assertEqual(called, [True])
                self.assertEqual(output.read_bytes(), b"original workbook" if should_fail else b"new export")
                self.assertFalse(list(self.module.glob(".qa-xlsx-*")))

        # Invalid content must fail before the publication callback can run.
        called.clear()
        output.write_bytes(b"original workbook")
        with patch.dict(os.environ, {"QA_NODE_EXECUTABLE": sys.executable}), \
             patch("qa_cases.subprocess.run", side_effect=author), \
             patch("qa_cases.read_xlsx", return_value={"rows": [["wrong"]], "cells": {}}):
            with self.assertRaises(ValidationError):
                run_xlsx({"operation": "cases", "rows": rows}, output,
                         publication_guard=lambda: called.append(True))
        self.assertFalse(called)
        self.assertEqual(output.read_bytes(), b"original workbook")
        self.assertFalse(list(self.module.glob(".qa-xlsx-*")))

    def test_unsupported_language_is_explicitly_unverified(self):
        (self.root / "client.js").write_text("function send() {}", encoding="utf-8")
        self.manifest["capabilities"] = [self.entry("send", "function", "client.js", "send")]
        self.case.update({"automation_evidence": ["send"], "自动化建议": "待核对框架能力", "是否可自动化": "待定"})
        result = evaluate_automation(self.case, self.module, self.manifest)
        self.assertFalse(result["verified"])
        self.assertFalse(result["ready"])
        self.assertIn("不支持该语言", "".join(result["reasons"]))

    def test_project_manifest_and_framework_override(self):
        path = self.module.parent.parent / "框架能力清单.json"
        manifest = deepcopy(self.manifest)
        manifest["framework_root"] = str(self.root)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(load_manifest(self.module), manifest)
        manifest["framework_root"] = "other-root"
        with patch.dict(os.environ, {"QA_FRAMEWORK_ROOT": str(self.root)}):
            self.assertEqual(len(validate_manifest(manifest, self.module)["capabilities"]), 2)


if __name__ == "__main__":
    unittest.main()
