#!/usr/bin/env python3
"""Export validated test points as an editable, modern XMind ZIP workbook."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

CASE_SCRIPTS = Path(__file__).resolve().parents[2] / "testcase-builder" / "scripts"
sys.path.insert(0, str(CASE_SCRIPTS))

from qa_cases import ValidationError, percent
from point_common import (fingerprints, load_context, load_current_review,
                          point_coverage, protect_outputs)

XML_NS = "urn:xmind:xmap:xmlns:content:2.0"
ET.register_namespace("", XML_NS)


def legacy_content(content: list[dict]) -> bytes:
    """Provide the same editable tree to classic XMind's XML reader."""
    def element(parent, tag, attributes=None, text=None):
        node = ET.SubElement(parent, f"{{{XML_NS}}}{tag}", attributes or {})
        if text is not None:
            if any((ord(char) < 32 and char not in "\t\n\r") or
                   0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF)
                   for char in text):
                raise ValidationError("XMind文本包含XML不支持的控制字符。")
            node.text = text
        return node

    def add_topic(parent, data):
        attributes = {"id": data["id"]}
        if data.get("structureClass"):
            attributes["structure-class"] = data["structureClass"]
        node = element(parent, "topic", attributes)
        element(node, "title", text=data["title"])
        if data.get("notes"):
            element(element(node, "notes"), "plain", text=data["notes"]["plain"]["content"])
        children = data.get("children", {}).get("attached", [])
        if children:
            topics = element(element(node, "children"), "topics", {"type": "attached"})
            for child in children:
                add_topic(topics, child)

    root = ET.Element(f"{{{XML_NS}}}xmap-content", {"version": "2.0"})
    for data in content:
        sheet = element(root, "sheet", {"id": data["id"]})
        element(sheet, "title", text=data["title"])
        add_topic(sheet, data["rootTopic"])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")


def check_legacy_content(xml: bytes, content: list[dict]) -> None:
    root = ET.fromstring(xml)
    ns = {"x": XML_NS}

    def check_topic(node, data):
        if node is None or node.get("id") != data["id"] or node.findtext("x:title", namespaces=ns) != data["title"]:
            raise ValidationError("XMind经典格式的主题与当前数据不一致。")
        expected_note = data.get("notes", {}).get("plain", {}).get("content")
        if node.findtext("x:notes/x:plain", namespaces=ns) != expected_note:
            raise ValidationError("XMind经典格式的备注与当前数据不一致。")
        children = node.findall("x:children/x:topics[@type='attached']/x:topic", ns)
        expected_children = data.get("children", {}).get("attached", [])
        if len(children) != len(expected_children):
            raise ValidationError("XMind经典格式的层次或主题数量不一致。")
        for child, expected_child in zip(children, expected_children):
            check_topic(child, expected_child)

    sheets = root.findall("x:sheet", ns)
    if root.tag != f"{{{XML_NS}}}xmap-content" or len(sheets) != len(content):
        raise ValidationError("XMind经典格式的工作表结构不一致。")
    for sheet, expected in zip(sheets, content):
        if sheet.get("id") != expected["id"] or sheet.findtext("x:title", namespaces=ns) != expected["title"]:
            raise ValidationError("XMind经典格式的工作表与当前数据不一致。")
        check_topic(sheet.find("x:topic", ns), expected["rootTopic"])


def note(text: str) -> dict:
    """Use the plain/HTML/ops note structure from XMind's official SDK."""
    return {
        "plain": {"content": text},
        "html": {"content": {"paragraphs": [
            {"spans": [{"text": line}]} for line in text.split("\n")
        ]}},
        "ops": {"ops": [{"insert": text}]},
    }


def topic(title: str, *, text: str = "", children: list[dict] | None = None) -> dict:
    result = {"class": "topic", "id": str(uuid.uuid4()), "title": title}
    if text:
        result["notes"] = note(text)
    if children is not None:
        result["children"] = {"attached": children}
    return result


def source_text(req: dict) -> str:
    source = req["source"]
    start, end = source["lines"]
    location = f"{source['file']}，第{start}–{end}行"
    lines = [f"{req['id']} {req['title']}", f"来源：{location}",
             "原文：" + source["quote"]]
    if source["images"]:
        lines.append("关联图片（相对模块目录）：" + "、".join(source["images"]))
    return "\n".join(lines)


def point_text(point: dict, requirements: dict, review: dict | None) -> str:
    lines = [
        f"测试点编号：{point['测试点编号']}",
        f"名称：{point['测试点名称']}",
        f"优先级：{point['优先级']}",
        f"场景类型：{point['场景类型']}",
        "设计方法：" + "、".join(point["设计方法"]),
        "前置条件：" + point["前置条件"],
        "检查内容：" + point["检查内容"],
        "测试数据：" + point["测试数据"],
        "预期结果：",
        *[f"{index}. {text}" for index, text in enumerate(point["预期结果"], 1)],
        "关联需求（全部）：" + "、".join(point["关联需求"]),
        "关联验收标准（全部）：" + "、".join(point["验收标准"]),
        "", "验收标准原文：",
    ]
    linked_criteria = set(point["验收标准"])
    for req_id in point["关联需求"]:
        for criterion in requirements[req_id]["acceptance_criteria"]:
            if criterion["id"] in linked_criteria:
                lines.append(f"{criterion['id']}：{criterion['text']}")
    lines.extend(["", "需求来源（file路径相对模块的父目录）："])
    for req_id in point["关联需求"]:
        lines.extend([source_text(requirements[req_id]), ""])
    if review is None:
        lines.extend(["当前评审状态：未评审", "尚无当前测试点评审结果，不能将此图视为已通过的测试方案。"])
    else:
        lines.extend(["当前评审状态：" + review["结论"],
                      "评审原因：" + review["原因"],
                      "改进建议：" + review["建议"]])
    return "\n".join(lines)


def build_content(spec: dict, points: list[dict], review: dict | None = None,
                  source_fingerprints: dict | None = None) -> list[dict]:
    requirements = {item["id"]: item for item in spec["requirements"]}
    reviews = {item["测试点编号"]: item for item in review["reviews"]} if review is not None else {}
    # A multi-REQ point appears once, under its first linked requirement.
    grouped: dict[str, list[dict]] = {}
    for point in points:
        point_review = reviews.get(point["测试点编号"])
        if review is not None and point_review is None:
            raise ValidationError(f"当前评审缺少测试点：{point['测试点编号']}。")
        state = point_review["结论"] if point_review is not None else "未评审"
        title = f"{point['测试点编号']} {point['测试点名称']} [{point['优先级']}｜{state}]"
        grouped.setdefault(point["关联需求"][0], []).append(
            topic(title, text=point_text(point, requirements, point_review))
        )
    branches = []
    # Preserve the requirement list's ordering and the points' input ordering.
    for req in spec["requirements"]:
        if req["id"] in grouped:
            branches.append(topic(f"{req['id']} {req['title']}",
                                  text=source_text(req), children=grouped[req["id"]]))
    coverage = point_coverage(spec, points)
    req_coverage = coverage["requirement_coverage"]
    ac_coverage = coverage["acceptance_coverage"]
    root_lines = [
        f"模块：{spec['module']}（{spec['code']}）", f"测试点数：{len(points)}",
        "当前评审：" + ("已读取当前测试点评审结果，各点结论见标题和备注。" if review is not None else "未评审。"),
        f"REQ关联覆盖：{req_coverage['covered']}/{req_coverage['total']}，{percent(req_coverage['rate'])}。",
        f"验收标准关联覆盖：{ac_coverage['covered']}/{ac_coverage['total']}，{percent(ac_coverage['rate'])}。",
        "未关联REQ：" + ("、".join(req_coverage["missing"]) or "无。"),
        "未关联验收标准：" + ("、".join(ac_coverage["missing"]) or "无。"),
        "关联覆盖数字仅说明映射完整性，不能证明业务正确、测试充分或被测软件已通过测试。",
        "多需求测试点按首个关联REQ分组且只出现一次；完整REQ/AC、原文来源及评审意见在每个测试点的备注中。",
    ]
    if review is not None:
        counts = {state: sum(item["结论"] == state for item in reviews.values())
                  for state in ("通过", "需确认", "不通过")}
        root_lines.append("评审结论：" + "；".join(f"{state}{count}条" for state, count in counts.items()) + "。")
        root_lines.append("评审整体结论：" + ("已通过设计就绪检查。" if review["summary"]["ready"]
                                              else "尚有未通过或待补充内容，详见测试点评审报告。"))
    for status, label in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        excluded = coverage["excluded"][status]
        if excluded:
            root_lines.extend(["", f"{label}（不计入关联覆盖分母）："])
            root_lines.extend(f"{item['id']} {item['title']}：{item['reason']}" for item in excluded)
    if source_fingerprints:
        root_lines.extend(["", "导出时输入指纹：",
                           *[f"{key}：{value}" for key, value in source_fingerprints.items()]])
    root = topic(spec["module"], text="\n".join(root_lines), children=branches)
    root["structureClass"] = "org.xmind.ui.logic.right"
    return [{"class": "sheet", "id": str(uuid.uuid4()),
             "title": f"{spec['module']}测试点", "rootTopic": root}]


def check_archive(path: Path, expected: list[dict]) -> None:
    """Verify the ZIP and its modern workbook structure before publication."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 4 or set(names) != {"content.json", "content.xml", "metadata.json", "manifest.json"}:
            raise ValidationError("XMind归档文件列表不完整或重复。")
        if archive.testzip() is not None:
            raise ValidationError("XMind归档校验失败。")
        content = json.loads(archive.read("content.json").decode("utf-8"))
        metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if content != expected or not isinstance(metadata, dict):
            raise ValidationError("XMind归档内容与导出数据不一致。")
        if manifest != {"file-entries": {"content.json": {}, "metadata.json": {}}}:
            raise ValidationError("XMind归档manifest无效。")
        check_legacy_content(archive.read("content.xml"), expected)
    identifiers = set()

    def inspect(item: dict, expected_class: str) -> None:
        identifier = item.get("id")
        if item.get("class") != expected_class or not isinstance(item.get("title"), str):
            raise ValidationError("XMind主题或工作表结构无效。")
        if not isinstance(identifier, str) or identifier in identifiers:
            raise ValidationError("XMind工作表/主题ID为空或重复。")
        try:
            uuid.UUID(identifier)
        except ValueError as exc:
            raise ValidationError("XMind工作表/主题ID不是有效UUID。") from exc
        identifiers.add(identifier)
        for child in item.get("children", {}).get("attached", []):
            inspect(child, "topic")

    for sheet in content:
        inspect(sheet, "sheet")
        inspect(sheet["rootTopic"], "topic")


def export_archive(output: Path, content: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(dir=output.parent, prefix=".qa-xmind-", suffix=".xmind")
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        payloads = {
            "content.json": content,
            "metadata.json": {},
            "manifest.json": {"file-entries": {"content.json": {}, "metadata.json": {}}},
        }
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in payloads.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
            archive.writestr("content.xml", legacy_content(content))
        check_archive(temporary, content)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="输出.xmind路径，默认模块目录/测试点.xmind")
    parser.add_argument("--review", type=Path, help="显式指定当前评审结果；默认探测模块目录/测试点评审结果.json")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        output = (args.output or module_dir / "测试点.xmind").resolve()
        if output.suffix.lower() != ".xmind":
            raise ValidationError("XMind输出文件必须以 .xmind 结尾。")
        review_path = args.review or module_dir / "测试点评审结果.json"
        protect_outputs(module_dir, [output], extra_inputs=(review_path,))
        before = fingerprints(module_dir)
        spec, _, points = load_context(module_dir)
        review = load_current_review(module_dir, review_path=args.review)
        # Source paths follow the validated REQ schema: relative to output root.
        protected_sources = {(module_dir.parent / item["source"]["file"]).resolve()
                             for item in spec["requirements"]}
        protected_sources.update((module_dir / image["path"]).resolve()
                                 for image in spec["images"])
        if output in protected_sources:
            raise ValidationError("XMind输出路径不能覆盖需求原文或来源图片。")
        if fingerprints(module_dir) != before:
            raise ValidationError("读取期间需求或测试点发生变化，请重新导出。")
        content = build_content(spec, points, review, before)
        export_archive(output, content)
    except (ValidationError, OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"XMind导出失败：{exc}", file=sys.stderr)
        return 1
    print(f"已生成可编辑XMind：{output}（{len(points)}个测试点，{'包含当前逐点评审结论' if review is not None else '未评审'}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
