#!/usr/bin/env python3
"""用例可读性检查：把“前置条件难读”从主观感受变成可复算的指标。

四个指标都只做形式判断，不判断业务内容是否正确：

1. 前置独有信息占比——模块内重复出现的片段不算独有信息，公共段应写在
   `module_preconditions` 而不该逐条抄。
2. 前置最长单句——超过阈值说明该拆行。
3. 预期结果落点率——预期必须说明在哪个界面/列表/记录/通道上观察。
4. 未完成句与内部标识符残留——半截话与代码黑话不应作为描述语言。

默认只报警（退出码 0），加 `--strict` 才以非零退出，便于既有资料先看清单再整改。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from qa_cases import case_text_fragments, cli_error, load_json, module_shared_lines, write_text

# 阈值集中在此，tests/test_readability_contract.py 会核对文档与代码一致。
THRESHOLDS = {
    "unique_ratio_min": 0.70,
    "max_sentence_chars": 60,
    "expected_anchor_rate_min": 0.80,
}
# 预期结果必须落到这些观察对象之一，否则无法判断通过与否。
OBSERVATION_ANCHORS = (
    "界面", "面板", "弹窗", "列表", "提示", "文案", "记录", "日志", "通道", "消息",
    "事件", "状态", "字段", "数量", "计数", "反馈", "报错", "错误", "按钮", "页面",
)
# 半截话：出现这些词说明缺失能力被留在了字段里而没有说清补齐办法。
# 早期只查句尾，实测“当前框架尚未封装”这类把关键词放在句中的写法会漏判。
UNFINISHED_MARKERS = ("尚未封装", "尚未提供", "尚未核对", "待补", "待定", "暂缺", "未能确定", "需补充说明")
# 项目内部标识符：snake_case、全大写常量或 camelCase 形式的代码名。
# camelCase 必须单独匹配：早期只查下划线形式，“openInvites”这类黑话会漏判。
INTERNAL_IDENTIFIER = re.compile(
    r"\b(?:[a-z][a-z0-9]*_[a-z0-9_]+"          # snake_case
    r"|[A-Z][A-Z0-9]*_[A-Z0-9_]+"              # CONSTANT_CASE
    r"|[a-z]+[A-Z][A-Za-z0-9]*)\b"             # camelCase
)
# 允许出现在业务描述里的业务缩写，不视为黑话。
IDENTIFIER_ALLOWLIST = {"REQ", "AC", "TC", "P0", "P1", "P2", "P3"}


def split_fragments(text: str) -> list[str]:
    """按分号、句号与换行切分前置条件片段（与校验器共用同一实现）。"""
    return case_text_fragments(text)


def split_sentences(text: str) -> list[str]:
    """按分号、换行与句末标点切句，用于测量最长单句。"""
    parts = re.split(r"[；;\n。！？!?]", str(text))
    return [part.strip() for part in parts if part.strip()]


def check_cases(data: dict, cases: list[dict]) -> dict:
    """返回可读性报告；不修改用例内容。"""
    shared = module_shared_lines(data)
    # 缺字段的用例必须得到告警，而不是让整份检查崩在 KeyError 上。
    fragments_per_case = {case.get("用例编号"): split_fragments(case.get("前置条件", "")) for case in cases}
    counter: Counter[str] = Counter()
    for fragments in fragments_per_case.values():
        for fragment in set(fragments):
            counter[fragment] += 1
    # 出现在两条以上用例里的片段即为该抄进公共段的内容。
    duplicated = {fragment for fragment, count in counter.items() if count > 1}

    case_findings = []
    for case in cases:
        case_id = case.get("用例编号")
        precondition = str(case.get("前置条件", ""))
        fragments = fragments_per_case[case_id] or [precondition]
        unique_chars = sum(len(f) for f in fragments if f not in duplicated)
        total_chars = sum(len(f) for f in fragments) or 1
        unique_ratio = unique_chars / total_chars
        repeated = sorted({f for f in fragments if f in duplicated})
        longest = max((len(s) for s in split_sentences(precondition)), default=0)

        expected = case.get("预期结果")
        expected_items = expected if isinstance(expected, list) else [expected]
        anchored = [item for item in expected_items
                    if any(anchor in str(item) for anchor in OBSERVATION_ANCHORS)]
        anchor_rate = len(anchored) / len(expected_items) if expected_items else 0.0

        unfinished = [s for s in split_sentences(precondition)
                      if any(marker in s for marker in UNFINISHED_MARKERS)]
        identifiers = sorted({token for token in INTERNAL_IDENTIFIER.findall(precondition)
                              if token.split("_")[0] not in IDENTIFIER_ALLOWLIST})

        warnings = []
        if unique_ratio < THRESHOLDS["unique_ratio_min"]:
            detail = f"，{len(repeated)} 个片段疑似公共段" if repeated else ""
            if repeated:
                detail += f"，例如「{repeated[0][:30]}…」"
            warnings.append(f"前置独有信息占比 {unique_ratio:.0%} 低于 "
                            f"{THRESHOLDS['unique_ratio_min']:.0%}{detail}")
        if longest > THRESHOLDS["max_sentence_chars"]:
            warnings.append(f"前置最长单句 {longest} 字，超过 {THRESHOLDS['max_sentence_chars']} 字，建议拆行")
        if anchor_rate < THRESHOLDS["expected_anchor_rate_min"]:
            warnings.append(f"预期结果落点率 {anchor_rate:.0%} 低于 "
                            f"{THRESHOLDS['expected_anchor_rate_min']:.0%}：有预期未说明在哪观察")
        if unfinished:
            warnings.append(f"前置含未完成句：{unfinished[0][:30]}")
        if identifiers:
            warnings.append(f"前置含未翻译的内部标识符：{'、'.join(identifiers[:3])}")

        if warnings:
            case_findings.append({"用例编号": case_id, "用例名称": case.get("用例名称"),
                                  "指标": {"unique_ratio": round(unique_ratio, 3),
                                           "longest_sentence": longest,
                                           "expected_anchor_rate": round(anchor_rate, 3)},
                                  "警告": warnings})

    case_count = len(cases)
    findings = case_findings
    # 模块级：一条公共段都没登记却出现跨用例重复，说明该抽公共段而不是逐条抄。
    if duplicated and not (shared["module_preconditions"] or shared["evidence_setup"]):
        findings = [{
            "用例编号": "（模块级）",
            "用例名称": "未登记公共段",
            "指标": {"unique_ratio": None, "longest_sentence": None, "expected_anchor_rate": None},
            "警告": [f"检测到 {len(duplicated)} 个跨用例重复片段，但 module_preconditions 与 "
                     f"evidence_setup 均未登记；公共环境与取证要求应只写一次"],
        }] + findings
    return {
        "schema_version": "1.0",
        "module": data.get("module"),
        "case_count": case_count,
        "status": "passed" if not findings else "warned",
        "thresholds": dict(THRESHOLDS),
        "shared_registered": bool(shared["module_preconditions"] or shared["evidence_setup"]),
        "duplicated_fragments": sorted(duplicated),
        "findings": findings,
    }


def render_report(report: dict) -> str:
    lines = ["# 用例可读性检查", ""]
    lines.append(f"- 模块：{report['module']}　用例：{report['case_count']} 条")
    lines.append(f"- 结论：{'通过' if report['status'] == 'passed' else '存在告警'}"
                 f"（告警 {len(report['findings'])} 条）")
    lines.append(f"- 阈值：前置独有信息占比 ≥ {report['thresholds']['unique_ratio_min']:.0%}；"
                 f"前置最长单句 ≤ {report['thresholds']['max_sentence_chars']} 字；"
                 f"预期落点率 ≥ {report['thresholds']['expected_anchor_rate_min']:.0%}")
    lines.append(f"- 公共段登记：{'已登记' if report.get('shared_registered') else '未登记'}")
    lines.append("")
    if report["duplicated_fragments"]:
        lines.append("## 疑似公共段（应移入 module_preconditions 或 evidence_setup）")
        lines.append("")
        for fragment in report["duplicated_fragments"][:20]:
            lines.append(f"- {fragment}")
        lines.append("")
    if report["findings"]:
        lines.append("## 逐条告警")
        lines.append("")
        lines.append("| 用例 | 独有占比 | 最长句 | 预期落点率 | 告警 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for finding in report["findings"]:
            metrics = finding["指标"]
            ratio = "—" if metrics["unique_ratio"] is None else f"{metrics['unique_ratio']:.0%}"
            longest = "—" if metrics["longest_sentence"] is None else f"{metrics['longest_sentence']} 字"
            anchor = "—" if metrics["expected_anchor_rate"] is None else f"{metrics['expected_anchor_rate']:.0%}"
            lines.append(f"| {finding['用例编号']} {finding['用例名称']} | {ratio} | "
                         f"{longest} | {anchor} | {'；'.join(finding['警告'])} |")
        lines.append("")
    lines.append("本检查只判断书写形式，不代表用例设计正确或已执行。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查用例前置条件与预期的可读性指标；默认只告警。")
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--cases", type=Path, help="默认读取模块的 用例数据.json")
    parser.add_argument("--strict", action="store_true", help="有告警时以非零退出，用于新资料门禁。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        source = (args.cases or module_dir / "用例数据.json").resolve()
        data = load_json(source)
        cases = data.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("用例数据没有可检查的 cases 数组。")
        report = check_cases(data, cases)
        write_text(module_dir / "可读性数据.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_text(module_dir / "可读性报告.md", render_report(report))
        print(f"可读性检查：{report['status']}，{len(report['findings'])} 条告警。"
              f"已写入可读性数据.json和可读性报告.md。")
        if args.strict and report["findings"]:
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
