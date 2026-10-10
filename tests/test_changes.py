"""History, precise dependency impact and safe update regressions on real files."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "modules" / "change-manager" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import change_common as change


class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-changes-")
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(PACKAGE / "example", self.project)
        self.module = (self.project / "output" / "商品管理").resolve()

    def tearDown(self):
        self.temp.cleanup()

    def read(self, directory, name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    def alter(self, directory, name, callback):
        data = self.read(directory, name)
        callback(data)
        change.write_json(directory / name, data)

    def current_hashes(self):
        return {str(path.relative_to(self.module)): change.digest(path) for path in change.current_files(self.module)}

    def resolve(self, draft):
        for kind, name in change.FILES.items():
            path = draft / name
            if not path.exists():
                continue
            data = self.read(draft, name)
            data.update(draft=False, update_state="resolved")
            for row in data.get(kind, []):
                if row.get("update_state"):
                    row.update(update_state="resolved", update_resolution="已依据当前原文复核业务规则和关联，保留同一测试意图。", same_test_intent=True)
            change.write_json(path, data)
        plan = self.read(draft, "更新计划.json")
        plan.update(draft=False, update_state="resolved")
        for item in plan["retirement_decisions"]:
            item.update(update_state="resolved", reason="该测试意图已移除，保留原记录归档。")
        change.write_json(draft / "更新计划.json", plan)

    def cli(self, script, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), "--module-dir", str(self.module), *map(str, args)], capture_output=True, text=True, timeout=30)
        message = result.stdout + result.stderr
        self.assertNotIn("Traceback", message)
        self.assertEqual(result.returncode, 0 if ok else 1, message)
        return message

    def resolve_dependents(self, draft):
        """Finish item declarations so a test can exercise deeper evidence guards."""
        for kind in ("testpoints", "cases"):
            def mark(data):
                for row in data[kind]:
                    row.update(update_state="resolved", update_resolution="已明确完成候选需求相关项的核对。", same_test_intent=True)
            self.alter(draft, change.FILES[kind], mark)

    def test_snapshot_copies_current_artifacts_and_evidence_without_changes(self):
        before = self.current_hashes()
        snapshot = change.create_snapshot(self.module, "初版")
        manifest = change.load_snapshot(snapshot)
        self.assertEqual(before, self.current_hashes())
        self.assertIn("module/详细用例.xlsx", manifest["files"])
        self.assertIn("module/测试点.xmind", manifest["files"])
        self.assertEqual(len(manifest["evidence"]), 1)
        entry = manifest["evidence"][0]
        self.assertEqual(entry["sha256"], change.digest(self.project / "input" / "新增商品.md"))
        self.assertTrue((snapshot / entry["stored"]).is_file())
        self.assertNotEqual(snapshot, change.create_snapshot(self.module, "初版"))

    def test_snapshot_label_rejects_path_traversal(self):
        with self.assertRaisesRegex(change.ValidationError, "标签"):
            change.create_snapshot(self.module, "../../危险")

    def test_tampered_snapshot_detected(self):
        snapshot = change.create_snapshot(self.module)
        (snapshot / "module" / "REQ清单.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(change.ValidationError, "改变或缺失"):
            change.analyze(self.module, snapshot)

    def test_module_shared_change_marks_every_case_for_evidence_recheck(self):
        """模块级公共前置条件/取证方式变化不能被报告成"无差异"。"""
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "用例数据.json",
                   lambda data: data.update(module_preconditions=["改为在独立测试服上执行。"]))
        data = change.analyze(self.module, snapshot)
        module_change = [item for item in data["changes"]["cases"] if item["category"] == "module_shared_changed"]
        self.assertEqual(module_change[0]["fields"], ["module_preconditions"])
        self.assertEqual(len(data["affected_cases"]), 10)
        self.assertTrue(all(item["update_state"] == "needs_evidence_check" for item in data["affected_cases"]))
        self.assertIn("模块级公共段变更：module_preconditions", data["affected_cases"][0]["reasons"])

    def test_string_source_reports_clear_error_instead_of_attribute_error(self):
        """手改成字符串的 source 必须报出哪条 REQ 有问题。"""
        self.alter(self.module, "REQ清单.json",
                   lambda data: data["requirements"][0].update(source="input/任务规则文本.md"))
        with self.assertRaises(change.ValidationError) as caught:
            change.create_snapshot(self.module)
        self.assertIn("source 必须为对象", str(caught.exception))

    def test_same_data_has_no_changes(self):
        snapshot = change.create_snapshot(self.module)
        data = change.analyze(self.module, snapshot)
        self.assertFalse(any(data["changes"].values()))
        self.assertFalse(data["affected_testpoints"])
        self.assertEqual(len(data["unaffected_cases"]), 10)

    def test_ac_parameter_revision_precisely_propagates(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "REQ清单.json", lambda data: data["requirements"][1]["acceptance_criteria"][0].update(text="价格等于0时提示新的价格校验文案并不创建。"))
        data = change.analyze(self.module, snapshot)
        self.assertEqual([item["id"] for item in data["affected_testpoints"]], ["TP_PRO_002"])
        self.assertEqual([item["id"] for item in data["affected_cases"]], ["TC_PRO_003"])
        self.assertIn("TC_PRO_004", data["unaffected_cases"])
        self.assertFalse(data["changes"]["requirements"])

    def test_req_description_changes_all_direct_dependents(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "REQ清单.json", lambda data: data["requirements"][0].update(description="名称为空时显示修改后的提示。"))
        data = change.analyze(self.module, snapshot)
        self.assertEqual([item["id"] for item in data["affected_testpoints"]], ["TP_PRO_001"])
        self.assertEqual([item["id"] for item in data["affected_cases"]], ["TC_PRO_002"])

    def test_testpoint_revision_propagates_without_direct_req_change(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "测试点数据.json", lambda data: data["testpoints"][0].update(测试数据="名称为空；价格1.00。"))
        data = change.analyze(self.module, snapshot)
        self.assertEqual([item["id"] for item in data["affected_cases"]], ["TC_PRO_002"])
        self.assertIn("经测试点传递：TP_PRO_001", data["affected_cases"][0]["reasons"])

    def test_source_location_only_is_not_business_redesign(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "REQ清单.json", lambda data: data["requirements"][0]["source"].update(lines=[7, 8]))
        data = change.analyze(self.module, snapshot)
        source_change = data["changes"]["requirements"][0]
        self.assertEqual(source_change["category"], "source_relocated")
        self.assertFalse(source_change["business_change"])
        self.assertEqual(data["affected_testpoints"][0]["update_state"], "needs_evidence_check")
        self.assertEqual(data["affected_cases"][0]["update_state"], "needs_evidence_check")

    def test_source_file_change_requires_recheck_even_same_json(self):
        snapshot = change.create_snapshot(self.module)
        source = self.project / "input" / "新增商品.md"
        source.write_text(source.read_text(encoding="utf-8") + "\n补充说明。\n", encoding="utf-8")
        data = change.analyze(self.module, snapshot)
        self.assertTrue(all(item["update_state"] == "needs_evidence_check" for item in data["affected_cases"]))
        self.assertEqual(len(data["affected_cases"]), 10)

    def test_execution_result_change_does_not_trigger_redesign(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "用例数据.json", lambda data: data["cases"][0].update(实际结果="人工执行失败：列表缺少商品。"))
        data = change.analyze(self.module, snapshot)
        self.assertFalse(data["affected_cases"])
        self.assertEqual(data["changes"]["cases"][0]["category"], "execution_record_changed")

    def test_prepare_is_draft_and_does_not_replace_business_content(self):
        self.alter(self.module, "用例数据.json", lambda data: data["cases"][2].update(实际结果="已执行，价格0被拒绝。", execution_records=[{"run": 1, "result": "passed"}]))
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "REQ清单.json", lambda data: data["requirements"][1]["acceptance_criteria"][0].update(text="价格为0的提示文案发生变化。"))
        before = self.current_hashes()
        draft = change.prepare(self.module, snapshot)
        candidate = self.read(draft, "用例数据.json")
        current = self.read(self.module, "用例数据.json")
        self.assertTrue(candidate["draft"])
        self.assertEqual(candidate["cases"][2]["update_state"], "needs_redesign")
        for field in change.BUSINESS_FIELDS["cases"] | change.EXECUTION_FIELDS:
            self.assertEqual(candidate["cases"][2].get(field), current["cases"][2].get(field))
        self.assertNotIn("update_state", candidate["cases"][3])
        self.assertEqual(before, self.current_hashes())

    def test_apply_rejects_draft_without_mutation(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        before = self.current_hashes()
        with self.assertRaisesRegex(change.ValidationError, "草稿"):
            change.apply(self.module, draft)
        self.assertEqual(before, self.current_hashes())

    def test_apply_rejects_unresolved_row_despite_root_flags(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "测试点数据.json", lambda data: data["testpoints"][0].update(测试数据="名称为空；价格2。"))
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "测试点数据.json", lambda data: data["testpoints"][0].update(update_state="needs_redesign"))
        with self.assertRaisesRegex(change.ValidationError, "仍需处理"):
            change.apply(self.module, draft)

    def test_apply_rejects_tampered_affected_plan(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "测试点数据.json", lambda data: data["testpoints"][0].update(测试数据="名称为空；价格2。"))
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "更新计划.json", lambda data: data.update(affected_items={"testpoints": {}, "cases": {}}))
        with self.assertRaisesRegex(change.ValidationError, "受影响项"):
            change.apply(self.module, draft)

    def test_apply_rejects_stale_current_inputs_and_evidence(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        source = self.project / "input" / "新增商品.md"
        source.write_text(source.read_text(encoding="utf-8") + "\n原文新增要求。", encoding="utf-8")
        with self.assertRaisesRegex(change.ValidationError, "来源文件改变"):
            change.apply(self.module, draft)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(self.module, "用例数据.json", lambda data: data["cases"][0].update(实际结果="新的执行记录。"))
        with self.assertRaisesRegex(change.ValidationError, "草稿已过期"):
            change.apply(self.module, draft)

    def test_apply_preserves_execution_records(self):
        self.alter(self.module, "用例数据.json", lambda data: data["cases"][0].update(实际结果="已执行通过。", execution_records=[{"result": "passed"}]))
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "用例数据.json", lambda data: data["cases"][0].update(实际结果=""))
        with self.assertRaisesRegex(change.ValidationError, "执行记录"):
            change.apply(self.module, draft)

    def test_apply_rejects_source_without_actual_evidence(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0]["source"].update(quote="不存在的原文。"))
        self.resolve_dependents(draft)
        with self.assertRaisesRegex(change.ValidationError, "quote"):
            change.apply(self.module, draft)

    def test_apply_rejects_blocked_and_dangling_relations(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0].update(status="blocked"))
        self.resolve_dependents(draft)
        with self.assertRaisesRegex(change.ValidationError, "active"):
            change.apply(self.module, draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0].update(status="active"))
        def break_link(data):
            data["cases"][0].update(关联测试点=["TP_PRO_999"], update_state="resolved", update_resolution="明确更改关联。", same_test_intent=True)
        self.alter(draft, "用例数据.json", break_link)
        with self.assertRaisesRegex(change.ValidationError, "不存在或REQ/AC"):
            change.apply(self.module, draft)

    def test_apply_rejects_missing_new_acceptance_coverage(self):
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "REQ清单.json", lambda data: data["requirements"][0]["acceptance_criteria"].append({"id": "AC_PRO_001_02", "text": "新增已确认的名称验收标准。"}))
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        with self.assertRaisesRegex(change.ValidationError, "新增验收标准尚缺"):
            change.apply(self.module, draft)

    def test_candidate_ac_revision_after_prepare_requires_dependents_resolution(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][1]["acceptance_criteria"][0].update(text="价格等于0时改为新版价格错误提示并不创建。"))
        before = self.current_hashes()
        with self.assertRaisesRegex(change.ValidationError, "TP_PRO_002.*缺少明确"):
            change.apply(self.module, draft)
        self.assertEqual(before, self.current_hashes())
        self.alter(draft, "测试点数据.json", lambda data: data["testpoints"][1].update(update_state="resolved", update_resolution="已按新版价格提示重新设计。", same_test_intent=True))
        with self.assertRaisesRegex(change.ValidationError, "TC_PRO_003.*缺少明确"):
            change.apply(self.module, draft)

    def test_candidate_req_revision_after_prepare_propagates_to_dependencies(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0].update(description="商品名称为空时采用修订后的必填提示。"))
        with self.assertRaisesRegex(change.ValidationError, "TP_PRO_001.*缺少明确"):
            change.apply(self.module, draft)

    def test_candidate_point_revision_after_prepare_propagates_to_cases(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "测试点数据.json", lambda data: data["testpoints"][0].update(测试数据="名称为空；价格2.00。", update_state="resolved", update_resolution="已调整本点测试数据。", same_test_intent=True))
        with self.assertRaisesRegex(change.ValidationError, "TC_PRO_002.*缺少明确"):
            change.apply(self.module, draft)

    def test_candidate_new_ac_after_prepare_requires_explicit_coverage(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0]["acceptance_criteria"].append({"id": "AC_PRO_001_02", "text": "新增已确认的名称验收标准。"}))
        before = self.current_hashes()
        with self.assertRaisesRegex(change.ValidationError, "新增验收标准尚缺"):
            change.apply(self.module, draft)
        self.assertEqual(before, self.current_hashes())

    def test_candidate_source_revision_requires_dependency_evidence_recheck(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        self.alter(draft, "REQ清单.json", lambda data: data["requirements"][0]["source"].update(lines=[7, 8]))
        with self.assertRaisesRegex(change.ValidationError, "TP_PRO_001.*缺少明确"):
            change.apply(self.module, draft)

    def test_reviewed_parser_fingerprints_use_real_project_during_staged_apply(self):
        source = self.project / "input" / "新增商品.md"
        source_sha = change.digest(source)
        change.write_json(self.module / "解析来源.json", {"schema_version": "1.0", "module": "商品管理",
            "original": {"file": "input/新增商品.md", "sha256": source_sha, "format": ".md"},
            "draft": "input/新增商品.md", "draft_sha256": source_sha,
            "needs_review": True, "warnings": ["测试夹具：需视觉核对的来源"]})
        change.write_json(self.module / "解析核对.json", {"schema_version": "1.0", "original_sha256": source_sha,
            "draft_sha256": source_sha, "status": "confirmed", "reviewer": "测试夹具核对者",
            "notes": "本回归只验证隔离校验仍指向实际项目来源，不认证业务。", "findings": []})
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        backup = change.apply(self.module, draft)
        self.assertTrue((backup / "module" / "解析核对.json").is_file())
        self.assertEqual(change.validate_spec(self.module)["module"], "商品管理")
        # Review changes are part of the stale-draft guard, just like raw inputs.
        fresh = change.prepare(self.module, snapshot)
        self.resolve(fresh)
        self.alter(self.module, "解析核对.json", lambda data: data.update(notes="核对说明更新。"))
        with self.assertRaisesRegex(change.ValidationError, "草稿已过期"):
            change.apply(self.module, fresh)

    def test_candidate_transcription_files_are_published_with_the_validated_req(self):
        previous_parse = (self.module / "解析.md").read_bytes()
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        source = self.project / "input" / "新增商品第二版.md"
        change.write_text(source, (self.project / "input" / "新增商品.md").read_text(encoding="utf-8") + "\n第二版来源备注。\n")
        source_sha = change.digest(source)
        change.write_text(draft / "解析.md", "# 第二版解析\n\n本测试验证已校验的三份来源文件一起发布。\n")
        change.write_json(draft / "解析来源.json", {"schema_version": "1.0", "module": "商品管理",
            "original": {"file": "input/新增商品第二版.md", "sha256": source_sha, "format": ".md"},
            "draft": "input/新增商品第二版.md", "draft_sha256": source_sha, "needs_review": True})
        change.write_json(draft / "解析核对.json", {"schema_version": "1.0", "original_sha256": source_sha,
            "draft_sha256": source_sha, "status": "confirmed", "reviewer": "测试夹具核对者",
            "notes": "第二版来源及转录已核对，仅验证元数据发布一致性。", "findings": []})
        def rebase(data):
            for req in data["requirements"]:
                req["source"]["file"] = "../input/新增商品第二版.md"
        self.alter(draft, "REQ清单.json", rebase)
        self.resolve_dependents(draft)
        backup = change.apply(self.module, draft)
        for name in ("解析.md", "解析来源.json", "解析核对.json"):
            self.assertEqual((draft / name).read_bytes(), (self.module / name).read_bytes())
        self.assertEqual(previous_parse, (backup / "module" / "解析.md").read_bytes())
        self.assertEqual(change.validate_spec(self.module)["requirements"][0]["source"]["file"], "../input/新增商品第二版.md")

    def test_apply_success_backups_and_archives_stale_derivatives(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        before = self.current_hashes()
        backup = change.apply(self.module, draft)
        change.load_snapshot(backup)
        self.assertEqual((backup / "module" / "用例数据.json").read_bytes(), (snapshot / "module" / "用例数据.json").read_bytes())
        self.assertFalse((self.module / "详细用例.xlsx").exists())
        self.assertTrue((backup / "stale-derived" / "详细用例.xlsx").is_file())
        self.assertFalse((self.module / "测试点.xmind").exists())
        self.assertFalse((self.module / "测试点评审结果.json").exists())
        self.assertFalse(self.read(self.module, "用例数据.json")["draft"])
        self.assertIn("尚未进行业务评审或实际执行", (self.module / "更新应用报告.md").read_text(encoding="utf-8"))
        self.assertEqual(before["解析.md"], change.digest(self.module / "解析.md"))

    def test_removed_case_decision_and_execution_archive_required(self):
        self.alter(self.module, "用例数据.json", lambda data: data["cases"][-1].update(实际结果="旧执行结果应保留。"))
        snapshot = change.create_snapshot(self.module)
        self.alter(self.module, "用例数据.json", lambda data: data["cases"].pop())
        draft = change.prepare(self.module, snapshot)
        records = self.read(draft, "移除记录.json")["records"]
        self.assertEqual(records[0]["record"]["实际结果"], "旧执行结果应保留。")
        self.resolve(draft)
        self.alter(draft, "更新计划.json", lambda data: data["retirement_decisions"][0].update(reason=""))
        with self.assertRaisesRegex(change.ValidationError, "归档原因"):
            change.apply(self.module, draft)
        self.alter(draft, "更新计划.json", lambda data: data["retirement_decisions"][0].update(reason="已移除该测试意图。"))
        backup = change.apply(self.module, draft)
        self.assertEqual(self.read(backup, "removed-records.json")["records"][0]["record"]["实际结果"], "旧执行结果应保留。")

    def test_publication_failure_restores_all_current_files(self):
        snapshot = change.create_snapshot(self.module)
        draft = change.prepare(self.module, snapshot)
        self.resolve(draft)
        before = self.current_hashes()
        original_write = change.write_text
        def fail_on_cases(path, value):
            if path == self.module / "用例数据.json":
                raise OSError("模拟存储失败")
            return original_write(path, value)
        with mock.patch.object(change, "write_text", side_effect=fail_on_cases):
            with self.assertRaisesRegex(change.ValidationError, "已恢复原资料"):
                change.apply(self.module, draft)
        self.assertEqual(before, self.current_hashes())

    def test_cli_errors_are_actionable_without_traceback(self):
        message = self.cli("snapshot.py", "--label", "../无效", ok=False)
        self.assertIn("标签", message)
        snapshot = change.create_snapshot(self.module)
        self.assertIn("变更影响报告", self.cli("analyze_changes.py", "--baseline", snapshot))
        self.assertIn("更新草稿", self.cli("prepare_update.py", "--baseline", snapshot))


if __name__ == "__main__":
    unittest.main()
