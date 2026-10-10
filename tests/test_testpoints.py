"""Behavior checks for point output, review freshness, XMind and TP-to-case links."""

import hashlib
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
SCRIPTS = PACKAGE / "modules" / "testpoints" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from point_common import ValidationError, load_context, load_current_review
from review_testpoints import build_result


def topics(root):
    yield root
    for child in root.get("children", {}).get("attached", []):
        yield from topics(child)


class TestpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="requirements-points-")
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(PACKAGE / "example", self.project)
        self.module = self.project / "output" / "商品管理"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, script, *args, ok=True):
        completed = subprocess.run([sys.executable, str(SCRIPTS / script), "--module-dir", str(self.module), *map(str, args)], capture_output=True, text=True, timeout=30)
        message = completed.stdout + completed.stderr
        self.assertNotIn("Traceback", message, message)
        self.assertEqual(completed.returncode, 0 if ok else 1, message)
        return message

    def read(self, name):
        return json.loads((self.module / name).read_text(encoding="utf-8"))

    def change(self, name, callback):
        data = self.read(name)
        callback(data)
        (self.module / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_complete_points_review_and_case_links(self):
        names = ("REQ清单.json", "测试点数据.json", "测试点评审数据.json")
        digests = {name: hashlib.sha256((self.module / name).read_bytes()).digest() for name in names}
        self.run_cli("export_testpoints.py")
        self.run_cli("review_testpoints.py")
        self.run_cli("check_traceability.py")
        result = load_current_review(self.module)
        self.assertEqual(result["coverage"]["point_count"], 13)
        self.assertEqual(result["approved_coverage"]["acceptance_coverage"]["covered"], 10)
        self.assertTrue(result["summary"]["ready"])
        links = self.read("测试点用例关联数据.json")
        self.assertEqual(links["linked_point_count"], 13)
        self.assertEqual(links["case_count"], 10)
        self.assertTrue(links["full_scope_complete"])
        for name in names:
            self.assertEqual(digests[name], hashlib.sha256((self.module / name).read_bytes()).digest())
        matrix = (self.module / "测试点覆盖矩阵.md").read_text(encoding="utf-8")
        self.assertIn("AC_PRO_005_02", matrix)
        self.assertIn("TP_PRO_011", matrix)

    def test_duplicate_point_id_rejected(self):
        self.change("测试点数据.json", lambda data: data["testpoints"].append(data["testpoints"][0]))
        self.run_cli("export_testpoints.py", ok=False)

    def test_unknown_requirement_rejected(self):
        self.change("测试点数据.json", lambda data: data["testpoints"][0].update(关联需求=["REQ_PRO_999"]))
        self.run_cli("export_testpoints.py", ok=False)

    def test_wrong_acceptance_owner_rejected(self):
        self.change("测试点数据.json", lambda data: data["testpoints"][0].update(验收标准=["AC_PRO_002_01"]))
        self.run_cli("export_testpoints.py", ok=False)

    def test_blocked_requirement_not_used_by_formal_point(self):
        self.change("REQ清单.json", lambda data: data["requirements"][0].update(status="blocked"))
        self.run_cli("export_testpoints.py", ok=False)

    def test_missing_point_coverage_is_reported(self):
        self.change("测试点数据.json", lambda data: data.update(testpoints=data["testpoints"][1:]))
        self.run_cli("export_testpoints.py", ok=False)
        coverage = self.read("测试点覆盖率数据.json")
        self.assertIn("AC_PRO_001_01", coverage["acceptance_coverage"]["missing"])

    def test_empty_blocked_scope_does_not_report_full_coverage(self):
        self.change("REQ清单.json", lambda data: [req.update(status="blocked") for req in data["requirements"]])
        self.change("测试点数据.json", lambda data: data.update(testpoints=[]))
        self.run_cli("export_testpoints.py", ok=False)
        coverage = self.read("测试点覆盖率数据.json")
        self.assertEqual(coverage["status"], "not_designable")
        self.assertIsNone(coverage["requirement_coverage"]["rate"])

    def test_missing_point_review_rejected(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"].pop())
        self.run_cli("review_testpoints.py", ok=False)

    def test_duplicate_point_review_rejected(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"].append(data["reviews"][0]))
        self.run_cli("review_testpoints.py", ok=False)

    def test_review_wrong_scene_evidence_rejected(self):
        self.change("测试点评审数据.json", lambda data: data["scope_checks"][2].update(evidence=["TP_PRO_010"]))
        self.run_cli("review_testpoints.py", ok=False)

    def test_review_cannot_hide_present_scene_as_not_applicable(self):
        self.change("测试点评审数据.json", lambda data: data["scope_checks"][0].update(status="not_applicable", evidence=[]))
        self.run_cli("review_testpoints.py", ok=False)

    def test_high_grade_does_not_hide_rejected_point_or_lost_coverage(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"][0].update(结论="不通过", 原因="未落实观察方法。", 建议="明确结果观察渠道。"))
        self.run_cli("review_testpoints.py")
        result = self.read("测试点评审结果.json")
        # 高分不掩盖被否的点：得分仍高，但 ready 必须为 false
        self.assertGreaterEqual(result["summary"]["total"], 90)
        self.assertNotIn("grade", result["summary"], "不应再产出字母等级")
        self.assertFalse(result["summary"]["ready"])
        self.assertEqual(result["coverage"]["requirement_coverage"]["rate"], 1)
        self.assertLess(result["approved_coverage"]["requirement_coverage"]["rate"], 1)
        self.assertIn("AC_PRO_001_01", result["approved_coverage"]["acceptance_coverage"]["missing"])

    def test_rejected_evidence_does_not_score_as_covered_flow(self):
        def reject_errors(data):
            for row in data["reviews"]:
                if row["测试点编号"] in ("TP_PRO_001", "TP_PRO_003", "TP_PRO_009"):
                    row["结论"] = "不通过"
        self.change("测试点评审数据.json", reject_errors)
        self.run_cli("review_testpoints.py")
        summary = self.read("测试点评审结果.json")["summary"]
        self.assertEqual(summary["components"]["适用三流覆盖"], 10)
        self.assertIn("异常", summary["missing"])
        self.assertFalse(summary["ready"])

    def test_exact_duplicate_content_stays_visible_despite_pass_verdicts(self):
        def duplicate(data):
            point = dict(data["testpoints"][3], 测试点编号="TP_PRO_014")
            data["testpoints"].append(point)
        self.change("测试点数据.json", duplicate)
        self.change("测试点评审数据.json", lambda data: data["reviews"].append({"测试点编号": "TP_PRO_014", "结论": "通过", "原因": "输入格式完整。", "建议": "无。"}))
        self.run_cli("review_testpoints.py")
        result = self.read("测试点评审结果.json")
        self.assertFalse(result["summary"]["ready"])
        self.assertIn(["TP_PRO_004", "TP_PRO_014"], result["summary"]["duplicate_groups"])

    def test_changed_points_invalidate_review_for_xmind(self):
        self.change("测试点数据.json", lambda data: data["testpoints"][0].update(测试点名称="修改后的名称校验"))
        self.assertIn("过期", self.run_cli("export_xmind.py", ok=False))

    def test_changed_requirements_invalidate_review(self):
        self.change("REQ清单.json", lambda data: data["questions"][0].update(text="修改后的待澄清问题。"))
        with self.assertRaisesRegex(ValidationError, "过期"):
            load_current_review(self.module)

    def test_tampered_cached_score_rejected(self):
        self.change("测试点评审结果.json", lambda data: data["summary"].update(total=1))
        self.run_cli("export_xmind.py", ok=False)

    def test_xmind_displays_current_unconfirmed_verdict(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"][0].update(结论="需确认", 原因="观察入口待确定。"))
        self.run_cli("review_testpoints.py")
        self.run_cli("export_xmind.py")
        with zipfile.ZipFile(self.module / "测试点.xmind") as archive:
            content = json.loads(archive.read("content.json"))
        node = next(item for item in topics(content[0]["rootTopic"]) if item["title"].startswith("TP_PRO_001"))
        self.assertIn("需确认", node["title"])
        self.assertIn("观察入口待确定", node["notes"]["plain"]["content"])

    def test_explicit_missing_review_is_not_treated_as_unreviewed(self):
        self.run_cli("export_xmind.py", "--review", self.module / "不存在的评审.json", ok=False)

    def test_xmind_output_cannot_replace_source_document(self):
        source = self.project / "input" / "原件.xmind"
        source.write_bytes((self.project / "input" / "新增商品.md").read_bytes())
        original = source.read_bytes()
        self.change("REQ清单.json", lambda data: [req["source"].update(file="../input/原件.xmind") for req in data["requirements"]])
        (self.module / "测试点评审结果.json").unlink()
        self.run_cli("export_xmind.py", "--output", source, ok=False)
        self.assertEqual(source.read_bytes(), original)

    def test_partial_approved_scope_can_proceed_without_claiming_full_completion(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"][0].update(结论="需确认"))
        self.run_cli("review_testpoints.py")
        self.change("用例数据.json", lambda data: data.update(cases=[case for case in data["cases"] if case["用例编号"] != "TC_PRO_002"]))
        self.run_cli("check_traceability.py")
        result = self.read("测试点用例关联数据.json")
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["full_scope_complete"])
        self.assertEqual(result["approved_point_count"], 12)

    def test_cached_verdict_change_requires_consistent_recalculation(self):
        self.change("测试点评审结果.json", lambda data: data["reviews"][0].update(结论="不通过"))
        self.run_cli("check_traceability.py", ok=False)

    def test_case_cannot_reference_unapproved_point(self):
        self.change("测试点评审数据.json", lambda data: data["reviews"][0].update(结论="需确认"))
        self.run_cli("review_testpoints.py")
        self.run_cli("check_traceability.py", ok=False)

    def test_point_without_case_fails_even_if_req_ac_coverage_is_full(self):
        self.change("用例数据.json", lambda data: data["cases"][6].update(关联测试点=["TP_PRO_004", "TP_PRO_007", "TP_PRO_010", "TP_PRO_011"]))
        self.run_cli("check_traceability.py", ok=False)
        self.assertIn("TP_PRO_013", self.read("测试点用例关联数据.json")["missing_approved_points"])

    def test_wrong_point_case_association_rejected(self):
        self.change("用例数据.json", lambda data: data["cases"][2].update(关联测试点=["TP_PRO_007"]))
        self.run_cli("check_traceability.py", ok=False)

    def test_unicode_xmind_tree_notes_and_classic_content_match(self):
        self.change("测试点数据.json", lambda data: data["testpoints"][0].update(测试点名称="中文 <名称> & 特殊符号 😀"))
        (self.module / "测试点评审结果.json").unlink()
        self.run_cli("export_xmind.py")
        with zipfile.ZipFile(self.module / "测试点.xmind") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"content.json", "content.xml", "metadata.json", "manifest.json"})
            content = json.loads(archive.read("content.json"))
            xml = ET.fromstring(archive.read("content.xml"))
        nodes = list(topics(content[0]["rootTopic"]))
        point_nodes = [node for node in nodes if node["title"].startswith("TP_PRO_")]
        self.assertEqual(len(point_nodes), 13)
        self.assertEqual(len({node["id"] for node in nodes}), len(nodes))
        self.assertIn("中文 <名称> & 特殊符号 😀", point_nodes[0]["title"])
        self.assertIn("未评审", point_nodes[0]["title"])
        multi_req = next(node for node in point_nodes if node["title"].startswith("TP_PRO_013"))
        self.assertIn("REQ_PRO_003", multi_req["notes"]["plain"]["content"])
        titles = [node.text for node in xml.findall(".//{urn:xmind:xmap:xmlns:content:2.0}title")]
        self.assertIn(point_nodes[0]["title"], titles)

    def test_xml_illegal_control_character_rejected_without_overwrite(self):
        path = self.module / "测试点.xmind"
        before = path.read_bytes()
        self.change("测试点数据.json", lambda data: data["testpoints"][0].update(测试点名称="名字\u0000非法"))
        (self.module / "测试点评审结果.json").unlink()
        self.run_cli("export_xmind.py", ok=False)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
