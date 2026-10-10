"""可读性门禁：阈值有文档依据，判定可复算。

前置条件难读长期只是主观感受；本组用例把它固定成三个可复算指标，
并确保阈值写进文档、字段写法有依据、公共段机制真的能把告警消掉。
"""

import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from case_readability import (OBSERVATION_ANCHORS, THRESHOLDS, check_cases, render_report,
                              split_fragments)
from qa_cases import ValidationError, merged_precondition, module_shared_lines


def make_case(case_id, precondition, expected=None, name="示例用例"):
    return {
        "用例编号": case_id,
        "用例名称": name,
        "前置条件": precondition,
        "预期结果": expected if expected is not None else ["列表中出现该条目。"],
    }


class ReadabilityContractTests(unittest.TestCase):
    def test_missing_field_reports_warning_instead_of_crashing(self):
        """缺字段的用例必须进告警清单，不能让整份检查崩在 KeyError 上。"""
        report = check_cases({"module": "样例"}, [{"用例编号": "TC_X_001", "预期结果": ["列表出现该条目。"]}])
        self.assertEqual(report["status"], "warned")
        self.assertEqual([item["用例编号"] for item in report["findings"]], ["TC_X_001"])

    def test_thresholds_are_documented(self):
        """阈值必须写进用例模板与数据契约，避免只存在于代码里。"""
        template = (PACKAGE / "modules/testcase-builder/references/case-template.md").read_text(encoding="utf-8")
        contract = (PACKAGE / "modules/req-processor/references/data-contract.md").read_text(encoding="utf-8")
        for name in ("unique_ratio_min", "max_sentence_chars", "expected_anchor_rate_min"):
            self.assertIn(name, template, f"用例模板未登记阈值：{name}")
            self.assertIn(name, contract, f"数据契约未登记阈值：{name}")
        self.assertIn("case_readability.py", contract, "数据契约未登记可读性检查入口")
        self.assertIn("module_preconditions", template)
        self.assertIn("evidence_setup", template)

    def test_fragment_split_is_shared_with_validator(self):
        """检查器与校验器必须用同一套切分，否则阈值会漂移。"""
        sample = "隔离测试服；A=qaplayer，B=qaplayera\n各用例独立重置。"
        # 分号、换行与句号都结束一个片段：早期只认前两者，句号拼接的公共段会漏检。
        self.assertEqual(split_fragments(sample),
                         ["隔离测试服", "A=qaplayer，B=qaplayera", "各用例独立重置"])

    def test_duplicated_precondition_is_reported(self):
        """逐条抄公共环境的写法必须被量化报出来。"""
        shared = "隔离测试服，各用例独立重置账号状态。"
        cases = [make_case("TC_PRO_001", f"{shared}已登录后台。"),
                 make_case("TC_PRO_002", f"{shared}已进入列表页。")]
        report = check_cases({"module": "商品管理"}, cases)
        self.assertEqual(report["status"], "warned")
        module_findings = [item for item in report["findings"] if item["用例编号"] == "（模块级）"]
        self.assertTrue(module_findings, "未报出模块级公共段缺失")
        ratios = [item["指标"]["unique_ratio"] for item in report["findings"] if item["用例编号"].startswith("TC_")]
        self.assertTrue(all(ratio < THRESHOLDS["unique_ratio_min"] for ratio in ratios),
                        f"重复片段未拉低独有占比：{ratios}")
        self.assertIn(shared.rstrip("。"), report["duplicated_fragments"])

    def test_registering_shared_lines_clears_duplication(self):
        """把公共段登记到 module_preconditions 后，重复告警必须消失。"""
        shared = "隔离测试服，各用例独立重置账号状态。"
        data = {
            "module": "商品管理",
            "module_preconditions": [shared],
            "evidence_setup": ["事件取证：各观察端保存批次。"],
        }
        cases = [make_case("TC_PRO_001", "已登录后台。", ["页面出现保存成功提示。"]),
                 make_case("TC_PRO_002", "已进入列表页。", ["列表出现该条目。"])]
        report = check_cases(data, cases)
        self.assertEqual(report["duplicated_fragments"], [])
        self.assertEqual(report["findings"], [], render_report(report))
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["shared_registered"])

    def test_long_sentence_and_missing_anchor_are_reported(self):
        """超长单句与没有观察落点的预期都要报出来。"""
        long_line = ("A处于已核实禁言状态并且B在线且允许接收并且C在观察并且其余闸门全部允许"
                     "并且观察窗口已配置完成并且事件队列已就绪并且基线已保存")
        self.assertGreater(len(long_line), THRESHOLDS["max_sentence_chars"])
        cases = [make_case("TC_PRO_001", long_line, ["功能正常。"])]
        report = check_cases({"module": "商品管理"}, cases)
        warnings = "；".join(report["findings"][0]["警告"])
        self.assertIn("最长单句", warnings)
        self.assertIn("落点率", warnings)

    def test_unfinished_sentence_and_internal_identifier_are_reported(self):
        """半截话与代码黑话不得作为描述语言。"""
        cases = [make_case("TC_PRO_001", "当前框架尚未封装；openInvites 为空。", ["列表出现该条目。"])]
        report = check_cases({"module": "商品管理"}, cases)
        warnings = "；".join("；".join(item["警告"]) for item in report["findings"])
        self.assertIn("未完成句", warnings)
        self.assertIn("内部标识符", warnings)

    def test_observation_anchors_cover_required_fields(self):
        """预期落点必须包含界面、列表、记录、通道等可观察对象。"""
        for anchor in ("界面", "列表", "记录", "通道", "状态"):
            self.assertIn(anchor, OBSERVATION_ANCHORS)

    def test_shared_fields_are_optional_and_type_checked(self):
        """缺省表示未登记；写错类型必须拒绝。"""
        self.assertEqual(module_shared_lines({}), {"module_preconditions": [], "evidence_setup": []})
        with self.assertRaises(ValidationError):
            module_shared_lines({"module_preconditions": "隔离测试服"})
        with self.assertRaises(ValidationError):
            module_shared_lines({"evidence_setup": [""]})

    def test_merged_precondition_keeps_case_text_verbatim(self):
        """合并版只做拼接：原前置条件文字一字不改，另有公共段与取证要求。"""
        data = {
            "module_preconditions": ["隔离测试服。"],
            "evidence_setup": ["事件取证：保存批次。"],
        }
        case = make_case("TC_PRO_001", "A处于禁言状态；B在线。")
        merged = merged_precondition(data, case)
        self.assertIn("隔离测试服。", merged)
        self.assertIn("A处于禁言状态", merged)
        self.assertIn("B在线", merged)
        self.assertIn("取证要求：事件取证：保存批次。", merged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
