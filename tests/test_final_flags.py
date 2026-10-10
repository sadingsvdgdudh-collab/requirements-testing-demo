"""Direct formal entry points must reject row-level unfinished metadata too."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/testpoints/scripts"))
from point_common import validate_points
from qa_cases import ValidationError, validate_cases, validate_spec

FLAGS = ({"draft": True}, {"draft": "false"}, {"update_state": "needs_evidence_check"},
         {"update_state": "pending"}, {"missing_fields": ["需补全"]}, {"needs_redesign": True})


class FinalFlagsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-formal-flags-")
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(ROOT / "example", self.project)
        self.module = self.project / "output/商品管理"
        self.spec = validate_spec(self.module)

    def tearDown(self):
        self.temp.cleanup()

    def load(self, name):
        return json.loads((self.module / name).read_text(encoding="utf-8"))

    def test_req_root_flags_are_rejected(self):
        original = self.load("REQ清单.json")
        for flag in FLAGS:
            with self.subTest(flag=flag):
                data = copy.deepcopy(original)
                data.update(flag)
                (self.module / "REQ清单.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(ValidationError):
                    validate_spec(self.module)

    def test_req_and_ac_row_flags_are_rejected(self):
        original = self.load("REQ清单.json")
        for target in ("REQ", "AC"):
            for flag in FLAGS:
                with self.subTest(target=target, flag=flag):
                    data = copy.deepcopy(original)
                    row = data["requirements"][0]
                    if target == "AC":
                        row = row["acceptance_criteria"][0]
                    row.update(flag)
                    (self.module / "REQ清单.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValidationError):
                        validate_spec(self.module)

    def test_point_root_and_row_flags_are_rejected(self):
        original = self.load("测试点数据.json")
        for target in ("root", "row"):
            for flag in FLAGS:
                data = copy.deepcopy(original)
                (data if target == "root" else data["testpoints"][0]).update(flag)
                with self.subTest(target=target, flag=flag), self.assertRaises(ValidationError):
                    validate_points(data, self.spec)

    def test_case_root_and_row_flags_are_rejected(self):
        original = self.load("用例数据.json")
        for target in ("root", "row"):
            for flag in FLAGS:
                data = copy.deepcopy(original)
                (data if target == "root" else data["cases"][0]).update(flag)
                with self.subTest(target=target, flag=flag), self.assertRaises(ValidationError):
                    validate_cases(data, self.spec)

    def test_resolved_metadata_is_formal(self):
        for name, key, checker in (("用例数据.json", "cases", validate_cases), ("测试点数据.json", "testpoints", validate_points)):
            data = self.load(name)
            data.update(draft=False, update_state="resolved")
            data[key][0].update(draft=False, update_state="resolved", missing_fields=[])
            self.assertTrue(checker(data, self.spec))

    def test_explicit_case_draft_mode_remains_distinct(self):
        data = self.load("用例数据.json")
        data.update(draft=True, update_state="pending")
        data["cases"][0]["draft"] = True
        self.assertTrue(validate_cases(data, self.spec, draft=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
