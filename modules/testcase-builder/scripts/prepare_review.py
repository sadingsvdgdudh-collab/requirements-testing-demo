#!/usr/bin/env python3
"""Create a fresh, bound review form without converting or modifying old judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from qa_cases import ValidationError, cli_error, load_json, validate_cases, validate_spec
from review_context import build_review_template, protected_inputs, review_fingerprints


def create_template(module_dir: Path, output: Path | None = None) -> Path:
    module = Path(module_dir).resolve()
    destination = (output or module / "评审待填写.json").resolve()
    if os.path.lexists(output or module / "评审待填写.json"):
        raise ValidationError("评审待填写文件已存在；请使用新路径，不能覆盖旧评审或输入文件。")
    before = {name: hashlib.sha256((module / name).read_bytes()).hexdigest()
              for name in ("REQ清单.json", "用例数据.json")}
    spec = validate_spec(module)
    cases = validate_cases(load_json(module / "用例数据.json"), spec, module_dir=module)
    if destination in protected_inputs(spec, module):
        raise ValidationError("评审表输出不可覆盖需求、用例、原件、来源或框架能力清单。")
    template = build_review_template(spec, cases, module)
    current = {name: hashlib.sha256((module / name).read_bytes()).hexdigest() for name in before}
    if current != before or review_fingerprints(spec, cases, module) != template["reviewed_source"]:
        raise ValidationError("生成表单期间输入已改变，请重新读取并开始新评审。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".qa-review-", delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(template, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish the complete file only if the path is still absent.
        os.link(temp_path, destination)
        published = True
        current = {name: hashlib.sha256((module / name).read_bytes()).hexdigest() for name in before}
        if current != before or review_fingerprints(spec, cases, module) != template["reviewed_source"]:
            raise ValidationError("生成表单期间输入已改变；已撤回未填写表单，请重新开始评审。")
    except FileExistsError as exc:
        raise ValidationError("评审表输出已存在，未覆盖任何文件。") from exc
    except Exception:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        path = create_template(args.module_dir, args.output)
        print(f"已生成新评审待填写表：{path.name}。未填写结论；须实际评审当前材料后补齐全部判断，并将draft设为false。")
        return 0
    except (ValidationError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
