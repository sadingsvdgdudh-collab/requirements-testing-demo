#!/usr/bin/env python3
"""Archive and transcribe DOCX/PDF/images without inventing business requirements."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testcase-builder" / "scripts"))
from qa_cases import ValidationError, cli_error, write_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"w": W, "a": A}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic", ".webp"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_dependency(name):
    try:
        return __import__(name)
    except ImportError as exc:
        raise ValidationError(f"缺少{name}，请使用含该依赖的工作区Python运行时。") from exc


def immutable_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValidationError(f"不可变原件/底稿已存在且内容不同：{path}；请使用新的解析项目或模块。")
        return
    with path.open("xb") as stream:
        stream.write(payload)


def ocr_image(path, project):
    if platform.system() == "Darwin" and shutil.which("xcrun"):
        swift = Path(__file__).with_name("vision_ocr.swift")
        cache = project / "work" / "ocr-cache" / hashlib.sha256(swift.read_bytes()).hexdigest()[:12]
        binary = cache / "vision-ocr"
        if not binary.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            built = subprocess.run(["xcrun", "swiftc", "-O", "-module-cache-path", str(cache / "modules"), str(swift), "-o", str(binary)], capture_output=True, text=True, timeout=120)
            if built.returncode:
                raise ValidationError("本机Vision OCR编译失败：" + built.stderr[-1800:])
        completed = subprocess.run([str(binary), str(path)], capture_output=True, text=True, timeout=120)
        if completed.returncode:
            raise ValidationError("Vision OCR失败：" + completed.stderr.strip())
        return json.loads(completed.stdout)
    tesseract = os.environ.get("QA_TESSERACT_EXECUTABLE") or shutil.which("tesseract")
    if tesseract:
        lang = os.environ.get("QA_OCR_LANGUAGES", "chi_sim+eng")
        result = subprocess.run([tesseract, str(path), "stdout", "-l", lang, "tsv"], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValidationError("Tesseract OCR失败（检查语言包）：" + result.stderr.strip())
        groups = {}
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
            if row.get("text", "").strip():
                key = (row["block_num"], row["par_num"], row["line_num"])
                groups.setdefault(key, []).append(row)
        return {"engine": "tesseract", "needs_review": True, "lines": [
            {"text": " ".join(row["text"] for row in rows), "confidence": min(float(row["conf"]) for row in rows) / 100,
             "bbox_pixels": [int(rows[0]["left"]), int(rows[0]["top"])]} for rows in groups.values()]}
    raise ValidationError("没有可用OCR：macOS需要Vision/Swift工具，其他系统需要Tesseract和对应语言包。")


class Transcription:
    def __init__(self, project, module, stem, source):
        self.project, self.module, self.stem = project, module, stem
        self.source = source
        self.assets = project / "input" / (stem + "__assets")
        self.assets.mkdir(parents=True, exist_ok=True)
        self.lines = [f"# {source.name}转录底稿", "", "以下内容为原文件转录，来源定位及不确定项见解析来源.json。", ""]
        self.blocks, self.images, self.warnings = [], {}, []

    def block(self, text, location, kind="text", **metadata):
        start = len(self.lines) + 1
        self.lines.extend(text.splitlines() or [""])
        end = len(self.lines)
        self.lines.append("")
        self.blocks.append({"kind": kind, "location": location, "lines": [start, end], **metadata})

    def image(self, payload, filename, location):
        suffix = Path(filename).suffix.lower() or ".bin"
        name = "image_" + hashlib.sha256(payload).hexdigest()[:16] + suffix
        path = self.assets / name
        immutable_bytes(path, payload)
        self.images[name] = {"name": name, "location": location, "sha256": hashlib.sha256(payload).hexdigest()}
        self.block(f"![{filename}]({self.assets.name}/{name})", location, "image", asset=name)
        return path

    def recognize(self, path, location, mode):
        if mode == "off":
            self.warnings.append(f"{location}：未做OCR，图片内容需视觉转录。")
            return
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            self.warnings.append(f"{location}：{path.suffix}图片已保留，当前OCR不支持，需视觉核对。")
            return
        result = ocr_image(path, self.project)
        text = "\n".join(line["text"] for line in result["lines"])
        self.block(text or "[未识别到文字，需视觉核对]", location, "ocr", ocr=result, needs_review=True)
        self.warnings.append(f"{location}：OCR转录需核对文字、数字与阅读顺序，置信度不等于准确率。")


def word_text(element):
    pieces = []
    for node in element.iter():
        if node.tag == f"{{{W}}}t":
            pieces.append(node.text or "")
        elif node.tag == f"{{{W}}}tab":
            pieces.append("\t")
        elif node.tag in (f"{{{W}}}br", f"{{{W}}}cr"):
            pieces.append("\n")
        elif node.tag == f"{{{W}}}delText":
            pieces.append("[删除文字：" + (node.text or "") + "]")
    return "".join(pieces)


def table_markdown(rows):
    if not rows:
        return "[空表格]"
    width = max(len(row) for row in rows)
    def line(row):
        cells = [("" if value is None else str(value)).replace("|", "\\|").replace("\n", "<br>") for value in row]
        return "| " + " | ".join(cells + [""] * (width - len(cells))) + " |"
    return "\n".join([line(rows[0]), "| " + " | ".join(["---"] * width) + " |", *[line(row) for row in rows[1:]]])


def docx_images(archive, element, rels, transcript, location, mode):
    nodes = element.findall(".//a:blip", NS)
    nodes += [n for n in element.iter() if n.tag.endswith("}imagedata")]
    for node in nodes:
        rid = node.get(f"{{{R}}}embed") or node.get(f"{{{R}}}link") or node.get(f"{{{R}}}id")
        rel = rels.get(rid)
        if not rel:
            transcript.warnings.append(f"{location}：图片关系{rid}缺失。")
            continue
        if rel.get("TargetMode") == "External":
            transcript.warnings.append(f"{location}：外部图片未下载，需用户提供原图。")
            continue
        target = posixpath.normpath(posixpath.join("word", rel["Target"]))
        if target.startswith("../") or target.startswith("/") or target not in archive.namelist():
            raise ValidationError("DOCX图片关系越界或文件不存在。")
        image = transcript.image(archive.read(target), posixpath.basename(target), location)
        transcript.recognize(image, location, mode)


def parse_docx(source, transcript, mode):
    with zipfile.ZipFile(source) as archive:
        if archive.testzip():
            raise ValidationError("DOCX归档损坏。")
        doc = ET.fromstring(archive.read("word/document.xml"))
        rels = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            for item in ET.fromstring(archive.read("word/_rels/document.xml.rels")):
                rels[item.get("Id")] = item.attrib
        body = doc.find("w:body", NS)
        if body is None:
            raise ValidationError("DOCX缺少文档正文。")
        if any(node.tag.endswith("}oMath") or node.tag.endswith("}altChunk") for node in doc.iter()):
            transcript.warnings.append("DOCX含公式或外部正文对象，结构未完整转为文字，必须对照原件核对。")
        if doc.find(".//w:ins", NS) is not None or doc.find(".//w:del", NS) is not None:
            transcript.warnings.append("DOCX有未处理修订，保留可见/删除文字标记，需决定采用哪个版本。")
        for index, element in enumerate(body, 1):
            location = {"part": "word/document.xml", "body_index": index}
            if element.tag == f"{{{W}}}p":
                value = word_text(element)
                style = element.find("w:pPr/w:pStyle", NS)
                style_id = style.get(f"{{{W}}}val", "") if style is not None else ""
                heading = re.search(r"Heading([1-6])", style_id, re.I)
                numbering = element.find("w:pPr/w:numPr", NS)
                if numbering is not None or re.search(r"List|Bullet|Number", style_id, re.I):
                    transcript.warnings.append(f"正文对象{index}：Word自动列表编号未重建，已保留文字；需对照原件核对顺序和编号。")
                if heading and value:
                    value = "#" * (int(heading.group(1)) + 1) + " " + value
                if value:
                    transcript.block(value, location, "paragraph")
            elif element.tag == f"{{{W}}}tbl":
                rows, merges = [], []
                for row_number, row in enumerate(element.findall("w:tr", NS), 1):
                    values = []
                    for cell in row.findall("w:tc", NS):
                        column = len(values) + 1
                        span = cell.find("w:tcPr/w:gridSpan", NS)
                        width = int(span.get(f"{{{W}}}val", "1")) if span is not None else 1
                        if not 1 <= width <= 1000:
                            raise ValidationError("DOCX表格跨度异常。")
                        vertical = cell.find("w:tcPr/w:vMerge", NS)
                        if width > 1 or vertical is not None:
                            merges.append({"row": row_number, "column": column, "colspan": width,
                                           "vertical": vertical.get(f"{{{W}}}val", "continue") if vertical is not None else None})
                        # Include nested cells' text in source order; structure is
                        # uncertain but their content must not silently vanish.
                        values.append("\n".join(word_text(p) for p in cell.findall(".//w:p", NS)))
                        values.extend([None] * (width - 1))
                        if cell.find("w:tbl", NS) is not None:
                            transcript.warnings.append(f"正文对象{index}：嵌套表格需核对，原件已保留。")
                    rows.append(values)
                transcript.block(table_markdown(rows), location, "table", rows=rows, merges=merges)
                if merges:
                    transcript.warnings.append(f"正文对象{index}：合并单元格结构已登记，Markdown空位不代表原文缺失。")
            else:
                value = word_text(element)
                if value:
                    transcript.block(value, location, "other")
            docx_images(archive, element, rels, transcript, location, mode)
        # Headers, footers and notes are evidence too, not silent omissions.
        for name in sorted(archive.namelist()):
            if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name):
                part = ET.fromstring(archive.read(name))
                value = "\n".join(word_text(p) for p in part.findall(".//w:p", NS)).strip()
                if value:
                    transcript.block(f"### 附加部分 {name}\n" + value, {"part": name}, "supplement")
                related = "word/_rels/" + posixpath.basename(name) + ".rels"
                part_rels = {r.get("Id"): r.attrib for r in ET.fromstring(archive.read(related))} if related in archive.namelist() else {}
                docx_images(archive, part, part_rels, transcript, {"part": name}, mode)


def parse_pdf(source, transcript, mode):
    pdfplumber = require_dependency("pdfplumber")
    try:
        document = pdfplumber.open(source)
    except Exception as exc:
        raise ValidationError(f"无法读取PDF（可能有密码或文件损坏）：{type(exc).__name__}；请提供可打开的本地副本。") from exc
    with document as pdf:
        for page in pdf.pages:
            location = {"page": page.page_number}
            transcript.block(f"## 第{page.page_number}页", location, "page")
            text = page.extract_text() or ""
            if text.strip():
                transcript.block(text, location, "pdf_text")
            tables = page.find_tables()
            for index, table in enumerate(tables, 1):
                rows = table.extract()
                transcript.block("### 表格结构转录\n" + table_markdown(rows), {**location, "table": index, "bbox": list(table.bbox)}, "table", rows=rows)
            # Preserve a true page image for visual cross-check and scanned OCR.
            if page.images or mode == "force" or not text.strip():
                try:
                    rendered = page.to_image(resolution=150, antialias=True).original
                    buffer = io.BytesIO()
                    rendered.save(buffer, format="PNG")
                    image = transcript.image(buffer.getvalue(), f"page-{page.page_number}.png", location)
                except Exception as exc:
                    raise ValidationError(f"PDF第{page.page_number}页渲染失败：{exc}") from exc
                if mode != "off" or not text.strip():
                    transcript.recognize(image, location, mode)
                elif page.images:
                    transcript.warnings.append(f"PDF第{page.page_number}页含图片：页图已保存，图内规则仍需视觉核对。")
            if tables:
                transcript.warnings.append(f"PDF第{page.page_number}页表格同时保留页文本和结构转录，需核对跨页/合并情况。")
            if len(page.chars) > 0 and not text.strip():
                transcript.warnings.append(f"PDF第{page.page_number}页文字层无可靠文本，采用OCR并待核对。")


def parse_input(source, transcript, mode):
    suffix = source.suffix.lower()
    if suffix == ".docx":
        parse_docx(source, transcript, mode)
    elif suffix == ".pdf":
        parse_pdf(source, transcript, mode)
    elif suffix in IMAGE_SUFFIXES:
        image = transcript.image(source.read_bytes(), source.name, {"image": source.name})
        transcript.recognize(image, {"image": source.name}, mode)
    elif suffix in (".md", ".txt"):
        value = source.read_text(encoding="utf-8-sig")
        if "![" in value or re.search(r"<img\b", value, re.I):
            raise ValidationError("带图片的Markdown请连同本地资产按需求解析规则导入，避免丢图。")
        transcript.block(value, {"file": source.name}, "text")
    else:
        raise ValidationError("支持DOCX、PDF、PNG/JPEG/TIFF/BMP/HEIC/WebP截图及纯文本；旧.doc需先转为.docx。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--module", required=True)
    parser.add_argument("--ocr", choices=("auto", "force", "off"), default="auto")
    args = parser.parse_args()
    try:
        source = args.input.resolve()
        project = args.project_dir.resolve()
        if not source.is_file() or args.module in ("", ".", "..") or re.search(r"[/\\\x00]", args.module):
            raise ValidationError("输入必须为本地文件，模块名不能包含路径分隔符。")
        before = digest(source)
        stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", source.stem) + "_" + before[:10]
        module_dir = project / "output" / args.module
        transcript = Transcription(project, module_dir, stem, source)
        raw_copy = project / "input" / (stem + source.suffix.lower())
        immutable_bytes(raw_copy, source.read_bytes())
        parse_input(source, transcript, args.ocr)
        if digest(source) != before:
            raise ValidationError("解析期间原输入发生变化，请重新解析。")
        if not transcript.blocks:
            raise ValidationError("未提取到正文、图或OCR内容，不能伪造解析成功。")
        markdown = "\n".join(transcript.lines).rstrip() + "\n"
        draft_path = project / "input" / (stem + "__parsed.md")
        immutable_bytes(draft_path, markdown.encode("utf-8"))
        # 先判定 解析.md 冲突再落图片：否则被拒绝的重解析会把内容哈希命名的新图
        # 留在 images/ 里，且不在 解析来源.json 登记表内，该模块此后一直校验不过。
        parsed_path = module_dir / "解析.md"
        module_markdown = markdown.replace(transcript.assets.name + "/", "images/")
        immutable_bytes(parsed_path, module_markdown.encode("utf-8"))
        images = module_dir / "images"
        images.mkdir(exist_ok=True)
        for name in transcript.images:
            immutable_bytes(images / name, (transcript.assets / name).read_bytes())
        manifest = {"schema_version": "1.0", "module": args.module,
                    "original": {"file": str(raw_copy.relative_to(project)), "sha256": before, "format": source.suffix.lower()},
                    "draft": str(draft_path.relative_to(project)), "draft_sha256": digest(draft_path),
                    "blocks": transcript.blocks, "images": list(transcript.images.values()),
                    "needs_review": bool(transcript.warnings), "warnings": transcript.warnings,
                    "scope": "文件转录，不是语义需求提取或真实系统测试"}
        write_text(module_dir / "解析来源.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        lines = ["# 文档解析报告", "", f"模块：{args.module}；格式：{source.suffix.lower()}；转录块：{len(transcript.blocks)}；图像：{len(transcript.images)}。", "",
                 f"原件：{manifest['original']['file']}。SHA-256：{before}。", f"不可变底稿：{manifest['draft']}。", "",
                 "来源定位为真实PDF页码或DOCX正文对象/表格位置；不估算Word排版页码。",
                 "文本转录与表格结构并列时不表示有两条业务要求。OCR文字和复杂表格必须核对。", "", "## 待核对事项", ""]
        lines.extend(["- " + str(item) for item in transcript.warnings] or ["未发现程序可检测的缺失；仍应抽查原文和表格，不能据此认证转录完全准确。"])
        write_text(module_dir / "解析报告.md", "\n".join(lines) + "\n")
        print(f"已生成解析底稿、图片、来源和报告：{module_dir}；需核对={manifest['needs_review']}。")
        return 0
    except (ValidationError, OSError, UnicodeError, zipfile.BadZipFile, ET.ParseError, json.JSONDecodeError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
