"""框架能力清单 1.1：通道一致性 + 反向证据（反向证据 = 声称能力不足时反查清单）。

清单 schema 1.0 只核对"能力是否真实存在"；1.1 起每条能力必须声明 channel，
据此核对"仅 api / UI / 双通道"的建议是否与所引能力的通道一致，
并在声称"当前框架能力不足"时反查清单，防止用一条最省事的取值绕开设计。
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from automation_evidence import evaluate_automation, validate_manifest  # noqa: E402


class ManifestChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-channels-")
        self.module = Path(self.temp.name) / "output" / "模块"
        self.module.mkdir(parents=True)
        self.root = self.module / "framework"
        self.root.mkdir()
        (self.root / "api.py").write_text(
            "def buy(player, item, qty):\n    pass\n", encoding="utf-8")
        (self.root / "ui.py").write_text(
            "class StendhalUI:\n    def dialogue(self, *words):\n        pass\n", encoding="utf-8")
        asset = self.root / "assets" / "chat.png"
        asset.parent.mkdir()
        asset.write_bytes(b"png bytes")
        self.manifest = {
            "schema_version": "1.1", "framework_root": "framework", "reviewer": "auto:test",
            "checked_at": "2026-10-10T10:00:00+08:00",
            "capabilities": [
                self.entry("buy", "function", "api.py", "buy", "api"),
                self.entry("dialogue", "function", "ui.py", "StendhalUI.dialogue", "ui"),
                self.entry("both_check", "function", "api.py", "buy", "both"),
                self.entry("chat", "asset", "assets/chat.png", None, "ui"),
            ],
        }
        example = PACKAGE / "example" / "output" / "商品管理"
        data = json.loads((example / "用例数据.json").read_text(encoding="utf-8"))
        self.case = deepcopy(data["cases"][0])
        self.case.update({"是否可自动化": "是", "自动化建议": "仅api自动化可用",
                          "自动化建议明细": "理由：接口可断言。已有函数/资源：buy。"})
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.old_override = os.environ.pop("QA_FRAMEWORK_ROOT", None)

    def tearDown(self):
        if self.old_override is not None:
            os.environ["QA_FRAMEWORK_ROOT"] = self.old_override
        self.env.stop()
        self.temp.cleanup()

    def entry(self, cap_id, kind, file, symbol=None, channel=None):
        result = {"id": cap_id, "kind": kind, "file": file,
                  "sha256": hashlib.sha256((self.root / file).read_bytes()).hexdigest(),
                  "notes": "已静态核对。"}
        if symbol:
            result["symbol"] = symbol
        if channel:
            result["channel"] = channel
        return result

    def publish(self, manifest=None):
        (self.module / "框架能力清单.json").write_text(
            json.dumps(manifest or self.manifest, ensure_ascii=False), encoding="utf-8")

    def evaluate(self, **changes):
        self.case.update(changes)
        return evaluate_automation(self.case, module_dir=self.module)

    # ── 清单契约 ──────────────────────────────────────────────
    def test_schema_1_1_requires_channel_on_every_capability(self):
        self.manifest["capabilities"][0].pop("channel")
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(self.manifest, self.module)
        self.assertIn("channel", str(ctx.exception))

    def test_unknown_channel_rejected(self):
        self.manifest["capabilities"][0]["channel"] = "desktop"
        with self.assertRaises(ValueError) as ctx:
            validate_manifest(self.manifest, self.module)
        self.assertIn("channel", str(ctx.exception))

    def test_schema_1_0_still_accepted_and_channel_rules_skipped(self):
        legacy = deepcopy(self.manifest)
        legacy["schema_version"] = "1.0"
        for cap in legacy["capabilities"]:
            cap.pop("channel")
        self.publish(legacy)
        # 1.0 清单没有通道信息：不能因为"通道未知"就把一条合理的建议判死。
        result = self.evaluate(**{"自动化建议": "双通道均可用", "automation_evidence": ["buy", "chat"]})
        self.assertTrue(result["verified"])
        for cap in result["capabilities"]:
            self.assertIsNone(cap["channel"])

    # ── 通道一致性 ────────────────────────────────────────────
    def test_api_only_advice_rejects_ui_capability(self):
        self.publish()
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{"自动化建议": "仅api自动化可用", "automation_evidence": ["dialogue"]})
        self.assertIn("api 通道", str(ctx.exception))

    def test_api_only_advice_accepts_api_capability(self):
        self.publish()
        result = self.evaluate(**{"自动化建议": "仅api自动化可用", "automation_evidence": ["buy"]})
        self.assertTrue(result["verified"])
        self.assertTrue(result["ready"])

    def test_ui_advice_rejects_api_function(self):
        self.publish()
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{"自动化建议": "UI自动化可用", "automation_evidence": ["buy", "chat"]})
        self.assertIn("ui 通道", str(ctx.exception))

    def test_ui_advice_accepts_ui_function_and_asset(self):
        self.publish()
        result = self.evaluate(**{"自动化建议": "UI自动化可用", "automation_evidence": ["dialogue", "chat"]})
        self.assertTrue(result["verified"])
        self.assertTrue(result["ready"])

    def test_dual_channel_requires_both_sides(self):
        self.publish()
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{"自动化建议": "双通道均可用", "automation_evidence": ["buy", "chat"]})
        self.assertIn("双通道", str(ctx.exception))
        result = self.evaluate(**{"自动化建议": "双通道均可用",
                                  "automation_evidence": ["buy", "dialogue", "chat"]})
        self.assertTrue(result["ready"])

    def test_both_channel_satisfies_api_and_ui(self):
        self.publish()
        result = self.evaluate(**{"自动化建议": "双通道均可用",
                                  "automation_evidence": ["both_check", "chat"]})
        self.assertTrue(result["ready"])

    # ── 反向证据 ──────────────────────────────────────────────
    def test_reverse_evidence_rejects_capability_that_exists(self):
        self.publish()
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{
                "是否可自动化": "是", "自动化建议": "当前框架能力不足",
                "自动化建议明细": "理由：需要买东西。缺失能力：buy、交易撮合。补齐办法：待补。"})
        message = str(ctx.exception)
        self.assertIn("buy", message)
        self.assertIn("清单中已存在", message)

    def test_reverse_evidence_allows_unknown_capability(self):
        self.publish()
        result = self.evaluate(**{
            "是否可自动化": "是", "自动化建议": "当前框架能力不足",
            "自动化建议明细": "理由：要跨服撮合。缺失能力：ledger_match、跨服房间。补齐办法：待补。"})
        self.assertFalse(result["ready"])
        self.assertTrue(any("框架缺少所需能力" in reason for reason in result["reasons"]))

    def test_reverse_evidence_ignores_pure_prose(self):
        self.publish()
        # 纯中文描述不该被误判成"清单里已存在"。
        result = self.evaluate(**{
            "是否可自动化": "是", "自动化建议": "当前框架能力不足",
            "自动化建议明细": "理由：需要把物品放到地面。缺失能力：地面物品准备、跨服房间。补齐办法：待补。"})
        self.assertFalse(result["ready"])

    def test_reverse_evidence_flags_capability_written_with_parameters(self):
        self.publish()
        # 写 buy(player, item, qty) 也指同一条已存在能力 —— 基础函数在清单里，
        # 声称"缺它的批量形态"需要人来说明，不能默认放行。
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{
                "是否可自动化": "是", "自动化建议": "当前框架能力不足",
                "自动化建议明细": "理由：要批量购买。缺失能力：buy(player, item, qty) 的批量形态。补齐办法：待补。"})
        self.assertIn("buy", str(ctx.exception))

    def test_reverse_evidence_flags_name_not_separated_from_next_prefix(self):
        self.publish()
        # 回归：能力名后面直接跟"补齐办法："（没有顿号/逗号）曾因整段是一个 token 而漏报。
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{
                "是否可自动化": "是", "自动化建议": "当前框架能力不足",
                "自动化建议明细": "理由：需要买东西。缺失能力：buy。补齐办法：待补。"})
        self.assertIn("buy", str(ctx.exception))

    def test_reverse_evidence_flags_dotted_capability_id(self):
        self.manifest["capabilities"][0]["id"] = "bridge.Bridge.buy"
        self.publish()
        # 带点的编号不能被分隔符拆碎（按 "." 切分会让 bridge.Bridge.buy 认不出来）。
        with self.assertRaises(ValueError) as ctx:
            self.evaluate(**{
                "是否可自动化": "是", "自动化建议": "当前框架能力不足",
                "自动化建议明细": "理由：要核对桥接。缺失能力：bridge.Bridge.buy。补齐办法：待补。"})
        self.assertIn("bridge.Bridge.buy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
