"""Real CLI checks for an explicit manifest and intentionally pending assets."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
TOOL = PACKAGE / "tools" / "检查能力清单对接.py"


class ManifestIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-manifest-cli-")
        self.base = Path(self.temp.name)
        self.module = self.base / "project" / "output" / "module"
        self.module.mkdir(parents=True)
        self.framework = self.module / "framework"
        self.framework.mkdir()
        source = self.framework / "ui.py"
        source.write_text("def dialogue():\n    pass\n", encoding="utf-8")
        self.manifest = {
            "schema_version": "1.1", "framework_root": str(self.framework),
            "reviewer": "test", "checked_at": "2026-10-12",
            "capabilities": [{"id": "dialogue", "kind": "function", "channel": "ui",
                              "file": "ui.py", "symbol": "dialogue", "notes": "fixture",
                              "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
        }
        self.case = {"用例编号": "TC_CLI_001", "是否可自动化": "是",
                     "自动化建议": "UI自动化可做，需采集新资产",
                     "自动化建议明细": "理由：需要新图。已有函数/资源：dialogue。需采集新资产：assets/new.png。",
                     "automation_evidence": ["dialogue"]}
        self.external = self.base / "external.json"
        self.write_json(self.external, self.manifest)
        self.write_json(self.module / "用例数据.json", {"cases": [self.case]})

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_tool(self, explicit=True):
        cmd = [sys.executable, str(TOOL), "--module", str(self.module)]
        if explicit:
            cmd += ["--manifest", str(self.external)]
        env = dict(os.environ)
        env.pop("QA_FRAMEWORK_ROOT", None)
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.base, timeout=30)

    def test_explicit_manifest_works_without_local_copy(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.module / "框架能力清单.json").exists())

    def test_explicit_manifest_overrides_stale_local_copy(self):
        stale = deepcopy(self.manifest)
        stale["capabilities"][0]["sha256"] = "0" * 64
        self.write_json(self.module / "框架能力清单.json", stale)
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_manifest_preserves_module_relative_framework_root(self):
        self.manifest["framework_root"] = "framework"
        self.write_json(self.external, self.manifest)
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pending_assets_are_consistent_but_not_reported_ready(self):
        self.write_json(self.module / "框架能力清单.json", self.manifest)
        result = self.run_tool(explicit=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("尚未就绪", result.stdout)

    def test_pending_assets_without_verified_evidence_are_rejected(self):
        self.case.pop("automation_evidence")
        self.write_json(self.module / "用例数据.json", {"cases": [self.case]})
        self.write_json(self.module / "框架能力清单.json", self.manifest)
        result = self.run_tool(explicit=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_missing_capability_still_blocks_pending_assets(self):
        self.case["automation_evidence"] = ["missing"]
        self.write_json(self.module / "用例数据.json", {"cases": [self.case]})
        self.write_json(self.module / "框架能力清单.json", self.manifest)
        result = self.run_tool(explicit=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未知框架能力", result.stdout)

    def test_claimed_ui_ready_without_asset_is_rejected(self):
        self.case["自动化建议"] = "UI自动化可用"
        self.write_json(self.module / "用例数据.json", {"cases": [self.case]})
        self.write_json(self.module / "框架能力清单.json", self.manifest)
        result = self.run_tool(explicit=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("场景资产", result.stdout)


if __name__ == "__main__":
    unittest.main()
