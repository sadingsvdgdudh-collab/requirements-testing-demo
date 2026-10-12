"""Regression checks for protection detection and explicit XLSX writer selection."""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from openpyxl import Workbook

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules/testcase-builder/scripts"))
from qa_cases import HEADERS, ValidationError, read_xlsx, run_xlsx

loader = importlib.util.spec_from_file_location(
    "qa_excel_guards", PACKAGE / "modules/excel/scripts/excel_tool.py")
excel = importlib.util.module_from_spec(loader)
loader.loader.exec_module(excel)
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class WorkbookProtectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="qa-protection-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "source.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "ordinary template"
        workbook.save(self.path)
        workbook.close()

    def protection(self, part, tag, attributes):
        with zipfile.ZipFile(self.path) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(parts[part])
        element = root.find(f"{{{NS}}}{tag}")
        if element is None:
            element = ET.SubElement(root, f"{{{NS}}}{tag}")
        element.attrib.clear()
        element.attrib.update(attributes)
        parts[part] = ET.tostring(root)
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in parts.items():
                archive.writestr(name, contents)

    def test_default_openpyxl_workbook_is_not_protected(self):
        original = self.path.read_bytes()
        self.assertNotIn("保护设置", excel.preflight(self.path, writing=True))
        self.assertEqual(self.path.read_bytes(), original)

    def test_explicitly_disabled_protection_is_allowed(self):
        self.protection("xl/workbook.xml", "workbookProtection",
                        {"lockStructure": "false", "lockWindows": "0", "lockRevision": "0"})
        self.protection("xl/worksheets/sheet1.xml", "sheetProtection",
                        {"sheet": "false", "objects": "true", "formatCells": "1"})
        self.assertNotIn("保护设置", excel.preflight(self.path, writing=True))

    def test_active_workbook_and_sheet_protection_are_refused(self):
        for part, tag, attributes in (
            ("xl/workbook.xml", "workbookProtection", {"lockStructure": "1"}),
            ("xl/worksheets/sheet1.xml", "sheetProtection", {"sheet": "true"}),
        ):
            with self.subTest(tag=tag):
                self.protection(part, tag, attributes)
                with self.assertRaisesRegex(excel.ExcelError, "保护设置"):
                    excel.preflight(self.path, writing=True)
                self.protection(part, tag, {})

    def test_password_metadata_is_not_silently_discarded(self):
        self.protection("xl/workbook.xml", "workbookProtection",
                        {"lockStructure": "false", "workbookPassword": "ABCD"})
        with self.assertRaisesRegex(excel.ExcelError, "保护设置"):
            excel.preflight(self.path, writing=True)


class WriterSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="qa-writer-mode-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.output = self.folder / "cases.xlsx"
        self.payload = {"operation": "create", "sheetName": "用例",
                        "rows": [HEADERS, ["TC_PRO_001"] + [""] * 12]}

    def test_node_only_missing_node_preserves_existing_output(self):
        self.output.write_bytes(b"previous deliverable")
        with patch.dict(os.environ, {"QA_XLSX_FALLBACK": "node",
                                     "QA_NODE_EXECUTABLE": "/missing/qa-node"}), \
                patch("qa_cases.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValidationError, "禁止.*回退"):
                run_xlsx(self.payload, self.output)
        self.assertEqual(self.output.read_bytes(), b"previous deliverable")
        self.assertEqual(list(self.folder.glob(".qa-xlsx-*")), [])

    def test_node_only_missing_artifact_tool_does_not_use_python(self):
        with patch.dict(os.environ, {"QA_XLSX_FALLBACK": "node"}), \
                patch("qa_cases._run_xlsx_node", return_value="缺少 @oai/artifact-tool"), \
                patch("qa_cases._run_xlsx_python") as fallback:
            with self.assertRaisesRegex(ValidationError, "禁止.*回退"):
                run_xlsx(self.payload, self.output)
            fallback.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_default_mode_still_falls_back_when_dependencies_are_missing(self):
        with patch.dict(os.environ, {"QA_XLSX_FALLBACK": ""}), \
                patch("qa_cases._run_xlsx_node", return_value="缺少 Node.js"):
            run_xlsx(self.payload, self.output)
        self.assertEqual(read_xlsx(self.output)["rows"], self.payload["rows"])

    def test_python_mode_does_not_try_node(self):
        with patch.dict(os.environ, {"QA_XLSX_FALLBACK": "python"}), \
                patch("qa_cases._run_xlsx_node") as node:
            run_xlsx(self.payload, self.output)
            node.assert_not_called()
        self.assertEqual(read_xlsx(self.output)["rows"], self.payload["rows"])

    def test_invalid_mode_leaves_no_temporary_file(self):
        with patch.dict(os.environ, {"QA_XLSX_FALLBACK": "invalid"}):
            with self.assertRaises(ValidationError):
                run_xlsx(self.payload, self.output)
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
