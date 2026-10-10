#!/usr/bin/env python3
"""Validate model-authored points and publish a separate list and link report."""

from __future__ import annotations

import argparse
from pathlib import Path

from point_common import (ValidationError, cli_error, load_context, point_coverage,
                          point_coverage_report, point_sources, protect_outputs,
                          write_json, write_text)


def render_points(spec: dict, points: list[dict]) -> str:
    lines = [f"# {spec['module']}测试点清单", "", f"模块代码：{spec['code']}。测试点数：{len(points)}。", "",
             "范围为已确认的active需求；来源从REQ清单的原文引用派生。此清单尚不代表测试点评审通过，也没有执行测试。", ""]
    if not points:
        lines.extend(["当前没有测试点，请查看覆盖率报告中的可设计范围。", ""])
    for point in points:
        lines.extend([f"## {point['测试点编号']} {point['测试点名称']}", "",
                      f"优先级：{point['优先级']}；场景类型：{point['场景类型']}；设计方法：{'、'.join(point['设计方法'])}。", "",
                      f"前置条件：{point['前置条件']}", "", f"检查内容：{point['检查内容']}", "",
                      f"测试数据：{point['测试数据']}", "", "预期结果：", ""])
        lines.extend(f"{index}. {text}" for index, text in enumerate(point["预期结果"], 1))
        lines.extend(["", "关联需求：" + "、".join(point["关联需求"]),
                      "验收标准：" + "、".join(point["验收标准"]), "", "原文来源（文件路径相对于模块的上级输出目录）：", ""])
        for req in point_sources(spec, point):
            source = req["source"]
            start, end = source["lines"]
            line_info = str(start) if start == end else f"{start}–{end}"
            lines.extend([f"- {req['id']} {req['title']}：{source['file']}，第{line_info}行。", ""])
            lines.extend("> " + line for line in source["quote"].splitlines())
            lines.append("")
            if source["images"]:
                lines.extend(["关联图片：" + "、".join(source["images"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验测试点JSON，导出独立清单和REQ/AC关联覆盖报告。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--input", type=Path, help="测试点数据；默认模块内测试点数据.json。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        outputs = [module_dir / name for name in ("测试点.md", "测试点覆盖率数据.json", "测试点覆盖率报告.md")]
        protect_outputs(module_dir, outputs, args.input)
        spec, _, points = load_context(module_dir, args.input)
        coverage = point_coverage(spec, points)
        write_text(outputs[0], render_points(spec, points))
        write_json(outputs[1], coverage)
        write_text(outputs[2], point_coverage_report(coverage))
        if coverage["status"] != "passed":
            print("已导出测试点清单和覆盖报告；关联覆盖未通过或当前没有可设计需求。")
            return 1
        print(f"已导出{len(points)}条测试点；REQ/AC关联覆盖检查通过，业务正确性需逐点评审。")
        return 0
    except (ValidationError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
