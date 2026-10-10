#!/usr/bin/env python3
"""Python 回退的 Excel 作者：在不具备 @oai/artifact-tool 时保持同一输出契约。

技能的 Excel 链路原本只走 `xlsx_io.mjs`（Node + @oai/artifact-tool）。该依赖
只在部分环境提供，缺失时整条链路不可用——而读取侧本来就用 openpyxl。
本模块用 openpyxl 实现等价的仅写入能力，因此不引入新的读取路径：

- 固定前 13 列，全部按文本写入（`set_number_format("@")`）；以 `=` 开头的用户
  文本只强制 `data_type = "s"` 按文本落盘，不加撇号前缀，也不写公式节点，
  与 Node 版同一口径。
- `create`：主表 + 可选说明工作表。
- `review`：只给第 14 列 `AI评审` 追加值，前 13 列逐字复制源表，并校验未被改动。

回退不承诺 Node 版的视觉细节（条件格式、表格筛选按钮、行高精算），
这些不影响 `run_xlsx` 的内容校验，但不应当被描述为“完全一致”。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="24476B")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name="Arial", size=10, color="172B4D")
TEXT_ALIGN = Alignment(vertical="top", horizontal="left", wrap_text=True)
HEADER_ALIGN = Alignment(vertical="center", horizontal="center", wrap_text=True)
# 与 xlsx_io.mjs 相同的列宽，保持同源输出观感一致。
CASE_WIDTHS = [17, 9, 31, 40, 45, 34, 45, 34, 26, 17, 20, 58, 38]
MAX_ROW_HEIGHT = 405


class XlsxWriteError(ValueError):
    """写入失败：消息与 Node 版保持同一语气，便于上层直接转发。"""


def visual_length(value) -> int:
    """按 xlsx_io.mjs 的口径估算显示宽度：码点 > 255 记 2。"""
    return sum(2 if ord(char) > 255 else 1 for char in str(value))


def row_height(values, widths) -> int:
    """与 Node 版同一算法，保留“文字过长拒绝导出”的既有保护。"""
    count = max(
        sum(max(1, -(-visual_length(line) // max(4, widths[index] - 2)))
            for line in str(value).split("\n"))
        for index, value in enumerate(values)
    )
    height = max(48, count * 15 + 10)
    if height > MAX_ROW_HEIGHT:
        raise XlsxWriteError("单条用例文字过长，超过Excel可显示行高，请精简并拆为可执行步骤后重试。")
    return height


def _write_rows(sheet, rows, widths, table_name=None):
    last_row = len(rows)
    last_column = max(len(row) for row in rows)
    letter = get_column_letter(last_column)
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row, start=1):
            # 以 "=" 开头的用户文本必须按文本落盘：openpyxl 会先判成公式，
            # 这里强制回字符串类型，落盘后不带公式节点。
            cell = sheet.cell(row=row_index, column=column_index, value=value)
            cell.number_format = "@"
            # 纯数字样文本（如“99.90”“1.0”“007”）必须保持文本：openpyxl 会把
            # 看起来像数字的字符串写成数值，回读就变成 99.9，触发导出内容不一致。
            if cell.data_type != "s":
                cell.data_type = "s"
            cell.font = HEADER_FONT if row_index == 1 else BODY_FONT
            cell.alignment = HEADER_ALIGN if row_index == 1 else TEXT_ALIGN
            if row_index == 1:
                cell.fill = HEADER_FILL
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 32
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    if table_name:
        from openpyxl.worksheet.table import Table, TableStyleInfo
        table = Table(displayName=table_name, ref=f"A1:{letter}{last_row}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        sheet.add_table(table)
    return sheet


def _load_rows(path: Path, sheet_name: str, columns: int):
    """按文本读回源表，作为前 13 列的复制来源与改动校验基线。"""
    workbook = load_workbook(path, data_only=False)
    if sheet_name not in workbook.sheetnames:
        raise XlsxWriteError(f"源工作簿缺少工作表：{sheet_name}。")
    sheet = workbook[sheet_name]
    rows = []
    for row in sheet.iter_rows(min_row=1, max_col=columns, values_only=True):
        if row[0] is None and all(value is None for value in row):
            continue
        rows.append(["" if value is None else str(value) for value in row])
    return rows


def write_workbook(payload: dict, output: Path) -> None:
    """按 payload 生成工作簿；契约与 xlsx_io.mjs 的 create/review 一致。"""
    operation = payload.get("operation")
    if operation == "create":
        rows = payload["rows"]
        if not rows:
            raise XlsxWriteError("没有可写入的用例行。")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = payload["sheetName"]
        _write_rows(sheet, rows, CASE_WIDTHS, table_name="QATestCases")
        # 与 Node 版一致：用例主表冻结首行首列；说明表只冻结首行。
        sheet.freeze_panes = "B2"
        for index, row in enumerate(rows[1:], start=2):
            sheet.row_dimensions[index].height = row_height(row, CASE_WIDTHS)
        for definition in payload.get("sheets") or []:
            extra = workbook.create_sheet(definition["name"])
            _write_rows(extra, definition["rows"], definition["widths"], definition.get("table"))
        workbook.save(output)
        return
    if operation == "review":
        source = Path(payload["input"])
        sheet_name = payload["sheetName"]
        reviews = payload["reviews"]
        original = _load_rows(source, sheet_name, 13)
        if len(original) != len(reviews) + 1:
            raise XlsxWriteError("源表行数与评审条数不一致，已拒绝导出。")
        workbook = load_workbook(source, data_only=False)
        sheet = workbook[sheet_name]
        # 第 14 列表头由写入器负责（源表只有 13 列），漏写会被 run_xlsx 的回读校验拒绝。
        header = sheet.cell(row=1, column=14, value="AI评审")
        header.number_format = "@"
        header.font = HEADER_FONT
        header.alignment = HEADER_ALIGN
        header.fill = HEADER_FILL
        for index, text in enumerate(reviews, start=2):
            cell = sheet.cell(row=index, column=14, value=text)
            cell.number_format = "@"
            cell.font = BODY_FONT
            cell.alignment = TEXT_ALIGN
        sheet.column_dimensions["N"].width = 96
        for index in range(2, len(reviews) + 2):
            required = row_height([reviews[index - 2]], [96])
            existing = sheet.row_dimensions[index].height
            if isinstance(existing, (int, float)) and existing + 1 < required:
                raise XlsxWriteError(f"第{index}行评审文字超出源行高。请缩短评审文字，保留原表布局后重试。")
        workbook.save(output)
        # 前 13 列必须逐字未变，与 Node 版同一门禁。
        after = _load_rows(output, sheet_name, 13)
        if after != original:
            raise XlsxWriteError("评审写入改变了原13列，已拒绝导出。")
        return
    raise XlsxWriteError("不支持的Excel操作。")


def normalize_cell(value) -> str:
    """读回比较用：openpyxl 会把纯数字文本读成数字，这里还原成文本口径。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


__all__ = ["write_workbook", "XlsxWriteError", "row_height", "visual_length", "normalize_cell"]
