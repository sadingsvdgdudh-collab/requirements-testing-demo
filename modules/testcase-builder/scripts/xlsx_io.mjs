// Author only derived workbooks using the documented Artifact Tool API.
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

async function artifactTool() {
  try {
    return await import("@oai/artifact-tool");
  } catch (firstError) {
    if (!process.env.QA_NODE_MODULES) {
      throw new Error("缺少 @oai/artifact-tool。请使用可用依赖运行时，或设置 QA_NODE_MODULES 为其 node_modules 路径。", { cause: firstError });
    }
    try {
      const require = createRequire(import.meta.url);
      const entry = require.resolve("@oai/artifact-tool", { paths: [path.resolve(process.env.QA_NODE_MODULES)] });
      return await import(pathToFileURL(entry).href);
    } catch (error) {
      throw new Error("QA_NODE_MODULES 中无法解析 @oai/artifact-tool，请检查依赖路径。", { cause: error });
    }
  }
}

function literal(value) {
  // Excel formulas are never inferred from user-authored text.
  return typeof value === "string" && value.startsWith("=") ? "'" + value : value;
}

function visualLength(value) {
  return [...String(value)].reduce((length, char) => length + (char.codePointAt(0) > 255 ? 2 : 1), 0);
}

function rowHeight(values, widths) {
  const count = Math.max(...values.map((value, index) => String(value).split("\n").reduce((sum, line) => sum + Math.max(1, Math.ceil(visualLength(line) / Math.max(4, widths[index] - 2))), 0)));
  const height = Math.max(48, count * 15 + 10);
  if (height > 405) throw new Error("单条用例文字过长，超过Excel可显示行高，请精简并拆为可执行步骤后重试。");
  return height;
}

async function preview(workbook, sheetName, range, suffix) {
  if (!process.env.QA_XLSX_PREVIEW_DIR) return;
  const directory = path.resolve(process.env.QA_XLSX_PREVIEW_DIR);
  await fs.mkdir(directory, { recursive: true });
  const image = await workbook.render({ sheetName, range, scale: 1.5, format: "png" });
  await fs.writeFile(path.join(directory, suffix + ".png"), new Uint8Array(await image.arrayBuffer()));
}

// 用例主表：13 列契约。无论是否追加说明工作表都走同一实现，避免"有没有附加表"
// 改变主表的列宽、行高与冻结窗格——追加追溯表不应降级主表观感。
function writeCaseSheet(workbook, { name, rows, widths }) {
  const sheet = workbook.worksheets.add(name);
  const lastRow = rows.length;
  const letter = String.fromCharCode(64 + widths.length);
  sheet.getRange(`A1:${letter}${lastRow}`).values = rows.map(row => row.map(literal));
  const body = sheet.getRange(`A1:${letter}${lastRow}`);
  body.format.font = { name: "Arial", size: 10, color: "#172B4D" };
  body.format.wrapText = true;
  body.format.verticalAlignment = "top";
  body.format.horizontalAlignment = "left";
  body.setNumberFormat("@");
  widths.forEach((width, index) => sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width);
  sheet.tables.add(`A1:${letter}${lastRow}`, true, "QATestCases").showFilterButton = true;
  const header = sheet.getRange(`A1:${letter}1`);
  header.format.fill = "#24476B";
  header.format.font = { name: "Arial", size: 10, bold: true, color: "#FFFFFF" };
  header.format.horizontalAlignment = "center";
  header.format.verticalAlignment = "center";
  header.format.rowHeight = 32;
  for (let index = 1; index < lastRow; index++) {
    sheet.getRangeByIndexes(index, 0, 1, widths.length).format.rowHeight = rowHeight(rows[index], widths);
  }
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(1);
  return sheet;
}

// 说明工作表：两列文本，不做行高压缩，避免把长句子截断。
function writeSheet(workbook, { name, rows, table, widths }) {
  const sheet = workbook.worksheets.add(name);
  const lastRow = rows.length;
  const lastColumn = Math.max(...rows.map(row => row.length));
  const letter = String.fromCharCode(64 + lastColumn);
  sheet.getRange(`A1:${letter}${lastRow}`).values = rows.map(row => row.map(literal));
  const body = sheet.getRange(`A1:${letter}${lastRow}`);
  body.format.font = { name: "Arial", size: 10, color: "#172B4D" };
  body.format.wrapText = true;
  body.format.verticalAlignment = "top";
  body.format.horizontalAlignment = "left";
  body.setNumberFormat("@");
  widths.forEach((width, index) => sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width);
  if (table) {
    sheet.tables.add(`A1:${letter}${lastRow}`, true, table).showFilterButton = true;
  }
  const header = sheet.getRange(`A1:${letter}1`);
  header.format.fill = "#24476B";
  header.format.font = { name: "Arial", size: 10, bold: true, color: "#FFFFFF" };
  header.format.horizontalAlignment = "center";
  header.format.verticalAlignment = "center";
  header.format.rowHeight = 32;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  return sheet;
}

async function main() {
  // 必须整块拼接后一次性按 UTF-8 解码：逐块 += 会在多字节汉字跨 chunk 边界时解出 U+FFFD。
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = Buffer.concat(chunks).toString("utf8");
  const payload = JSON.parse(input);
  const { FileBlob, SpreadsheetFile, Workbook } = await artifactTool();
  let workbook;
  let sheet;
  let lastRow;
  if (payload.operation === "create") {
    workbook = Workbook.create();
    // payload.sheets（可选）在用例表之后追加说明工作表；只传 rows 时行为与旧版一致。
    const widths = [17, 9, 31, 40, 45, 34, 45, 34, 26, 17, 20, 58, 38];
    const extraSheets = payload.sheets || [];
    // 主表永远先建，保证它是第一张表；说明表只影响自己的内容。
    sheet = writeCaseSheet(workbook, { name: payload.sheetName, rows: payload.rows, widths });
    lastRow = payload.rows.length;
    for (const definition of extraSheets) {
      writeSheet(workbook, definition);
    }
  } else if (payload.operation === "review") {
    workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(payload.input));
    sheet = workbook.worksheets.getItem(payload.sheetName);
    lastRow = payload.reviews.length + 1;
    // Render the source view before the only requested change.
    await preview(workbook, payload.sheetName, `I1:M${Math.min(lastRow, 5)}`, "review-before");
    const original = sheet.getRange(`A1:M${lastRow}`).values;
    sheet.getRange(`N1:N${lastRow}`).values = [["AI评审"], ...payload.reviews.map(value => [literal(value)])];
    const review = sheet.getRange(`N1:N${lastRow}`);
    review.format.font = { name: "Arial", size: 10, color: "#172B4D" };
    review.format.wrapText = true;
    review.format.verticalAlignment = "top";
    review.format.horizontalAlignment = "left";
    review.format.columnWidth = 96;
    review.setNumberFormat("@");
    sheet.getRange("N1").format.fill = "#24476B";
    sheet.getRange("N1").format.font = { name: "Arial", size: 10, bold: true, color: "#FFFFFF" };
    sheet.getRange("N1").format.horizontalAlignment = "center";
    sheet.getRange("N1").format.verticalAlignment = "center";
    const verdicts = sheet.getRange(`N2:N${lastRow}`);
    verdicts.conditionalFormats.add("beginsWith", {
      text: "结论：需确认",
      format: { fill: "#FFF2CC", font: { color: "#7F6000" } },
    });
    verdicts.conditionalFormats.add("beginsWith", {
      text: "结论：不通过",
      format: { fill: "#FCE4D6", font: { color: "#9C0006" } },
    });
    // Row height belongs to the source; reject a review that would be clipped.
    for (let index = 0; index < payload.reviews.length; index++) {
      const required = rowHeight([payload.reviews[index]], [96]);
      const existing = sheet.getRangeByIndexes(index + 1, 0, 1, 1).format.rowHeight;
      if (typeof existing === "number" && existing + 1 < required) {
        throw new Error(`第${index + 2}行评审文字超出源行高。请缩短评审文字，保留原表布局后重试。`);
      }
    }
    if (JSON.stringify(original) !== JSON.stringify(sheet.getRange(`A1:M${lastRow}`).values)) {
      throw new Error("评审写入改变了原13列，已拒绝导出。");
    }
  } else {
    throw new Error("不支持的Excel操作。");
  }
  workbook.recalculate();
  const mainSheet = payload.sheetName || (payload.sheets && payload.sheets[0] && payload.sheets[0].name);
  await workbook.inspect({ kind: "table", range: `${mainSheet}!A1:${payload.operation === "review" ? "N" : "M"}${Math.min(lastRow, 4)}`, include: "values,formulas", tableMaxRows: 4, tableMaxCols: 14, maxChars: 1200 });
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 20 }, maxChars: 1500, summary: "final formula error scan" });
  // There are no formulas in created sheets. Existing source formulas are preserved.
  await preview(workbook, mainSheet, payload.operation === "review" ? `I1:N${Math.min(lastRow, 5)}` : `A1:G${Math.min(lastRow, 5)}`, payload.operation);
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(payload.output);
  process.stdout.write(JSON.stringify({ output: payload.output, rows: lastRow - 1, inspected: true, errorScan: Boolean(errors) }) + "\n");
}

main().catch(error => {
  process.stderr.write(`错误：${error.message}\n`);
  process.exitCode = 1;
});
