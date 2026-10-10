import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

function literal(value) {
  if (value && typeof value === "object") return new Date(value.value + (value.type === "date" ? "T00:00:00Z" : ""));
  return typeof value === "string" && value.startsWith("=") ? "'" + value : value;
}

function values(sheet, block, autoDateFormat = false) {
  sheet.getRange(block.range).values = block.data.map(row => row.map(literal));
  block.data.forEach((row, r) => row.forEach((value, c) => {
    if (autoDateFormat && value && typeof value === "object") sheet.getRange(block.range).getCell(r, c).setNumberFormat(value.type === "date" ? "yyyy-mm-dd" : "yyyy-mm-dd hh:mm");
  }));
}

function format(sheet, block) {
  const range = sheet.getRange(block.range);
  // Use the documented setters, updating only the explicitly supplied properties.
  for (const [key, value] of Object.entries(block.format)) range.format[key] = value;
}

function table(sheet, item) {
  const target = sheet.getRange(item.range);
  const existing = sheet.tables.items;
  if (existing.some(t => t.name === item.name)) throw new Error(`重复表格名：${item.name}`);
  // Python preflight checked every existing/requested table range for overlaps.
  const obj = sheet.tables.add(item.range, item.has_headers !== false, item.name);
  if (item.style) obj.style = item.style;
  obj.showFilterButton = item.filter !== false;
  return target;
}

function boundsOf(address) {
  const pair = address.replaceAll("$", "").split(":");
  const parse = cell => {
    const match = /^([A-Z]+)(\d+)$/i.exec(cell);
    if (!match) throw new Error(`范围无效：${address}`);
    let col = 0; for (const c of match[1].toUpperCase()) col = col * 26 + c.charCodeAt(0) - 64;
    return { col, row: Number(match[2]) };
  };
  const a = parse(pair[0]), b = parse(pair[1] ?? pair[0]);
  return { left: a.col, top: a.row, right: b.col, bottom: b.row };
}
function overlap(a, b) { return a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top; }

function chart(sheet, item) {
  const area = boundsOf(item.position.join(":"));
  const ranges = item.ranges ?? [item.range];
  if (ranges.some(range => overlap(area, boundsOf(range)))) throw new Error("图表覆盖其数据范围，请留出空白图表区。");
  const obj = sheet.charts.add(item.type, item.ranges ? ranges.map(address => sheet.getRange(address)) : sheet.getRange(item.range));
  obj.setPosition(...item.position);
  obj.title = item.title;
  obj.titleTextStyle.fontSize = 12;
  obj.titleTextStyle.typeface = "Arial";
  obj.legend = { position: "top", textStyle: { typeface: "Arial", fontSize: 10 } };
  if (!["pie", "doughnut"].includes(item.type)) {
    obj.xAxis = { axisType: "textAxis", textStyle: { typeface: "Arial", fontSize: 10 } };
    obj.yAxis = { numberFormatCode: item.number_format ?? "General", numberFormatSourceLinked: false, textStyle: { typeface: "Arial", fontSize: 10 } };
  }
  if (item.colors) obj.series.items.forEach((series, index) => {
    const color = item.colors[index % item.colors.length];
    series.fill = color;
    if (item.type === "line") series.line = { fill: color, style: "solid", width: 2 };
  });
  if (!obj.series.items.length || obj.series.items.some(series => !series.formula || !series.categoryFormula)) {
    throw new Error("图表未生成数据/类别范围引用，请检查标题和数据排列。");
  }
}

function buildSheet(sheet, spec, fresh) {
  if (fresh) sheet.showGridLines = spec.show_gridlines ?? false;
  for (const block of spec.values ?? []) values(sheet, block, fresh);
  for (const block of spec.formulas ?? []) sheet.getRange(block.range).formulas = block.data;
  if (fresh) {
    const used = sheet.getUsedRange();
    if (used) {
      used.format.font = { name: "Arial", size: 10, color: "#172B4D" };
      used.format.verticalAlignment = "center";
    }
  }
  for (const block of spec.formats ?? []) format(sheet, block);
  for (const block of spec.column_widths ?? []) sheet.getRange(block.range).format.columnWidth = block.width;
  for (const block of spec.row_heights ?? []) sheet.getRange(block.range).format.rowHeight = block.height;
  if (spec.freeze_rows) sheet.freezePanes.freezeRows(spec.freeze_rows);
  if (spec.freeze_columns) sheet.freezePanes.freezeColumns(spec.freeze_columns);
  for (const item of spec.tables ?? []) table(sheet, item);
  for (const item of spec.charts ?? []) chart(sheet, item);
}

async function previews(workbook, names, directory, prefix, ranges = {}) {
  if (!directory) return;
  await fs.mkdir(directory, { recursive: true });
  for (const name of names) {
    const preview = await workbook.render({ sheetName: name, ...(ranges[name] ? { range: ranges[name] } : { autoCrop: "all" }), scale: 1.5, format: "png" });
    const safe = name.replace(/[^\p{L}\p{N}_-]/gu, "_");
    await fs.writeFile(path.join(directory, `${prefix}-${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}

function refreshExistingCharts(workbook, bindings) {
  // Imported chart caches in this runtime can be empty. Rebind the original
  // contiguous cells with the documented API and restore original series styles.
  for (const item of bindings ?? []) {
    const obj = workbook.worksheets.getItem(item.sheet).charts.items[item.index];
    obj.setData(workbook.worksheets.getItem(item.source_sheet).getRange(item.range));
    obj.series.items.forEach((series, index) => {
      if (item.styles[index].fill) series.fill = item.styles[index].fill;
      if (item.styles[index].line) series.line = item.styles[index].line;
    });
  }
}

async function main() {
  // 整块拼接后一次性解码，避免多字节汉字跨 chunk 边界被拆成 U+FFFD。
  const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
  const input = Buffer.concat(chunks).toString("utf8");
  const payload = JSON.parse(input), spec = payload.spec;
  let workbook;
  if (payload.command === "create") {
    workbook = Workbook.create();
    for (const item of spec.sheets) workbook.worksheets.add(item.name);
    for (const item of spec.sheets) buildSheet(workbook.worksheets.getItem(item.name), item, true);
  } else {
    workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(payload.input));
    refreshExistingCharts(workbook, payload.chart_rebindings);
    const names = [...new Set((spec.operations ?? []).filter(op => op.op !== "add_sheet").map(op => op.sheet))];
    await previews(workbook, names, payload.preview_dir, "before", spec.preview_ranges);
    // Create all requested new sheets before cross-sheet formulas are written.
    for (const item of spec.operations ?? []) if (item.op === "add_sheet") workbook.worksheets.add(item.sheet.name);
    for (const item of spec.operations ?? []) {
      if (item.op === "add_sheet") { buildSheet(workbook.worksheets.getItem(item.sheet.name), item.sheet, true); continue; }
      const sheet = workbook.worksheets.getItem(item.sheet);
      if (item.op === "set_values" || item.op === "append_rows") values(sheet, item);
      if (item.op === "set_formulas") sheet.getRange(item.range).formulas = item.data;
      if (item.op === "format") format(sheet, item);
      if (item.op === "add_table") table(sheet, item.table);
      if (item.op === "add_chart") chart(sheet, item.chart);
    }
    refreshExistingCharts(workbook, payload.chart_rebindings);
  }
  workbook.recalculate();
  const checks = [];
  for (const item of spec.checks ?? []) {
    const actual = workbook.worksheets.getItem(item.sheet).getRange(item.cell).values[0][0];
    const expected = item.value;
    const passed = typeof expected === "number" && typeof actual === "number" ? Math.abs(actual - expected) <= (item.tolerance ?? 0) : actual === expected;
    checks.push({ sheet: item.sheet, cell: item.cell, actual, expected, passed });
    if (!passed) throw new Error(`计算核对失败：${item.sheet}!${item.cell}，实际${actual}，预期${expected}`);
  }
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, maxChars: 1500, summary: "final formula error scan" });
  await workbook.inspect({ kind: "workbook,sheet,table", tableMaxRows: 3, tableMaxCols: 8, maxChars: 2000 });
  const names = [];
  if (payload.command === "create") names.push(...spec.sheets.map(s => s.name));
  else names.push(...new Set((spec.operations ?? []).map(op => op.op === "add_sheet" ? op.sheet.name : op.sheet)));
  await previews(workbook, names, payload.preview_dir, "after", spec.preview_ranges);
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(payload.output);
  process.stdout.write(JSON.stringify({ recalculated: true, checks, error_scan: Boolean(errors), previews_requested: Boolean(payload.preview_dir) }) + "\n");
}

main().catch(error => { process.stderr.write(`错误：${error.message}\n`); process.exitCode = 1; });
