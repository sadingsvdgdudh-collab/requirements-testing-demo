#!/usr/bin/env python3
"""Check that module drafts preserve every nonblank source Markdown line."""

import argparse
import hashlib
import re
import sys
from pathlib import Path

from validation_common import (INLINE_IMAGE, REFERENCE_DEF, REFERENCE_IMAGE, ValidationError,
                               canonical_images, image_path, line_range,
                               markdown_images, nonempty, read_json, read_text,
                               relative_path, require)


def check(root):
    root = Path(root).resolve()
    manifest = read_json(root / "_公共" / "拆分清单.json")
    modules, sources = manifest.get("modules"), manifest.get("sources")
    require(isinstance(modules, list) and modules, "拆分清单 modules 必须为非空数组")
    require(isinstance(sources, list) and sources, "拆分清单 sources 必须为非空数组")
    names, codes, targets, target_images = set(), set(), {}, {}
    for module in modules:
        require(isinstance(module, dict), "modules 每项必须为对象")
        name, code, path = module.get("name"), module.get("code"), module.get("path")
        require(nonempty(name) and name not in names, f"模块名称为空或重复：{name}")
        require(isinstance(code, str) and re.fullmatch(r"[A-Z]{2,4}", code)
                and code not in codes, f"模块 code 无效或重复：{code}")
        resolved = relative_path(root, path, f"模块 {name}", within=True)
        require(resolved not in targets, f"多个模块共用目标文件：{path}")
        text = read_text(resolved)
        require(bool(text.strip()), f"模块解析为空：{path}")
        target_images[resolved] = markdown_images(text, resolved.parent, path)
        targets[resolved] = canonical_images(text)
        names.add(name)
        codes.add(code)
    seen_sources, assigned_targets = set(), set()
    total_lines = 0
    for source in sources:
        require(isinstance(source, dict), "sources 每项必须为对象")
        path = relative_path(root, source.get("path"), "source.path")
        require(path not in seen_sources, f"源文件重复登记：{source.get('path')}")
        seen_sources.add(path)
        text = read_text(path)
        lines = text.splitlines()
        require(bool(text.strip()), f"源文档为空：{path}")
        markdown_images(text, path.parent, str(path))
        canonical_lines = canonical_images(text).splitlines()
        image_reference_labels = {(m.group(2) or m.group(1)).casefold()
                                  for m in REFERENCE_IMAGE.finditer(text)}
        segments = source.get("segments")
        require(isinstance(segments, list) and segments, f"{path}: segments 必须为非空数组")
        covered = set()
        for segment in segments:
            require(isinstance(segment, dict), f"{path}: segment 必须为对象")
            start, end = line_range(segment.get("lines"), len(lines), str(path))
            destinations = segment.get("targets")
            require(isinstance(destinations, list) and destinations, f"{path}: targets 必须为非空数组")
            require(all(nonempty(item) for item in destinations), f"{path}: targets 路径必须非空")
            require(len(destinations) == len(set(destinations)), f"{path}: segment targets 重复")
            original = "\n".join(canonical_lines[start - 1:end])
            segment_text = "\n".join(lines[start - 1:end])
            image_matches = list(INLINE_IMAGE.finditer(segment_text))
            image_matches += [match for match in REFERENCE_DEF.finditer(segment_text)
                              if match.group(1).casefold() in image_reference_labels]
            segment_paths = [image_path(match.group(2), path.parent, str(path))
                             for match in image_matches]
            for destination in destinations:
                target = relative_path(root, destination, "segment target", within=True)
                require(target in targets, f"segment target 未登记为模块：{destination}")
                require(original in targets[target], f"{destination}: 丢失或改写 {path.name} 第 {start}–{end} 行原文")
                # File bytes must survive an image copy, not just its filename.
                referenced_names = re.findall(r"<IMAGE:([^>]+)>", original)
                for name in referenced_names:
                    source_matches = [p for p in segment_paths if p.name == name]
                    target_matches = [p for p in target_images[target] if p.name == name]
                    require(source_matches and target_matches,
                            f"{destination}: 图片来源无法核验 {name}")
                    try:
                        source_hashes = {hashlib.sha256(p.read_bytes()).digest() for p in source_matches}
                        target_hashes = {hashlib.sha256(p.read_bytes()).digest() for p in target_matches}
                    except OSError as exc:
                        raise ValidationError(f"图片读取失败：{exc}") from exc
                    require(source_hashes <= target_hashes,
                            f"{destination}: 图片内容被改写 {name}")
                assigned_targets.add(target)
            covered.update(range(start, end + 1))
        missing = [n for n, line in enumerate(lines, 1) if line.strip() and n not in covered]
        require(not missing, f"{path}: 非空源行未分配：{missing}")
        total_lines += sum(bool(line.strip()) for line in lines)
    require(assigned_targets == set(targets), "有模块未被任何原文片段关联")
    return {"modules": len(modules), "sources": len(sources), "lines": total_lines}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = check(args.root)
    except ValidationError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"拆分校验通过：{result['sources']} 份底稿，{result['modules']} 个模块，{result['lines']} 个非空源行保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
