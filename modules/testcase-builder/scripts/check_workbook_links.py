#!/usr/bin/env python3
"""校验单独交付的用例工作簿或 用例数据.json 里的 REQ/AC/测试点编号是否真实存在。

`validate_cases` 只在 JSON 侧拦错；别人递来一个只有 xlsx 的工作簿时，编号是
否存在没人核对过，覆盖率就会按"引用了几条"虚高。本命令补上这个出口：直接读
用例工作簿（前 13 列 + `追溯` 派生表）或 JSON，逐条核对编号存在性与归属。

发现编号问题时以非零退出码结束——默认不允许把这种工作簿当合格输入继续用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qa_cases import (HEADERS, ValidationError, audit_case_links, cli_error, load_link_targets,
                      read_trace_sheet, read_xlsx, split_registered, write_text)


def read_case_links_from_xlsx(path: Path) -> tuple[list[dict], bool]:
    """返回 (用例关联行, 是否有追溯表)。前 13 列给出编号与需求，追溯表补 AC/测试点。"""
    sheet = read_xlsx(path, columns=len(HEADERS))
    rows = sheet["rows"]
    if [str(value).strip() for value in rows[0][:len(HEADERS)]] != HEADERS:
        raise ValidationError(f"{path.name} 前13列标题与约定不一致。")
    trace = read_trace_sheet(path)
    cases = []
    for row in rows[1:]:
        case_id = str(row[0]).strip()
        if not case_id:
            continue
        linked = trace.get(case_id, {})
        cases.append({
            "用例编号": case_id,
            # 追溯表是 AC/测试点的唯一来源；缺表即为未登记，不能当成通过。
            "关联需求": linked.get("关联需求") or split_registered(row[8]),
            "验收标准": linked.get("验收标准", []),
            "关联测试点": linked.get("关联测试点", []),
            "_registered": bool(linked),
        })
    if not cases:
        raise ValidationError(f"{path.name} 没有用例行。")
    return cases, bool(trace)


def read_case_links_from_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise ValidationError(f"{path.name} 里没有 cases 数组。")
    return [{"用例编号": str(item.get("用例编号") or "").strip(),
             "关联需求": item.get("关联需求") or [],
             "验收标准": item.get("验收标准") or [],
             "关联测试点": item.get("关联测试点") or [],
             "_registered": True} for item in raw]


def render_report(source: Path, targets: dict, cases: list[dict], issues: list[dict],
                  has_trace: bool) -> str:
    active = [req for req in targets["spec"]["requirements"] if req.get("status") == "active"]
    ac_total = sum(len(req.get("acceptance_criteria") or []) for req in active)
    unregistered = sum(1 for case in cases if not case.get("_registered"))
    lines = [f"# 编号校验报告：{source.name}", "",
             f"- 输入：`{source}`",
             f"- 编号基准：active 需求 {len(active)} 条、active 验收标准 {ac_total} 条、"
             + (f"测试点 {len(targets['point_ids'])} 个。" if targets["has_points"]
                else "无 `测试点数据.json`，测试点关联本次不参与判定。"),
             f"- 核对用例：{len(cases)} 条。",
             f"- 结论：**{'通过' if not issues else '不通过'}**"
             + (f"，发现 {len(issues)} 处编号问题。" if issues else "，未发现不存在的编号或错误归属。"), ""]
    if unregistered:
        lines += [f"> 提示：{unregistered} 条用例没有可读的 `追溯` 派生表，"
                  "其验收标准/测试点关联为**未登记**，不能据此声称已覆盖。", ""]
    if issues:
        lines += ["## 问题清单", "", "| 用例编号 | 字段 | 编号 | 问题 |", "| --- | --- | --- | --- |"]
        for item in issues:
            lines.append(f"| {item['用例编号']} | {item['字段']} | {item['编号']} | {item['问题']} |")
        lines += ["",
                  "编号不存在的关联必须先在 REQ清单/测试点数据中对齐或重编，再谈覆盖率；",
                  "按引用条数统计的覆盖率在修正前不成立。", ""]
    else:
        lines += ["所有引用的需求、验收标准与测试点编号都存在，且归属成立。", ""]
    lines += ["本报告只核对编号存在性与归属，不判断用例内容是否正确，也不执行被测系统。"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验用例工作簿/JSON 的 REQ、AC、测试点编号是否存在。")
    parser.add_argument("--module-dir", required=True, type=Path, help="模块目录，提供 REQ清单.json 与 测试点数据.json。")
    parser.add_argument("--input", required=True, type=Path, help="待校验的 xlsx 或 用例数据.json。")
    parser.add_argument("--out-dir", type=Path, help="报告输出目录，默认输入文件所在目录。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        source = args.input.resolve()
        if not source.is_file():
            raise ValidationError(f"待校验文件不存在：{source}。")
        targets = load_link_targets(module_dir)
        if source.suffix.lower() == ".json":
            cases, has_trace = read_case_links_from_json(source), True
        elif source.suffix.lower() == ".xlsx":
            cases, has_trace = read_case_links_from_xlsx(source)
        else:
            raise ValidationError("只支持 .xlsx 或 .json 输入。")
        issues = audit_case_links(cases, targets)
        out_dir = (args.out_dir or source.parent).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "source": str(source),
            "module": targets["spec"].get("module"),
            "code": targets["spec"].get("code"),
            "case_count": len(cases),
            "status": "failed" if issues else "passed",
            "has_trace_sheet": has_trace,
            "unregistered_case_count": sum(1 for case in cases if not case.get("_registered")),
            "testpoint_baseline_available": targets["has_points"],
            "issue_count": len(issues),
            "issues": issues,
        }
        write_text(out_dir / "编号校验数据.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        write_text(out_dir / "编号校验报告.md", render_report(source, targets, cases, issues, has_trace))
        if issues:
            print(f"编号校验不通过：{len(issues)} 处问题。已写入 {out_dir}。", file=sys.stderr)
            for item in issues[:10]:
                print(f"  {item['用例编号']} {item['字段']} {item['编号']}：{item['问题']}", file=sys.stderr)
            if len(issues) > 10:
                print(f"  …… 其余 {len(issues) - 10} 处见报告。", file=sys.stderr)
            return 1
        print(f"编号校验通过：{len(cases)} 条用例，编号全部存在且归属成立。已写入 {out_dir}。")
        return 0
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
