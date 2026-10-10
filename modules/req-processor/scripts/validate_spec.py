#!/usr/bin/env python3
"""Validate REQ data, provenance, images, questions and the nine-section spec."""

import argparse
import re
import sys
from pathlib import Path

from validation_common import (ValidationError, line_range, markdown_images,
                               nonempty, read_json, read_text, relative_path,
                               require)


def final_flags(data, label):
    require("draft" not in data or data["draft"] is False, f"{label}为草稿或draft标记不合法")
    require("update_state" not in data or data["update_state"] == "resolved", f"{label}仍需更新处理")
    require(not data.get("missing_fields") and not data.get("needs_redesign"), f"{label}仍有缺字段或待重设计标记")


def check(module_dir, project_dir=None):
    module_dir = Path(module_dir).resolve()
    output_root = module_dir.parent
    data = read_json(module_dir / "REQ清单.json")
    final_flags(data, "REQ清单")
    require(nonempty(data.get("module")), "module 不能为空")
    code = data.get("code")
    require(isinstance(code, str) and re.fullmatch(r"[A-Z]{2,4}", code), "code 必须为2–4位大写字母")
    manifest_path = output_root / "_公共" / "拆分清单.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        modules = manifest.get("modules")
        require(isinstance(modules, list) and all(isinstance(item, dict) for item in modules),
                "拆分清单 modules 必须为模块对象数组")
        matches = [item for item in modules if item.get("name") == data["module"]]
        require(len(matches) == 1 and matches[0].get("code") == code,
                "REQ模块名/code 与拆分清单不一致")
        parse_path = relative_path(output_root, matches[0].get("path"), "modules.path", within=True)
        require(parse_path == module_dir / "解析.md", "拆分清单的模块路径与当前模块不一致")
    # Keep module codes unique whenever sibling REQ lists are present.
    for sibling in output_root.glob("*/REQ清单.json"):
        if sibling.parent.resolve() != module_dir:
            try:
                other = read_json(sibling)
            except ValidationError:
                # 半写坏或旧格式的邻居只影响它自己，不能锁死本模块的校验。
                continue
            require(other.get("code") != code, f"跨模块 code 重复：{code}（{sibling.parent.name}）")
    requirements = data.get("requirements")
    questions, images = data.get("questions"), data.get("images")
    require(isinstance(requirements, list) and requirements, "requirements 必须为非空数组")
    require(isinstance(questions, list), "questions 必须为数组")
    require(isinstance(images, list), "images 必须为数组")
    # New file transcriptions retain their original fingerprint and must not
    # turn unchecked OCR into an apparently approved business requirement.
    parser_scripts = Path(__file__).resolve().parents[2] / "document-parser" / "scripts"
    if (module_dir / "解析来源.json").exists():
        sys.path.insert(0, str(parser_scripts))
        from check_transcription import check as check_transcription
        try:
            check_transcription(module_dir, requirements, project_dir=project_dir)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValidationError(f"解析核对记录无效：{exc}") from exc
    registered = set()
    for image in images:
        require(isinstance(image, dict), "images 每项必须为对象")
        path = relative_path(module_dir, image.get("path"), "images.path", within=True)
        require(path.is_relative_to(module_dir / "images"), "登记图片必须位于模块 images/ 目录")
        require(path not in registered, f"重复登记图片：{image.get('path')}")
        require(nonempty(image.get("description")) and nonempty(image.get("transcription")),
                f"图片描述与转录不能为空：{image.get('path')}")
        registered.add(path)
    disk_images = {path.resolve() for path in (module_dir / "images").rglob("*") if path.is_file()}
    require(all(path.is_relative_to(module_dir / "images") for path in disk_images),
            "images/ 图片链接越出模块 images/ 目录")
    require(disk_images == registered,
            f"images/ 磁盘与登记不一致，未登记：{sorted(str(p.relative_to(module_dir)) for p in disk_images - registered)}")
    ids, ac_ids, counts = {}, set(), {"active": 0, "blocked": 0, "deprecated": 0}
    for req in requirements:
        require(isinstance(req, dict), "requirements 每项必须为对象")
        final_flags(req, "REQ条目")
        req_id = req.get("id")
        match = re.fullmatch(rf"REQ_{code}_([0-9]{{3,}})", req_id) if isinstance(req_id, str) else None
        require(match is not None and int(match.group(1)) > 0, f"REQ编号无效：{req_id}")
        require(req_id not in ids, f"重复REQ编号：{req_id}")
        require(all(nonempty(req.get(key)) for key in ("title", "description", "kind")), f"{req_id}: title/description/kind 不能为空")
        require(req["kind"] in ("functional", "constraint", "nonfunctional"),
                f"{req_id}: kind 必须为 functional/constraint/nonfunctional")
        status = req.get("status")
        require(isinstance(status, str) and status in counts, f"{req_id}: status 必须为 active/blocked/deprecated")
        counts[status] += 1
        ids[req_id] = req
        criteria = req.get("acceptance_criteria")
        require(isinstance(criteria, list), f"{req_id}: acceptance_criteria 必须为数组")
        require(status != "active" or criteria, f"{req_id}: active 需求至少一个验收条件")
        for ac in criteria:
            require(isinstance(ac, dict), f"{req_id}: AC必须为对象")
            final_flags(ac, "AC条目")
            ac_id = ac.get("id")
            match_ac = re.fullmatch(rf"AC_{code}_{match.group(1)}_([0-9]{{2,}})", ac_id) if isinstance(ac_id, str) else None
            require(match_ac is not None and int(match_ac.group(1)) > 0, f"{req_id}: AC编号无效或不属于该REQ：{ac_id}")
            require(ac_id not in ac_ids, f"重复AC编号：{ac_id}")
            require(nonempty(ac.get("text")), f"{ac_id}: text 不能为空")
            ac_ids.add(ac_id)
        source = req.get("source")
        require(isinstance(source, dict), f"{req_id}: source 必须为对象")
        source_path = relative_path(output_root, source.get("file"), f"{req_id}: source.file")
        source_lines = read_text(source_path).splitlines()
        start, end = line_range(source.get("lines"), len(source_lines), req_id)
        quote = source.get("quote")
        require(nonempty(quote) and quote in "\n".join(source_lines[start - 1:end]), f"{req_id}: quote 不在指定源行中")
        linked_images = source.get("images")
        require(isinstance(linked_images, list), f"{req_id}: source.images 必须为数组")
        linked = set()
        for image in linked_images:
            path = relative_path(module_dir, image, f"{req_id}: source.images", within=True)
            require(path in registered, f"{req_id}: 图片未登记 {image}")
            require(path not in linked, f"{req_id}: 图片引用重复 {image}")
            linked.add(path)
    question_ids = set()
    for question in questions:
        require(isinstance(question, dict), "questions 每项必须为对象")
        question_id = question.get("id")
        match = re.fullmatch(rf"Q_{code}_([0-9]{{3,}})", question_id) if isinstance(question_id, str) else None
        require(match is not None and int(match.group(1)) > 0 and question_id not in question_ids,
                f"问题编号无效或重复：{question_id}")
        question_ids.add(question_id)
        require(question.get("priority") in ("P0", "P1", "P2"), f"{question_id}: priority 无效")
        require(question.get("status") in ("open", "resolved"), f"{question_id}: status 无效")
        require(nonempty(question.get("text")), f"{question_id}: text 不能为空")
        require(isinstance(question.get("resolution"), str), f"{question_id}: resolution 必须为字符串")
        if question["status"] == "resolved":
            require(nonempty(question["resolution"]), f"{question_id}: resolved 问题须有 resolution")
        affected = question.get("affected_requirements")
        require(isinstance(affected, list) and all(isinstance(item, str) for item in affected),
                f"{question_id}: affected_requirements 必须为REQ编号数组")
        require(len(affected) == len(set(affected)) and set(affected) <= set(ids),
                f"{question_id}: 受影响REQ重复或不存在")
        if question["priority"] == "P0" and question["status"] == "open":
            scope = affected or [key for key, req in ids.items() if req["status"] != "deprecated"]
            unblocked = [key for key in scope if ids[key]["status"] != "blocked"]
            require(not unblocked, f"{question_id}: P0 open 受影响需求必须 blocked：{unblocked}")
    spec = read_text(module_dir / "需求规范.md")
    sections = re.findall(r"^##\s+(\d+)\.\s+\S.*$", spec, flags=re.MULTILINE)
    require(sections == [str(n) for n in range(9)], "需求规范必须按顺序含且仅含 ## 0. 至 ## 8. 九节")
    definitions = re.findall(r"\*\*(REQ_[A-Z]{2,4}_[0-9]{3,})\*\*", spec)
    require(len(definitions) == len(set(definitions)), "需求规范中REQ定义重复（普通引用请勿加粗）")
    require(set(definitions) == set(ids),
            f"需求规范REQ定义集合与JSON不一致，缺失：{sorted(set(ids)-set(definitions))}；多余：{sorted(set(definitions)-set(ids))}")
    for path in markdown_images(spec, module_dir, "需求规范.md"):
        require(path in registered, f"需求规范引用未登记图片：{path.name}")
    parse_file = module_dir / "解析.md"
    if parse_file.exists():
        for path in markdown_images(read_text(parse_file), module_dir, "解析.md"):
            require(path in registered, f"解析底稿引用未登记图片：{path.name}")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--project-dir", type=Path, help="隔离发布校验时指定原用户项目，核对解析来源指纹")
    args = parser.parse_args()
    try:
        counts = check(args.module_dir, project_dir=args.project_dir)
    except ValidationError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"需求规范结构校验通过：可设计 {counts['active']} 条；待澄清阻塞 {counts['blocked']} 条；废弃 {counts['deprecated']} 条。")
    if not counts["active"]:
        print("当前没有可设计需求；结构校验通过不代表已确认范围可生成正式用例。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
