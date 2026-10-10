"""Python 回退 Excel 作者：与 Node 作者共用同一输出契约。

本机没有 @oai/artifact-tool 时，`run_xlsx` 会回退到 `xlsx_fallback.write_workbook`。
这条路径必须和 Node 版保持同一份内容契约，否则会出现“某个环境下能导出、
另一个环境下被回读校验拒绝”的假通过。本组用例固定该契约。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "testcase-builder" / "scripts"))
from qa_cases import HEADERS, read_xlsx
from xlsx_fallback import XlsxWriteError, row_height, visual_length, write_workbook

CASES = [
    HEADERS,
    ["TC_PRO_001", "P0", "=1+1", "已登录后台。", "1. 输入价格99.90。", "价格99.90", "列表显示99.90。",
     "", "REQ_PRO_001", "是", "双通道均可用", "理由：x\n已有函数/资源：y", ""],
    ["TC_PRO_002", "P1", "007 前导零名称", "已进入列表页。", "1. 点击保存。", "数量007", "提示保存成功。",
     "", "REQ_PRO_002", "是", "仅api自动化可用", "理由：x\n已有函数/资源：y", ""],
]


def write(payload, suffix=".xlsx"):
    handle, name = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    path = Path(name)
    write_workbook(payload, path)
    return path


class FallbackWriterTests(unittest.TestCase):
    def test_create_keeps_thirteen_columns_and_text_types(self):
        """13 列契约、文本类型与字面量规则必须与 Node 版一致。"""
        path = write({"operation": "create", "rows": CASES, "sheetName": "用例"})
        self.addCleanup(path.unlink)
        info = read_xlsx(path, sheet_name="用例")
        self.assertEqual(info["rows"][0], HEADERS)
        self.assertEqual(len(info["rows"]), 3)
        # 以 = 开头的用户文本按文本落盘：值逐字保留，且不带公式节点。
        self.assertEqual(info["rows"][1][2], "=1+1")
        self.assertFalse(info["cells"]["C2"][1], "用户文本意外写成公式")
        # 数字样文本（99.90 / 007）必须保持文本原样。
        self.assertEqual(info["rows"][1][5], "价格99.90")
        self.assertEqual(info["rows"][2][5], "数量007")
        self.assertEqual(info["rows"][1][6], "列表显示99.90。")

    def test_create_appends_shared_section_sheets(self):
        """公共段说明表追加时不改变主表列名与列数。"""
        payload = {
            "operation": "create", "rows": CASES, "sheetName": "用例",
            "sheets": [
                {"name": "公共前置条件", "rows": [["序号", "公共前置条件（全模块适用）"], ["1", "隔离测试服。"]],
                 "table": "QAModulePreconditions", "widths": [8, 110]},
                {"name": "合并前置条件",
                 "rows": [["用例编号", "用例名称", "合并前置条件（公共段＋本条独有）"],
                          ["TC_PRO_001", "=1+1", "1. 隔离测试服。\n2. 已登录后台。"]],
                 "table": "QAMergedPreconditions", "widths": [17, 31, 110]},
            ],
        }
        path = write(payload)
        self.addCleanup(path.unlink)
        info = read_xlsx(path, sheet_name="用例")
        self.assertEqual(info["rows"][0], HEADERS)
        merged = read_xlsx(path, sheet_name="合并前置条件")
        self.assertEqual(merged["rows"][1][2], "1. 隔离测试服。\n2. 已登录后台。")

    def test_create_without_shared_sheets_stays_single_sheet(self):
        """未登记公共段时保持单表输出：旧资料行为不变。"""
        path = write({"operation": "create", "rows": CASES, "sheetName": "用例"})
        self.addCleanup(path.unlink)
        info = read_xlsx(path)
        self.assertEqual(info["name"], "用例")

    def test_review_writes_header_and_preserves_first_thirteen_columns(self):
        """评审只追加第 14 列，表头与逐行评审值必须落位，前 13 列逐字不变。"""
        source = write({"operation": "create", "rows": CASES, "sheetName": "用例"})
        self.addCleanup(source.unlink)
        # 源表行高由人工执行记录决定；评审文字必须放得下，否则写入器拒绝导出。
        from openpyxl import load_workbook
        workbook = load_workbook(source)
        for row in (2, 3):
            workbook["用例"].row_dimensions[row].height = 60
        workbook.save(source)
        before = read_xlsx(source, sheet_name="用例")
        reviews = ["结论：通过\n原因：示例。\n建议：无。", "结论：需确认\n原因：示例。\n建议：补充。"]
        out = write({"operation": "review", "input": str(source), "sheetName": "用例", "reviews": reviews})
        self.addCleanup(out.unlink)
        after = read_xlsx(out, columns=14, sheet_name="用例")
        self.assertEqual(after["rows"][0][13], "AI评审")
        self.assertEqual([row[13] for row in after["rows"][1:]], reviews)
        for row_before, row_after in zip(before["rows"], after["rows"]):
            self.assertEqual(row_before, [value for value in row_after[:13]])

    def test_review_row_count_must_match(self):
        """源表行数与评审条数不一致时必须拒绝，不能静默错位。"""
        source = write({"operation": "create", "rows": CASES, "sheetName": "用例"})
        self.addCleanup(source.unlink)
        with self.assertRaises(XlsxWriteError):
            write({"operation": "review", "input": str(source), "sheetName": "用例", "reviews": ["只有一条"]})

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(XlsxWriteError):
            write({"operation": "export", "rows": CASES, "sheetName": "用例"})

    def test_oversized_case_is_rejected_like_node_version(self):
        """保留“文字过长拒绝导出”的既有保护，阈值与 Node 版同口径。"""
        with self.assertRaises(XlsxWriteError):
            row_height(["x" * 20000], [17])

    def test_visual_length_matches_node_codepoint_rule(self):
        """宽度口径与 xlsx_io.mjs 相同：码点 > 255 记 2。

        破折号、弯引号在 east_asian_width 下是 A/N，旧实现记 1、Node 记 2，
        造成同一条用例在两条写入路径上"过长拒绝"结论不同。
        """
        self.assertEqual(visual_length("ab中"), 4)
        self.assertEqual(visual_length("—"), 2)
        self.assertEqual(visual_length("“”"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
