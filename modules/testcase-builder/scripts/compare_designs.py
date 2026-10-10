#!/usr/bin/env python3
"""Compare two or more candidate case designs for the same module.

评审回答"这一份设计能不能用"，本工具回答"同一批需求下，哪一份设计更完整"。
两者是独立结论：本工具不改动任何输入，也不产出字母等级或"质量分"。
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from qa_cases import (HEADERS, METRICS_HEADERS, METHODS, SCOPES, ValidationError,
                      audit_case_links, cli_error, load_json, load_link_targets, read_trace_sheet,
                      read_xlsx, write_text)

# 维度与默认权重：与"六维完整度"口径一致，权重可被 --weights 覆盖。
DIMENSIONS = (
    ("需求关联广度", 0.20),
    ("异常与拒绝路径覆盖", 0.20),
    ("边界与状态覆盖", 0.20),
    ("测试设计方法覆盖", 0.15),
    ("用例质量与可执行性", 0.15),
    ("前置控制与证据充分性", 0.10),
)

NEGATIVE_ASSERTION = re.compile(
    r"拒绝|被拒|不开始|不新建|不创建|不触发|不发放|不发|不扣|不退|不推进|不改变|不变化|不变|仍为|仍存在|"
    r"保持|无变化|未创建|不再|不可|不为|无映射|没有|不删|不进入|不执行|不罚|不响应|不出现|不增长|"
    r"不给出|不传送|未被|不合法|越界|失败|提示|报错")
OBSERVATION_TARGET = re.compile(
    r"字段|面板|列表|日志|记录|提示|消息|文案|slot|zone|背包|金钱|经验|等级|坐标|数量|次数|状态|区域|"
    r"卡牌|开关|棋盘|排行榜|头盔|骰子|外观|库存|任务进度|对话")
ACTION_VERB = re.compile(
    r"说|点击|使用|丢|掷|走|走出|输入|击杀|召唤|领取|领奖|传送|报名|落子|回答|申请|确认|打开|采集|"
    r"等待|访问|发起|构造|交互|翻|加入|登记|对话|进入|离开|保持|闲置|同意|回复|移动|交付|提交")
PERFORMANCE_HINT = re.compile(r"性能|容量|并发|超时|日志|权限|兼容|分辨率|帧|秒级|毫秒级|分钟|小时")
BOUNDARY_HINT = re.compile(
    r"上限|下限|边界|等于|超过|不低于|不超过|第\s*\d|满|临界|阈值|最大值|最小值|恰好|"
    r"\d+\s*(?:ms|毫秒|秒|分钟|小时|元|money|级|人|只|张|次|格)")
STATE_HINT = re.compile(r"切换|转交|离队|踢|移除|清理|失效|更新|生效|转成|写库|复位|重置|状态")
COMBO_HINT = re.compile(r"/|、|或|分别|逐|两种|三种|同时|组合|各自|每(?:一|个)")
READ_PREFIX = re.compile(r"^(读取|检查|核对|枚举|查看|观察|记录|等待|保持|确认|查看|枚举)")


def split_numbered(value) -> list[str]:
    """把工作簿里的 "1. …\\n2. …" 文本还原成步骤数组；数组原样返回。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "")
    parts = [part.strip() for part in re.split(r"(?m)^\s*\d+[\.、)]\s*", text) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def split_links(value) -> list[str]:
    """关联需求/验收标准在旧工作簿里是换行或分隔符文本。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part for part in re.split(r"[\n,，;；、\s]+", str(value or "").strip()) if part]


def verdict_of(text: str) -> str:
    match = re.search(r"结论[:：]\s*(通过|需确认|不通过)", str(text or ""))
    return match.group(1) if match else "未评审"


def heuristic_scope(case: dict) -> str:
    """缺 场景类型 时的启发式归类（单值，按优先级取第一个命中）。

    端到端需要跨模块协作知识，启发式无法判定，因此不猜端到端——缺字段的设计
    在这一维会被如实记为 0，而不是靠关键字蒙一条。
    """
    name = case["用例名称"]
    blob = " ".join([name, case["测试数据"], " ".join(case["预期结果"])])
    if PERFORMANCE_HINT.search(blob):
        return "非功能"
    if re.search(r"拒绝|被拒|不能|不允许|失败|无效|不存在|非法|不可|超时|已满|不足|未|不", name):
        return "异常"
    if BOUNDARY_HINT.search(blob):
        return "边界"
    if re.search(r"或|另一|免费|取消|拒绝邀请|备选|回访|可选", name):
        return "备选"
    return "正常"


def heuristic_methods(case: dict) -> list[str]:
    """缺 设计方法 时的启发式归属；一条可命中多项。"""
    methods = ["场景法"]
    name, data = case["用例名称"], case["测试数据"]
    if re.search(r"有效|无效|不存在|非法|不合法|普通|规范|可|变体|找不到|不允许|非|门|类", name):
        methods.append("等价类")
    if BOUNDARY_HINT.search(name + data):
        methods.append("边界值")
    if STATE_HINT.search(name):
        methods.append("状态迁移")
    if COMBO_HINT.search(name) or re.search(r"同时|且|组合|叠加|其余|多个条件", case["前置条件"]):
        methods.append("判定表")
    if re.search(r"截取|异常|断线|覆盖|阻隔|嵌套|自身|静默|间隔|并发|溢出|长度|转成|失败|不足", name + data):
        methods.append("错误推测")
    return list(dict.fromkeys(methods))


def normalize_xlsx_case(row: list[str], index: int, trace: dict | None = None) -> dict:
    """把 13/14 列工作簿行规范成内部用例结构。

    AC/场景类型/设计方法/测试点按契约不在前 13 列；有 `追溯` 派生表时从该表读取，
    没有时保持为空并按"未登记"处理，由启发式估算并在报告里标注来源。
    """
    cells = list(row) + [""] * (len(HEADERS) + 1 - len(row))
    case_id = str(cells[0]).strip()
    if not case_id:
        raise ValidationError(f"候选工作簿第{index}行缺少用例编号。")
    steps, expected = split_numbered(cells[4]), split_numbered(cells[6])
    if not steps or not expected:
        raise ValidationError(f"候选工作簿 {case_id} 缺少操作步骤或预期结果。")
    linked = (trace or {}).get(case_id) or {}
    return {
        "用例编号": case_id,
        "优先级": str(cells[1]).strip(),
        "用例名称": str(cells[2]).strip(),
        "前置条件": str(cells[3]),
        "操作步骤": steps,
        "测试数据": str(cells[5]),
        "预期结果": expected,
        "关联需求": linked.get("关联需求") or split_links(cells[8]),
        "验收标准": linked.get("验收标准", []),
        "场景类型": linked.get("场景类型", ""),
        "设计方法": linked.get("设计方法", []),
        "关联测试点": linked.get("关联测试点", []),
        "是否可自动化": str(cells[9]).strip(),
        "自动化建议": str(cells[10]).strip(),
        "自动化建议明细": str(cells[11]),
        "评审结论": verdict_of(cells[13]),
    }


def load_shared_from_xlsx(path: Path) -> tuple[list[str], list[str]]:
    """工作簿里的公共段说明表；缺表时返回空，不当作缺陷。"""
    shared = []
    for sheet_name in ("公共前置条件", "取证要求"):
        try:
            sheet = read_xlsx(path, columns=2, sheet_name=sheet_name)
        except ValidationError:
            continue
        shared.extend(str(row[1]).strip() for row in sheet["rows"][1:] if str(row[1]).strip())
    return shared


def load_design(path: Path, targets: dict | None = None) -> dict:
    """读入候选设计：支持模块 用例数据.json 与 13/14 列工作簿。

    ``targets`` 提供编号基准；只要有基准就逐条核对 REQ/AC/测试点编号是否存在，
    核对结果进入设计自身的 ``link_issues``，不因"读得进来"就默认编号有效。
    """
    if not path.is_file():
        raise ValidationError(f"候选设计不存在：{path}。")
    if path.suffix.lower() == ".xlsx":
        sheet = read_xlsx(path, columns=14)
        rows = sheet["rows"]
        if [str(value).strip() for value in rows[0][:13]] != HEADERS:
            raise ValidationError(f"候选工作簿 {path.name} 前13列标题与约定不一致。")
        trace = read_trace_sheet(path)
        cases = [normalize_xlsx_case(row, index, trace)
                 for index, row in enumerate(rows[1:], 2) if str(row[0]).strip()]
        shared = load_shared_from_xlsx(path)
        for case in cases:
            case["_heuristic"] = not trace
        declared = trace and all(case.get("场景类型") in SCOPES and case.get("设计方法") for case in cases)
        field_source = ("工作簿（含 `追溯` 派生表，AC/场景类型/设计方法/测试点取自该表）" if declared
                        else "工作簿（无追溯表或追溯字段不全，按启发式估算）")
        design = {"label": path.stem, "source": str(path), "cases": cases,
                  "shared_lines": shared, "field_source": field_source,
                  "links_registered": bool(trace)}
        if targets is not None:
            design["link_issues"] = audit_case_links(cases, targets)
        return design
    data = load_json(path)
    raw = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise ValidationError(f"{path.name} 里没有 cases 数组。")
    cases = []
    heuristic_fields = 0
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValidationError(f"{path.name} 第{index}条用例必须为对象。")
        missing = [key for key in HEADERS if key not in item]
        if missing:
            raise ValidationError(f"{path.name} 第{index}条用例缺少字段：{'、'.join(missing)}。")
        case = dict(item)
        case["操作步骤"] = split_numbered(case["操作步骤"])
        case["预期结果"] = split_numbered(case["预期结果"])
        case["关联需求"] = split_links(case["关联需求"])
        case["验收标准"] = split_links(case.get("验收标准"))
        case["关联测试点"] = split_links(case.get("关联测试点"))
        if case.get("场景类型") not in SCOPES:
            case["场景类型"] = ""
            heuristic_fields += 1
        if not case.get("设计方法"):
            case["设计方法"] = []
            heuristic_fields += 1
        case["评审结论"] = verdict_of(item.get("AI评审", ""))
        cases.append(case)
    shared = []
    for key in ("module_preconditions", "evidence_setup"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            shared.extend(str(item).strip() for item in value if str(item).strip())
    field_source = "用例数据 JSON" if not heuristic_fields else "用例数据 JSON（部分字段缺失，按启发式估算）"
    design = {"label": path.stem, "source": str(path), "cases": cases,
              "shared_lines": shared, "field_source": field_source, "links_registered": True}
    if targets is not None:
        design["link_issues"] = audit_case_links(cases, targets)
    return design


def attach_review(design: dict, review_path: Path) -> None:
    """基准设计若已有已发布评审，附上逐条结论供报告单列；缺失即保持未评审。"""
    if not review_path.is_file():
        return
    try:
        data = load_json(review_path)
    except ValidationError:
        return
    rows = data.get("reviews") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return
    verdicts = {}
    for row in rows:
        if isinstance(row, dict) and row.get("用例编号"):
            verdicts[str(row["用例编号"])] = str(row.get("结论", "")).strip()
    for case in design["cases"]:
        if case["用例编号"] in verdicts:
            case["评审结论"] = verdicts[case["用例编号"]]
    design["field_source"] += "；已附模块已发布评审结论"


def measure(design: dict, active_reqs: list[str], active_acs: list[str], *, force_heuristic: bool) -> dict:
    """计算一份设计的分项指标。

    ``force_heuristic`` 为真时，所有设计统一改用启发式归类——只要有任意一份候选
    缺 ``场景类型``/``设计方法``，就必须让全组用同一套口径，否则"已声明字段"的
    设计和"估算"的设计无法公平比较。
    """
    cases = design["cases"]
    total = len(cases)
    active_ac_set = set(active_acs)
    by_req = Counter(req for case in cases for req in case["关联需求"] if req in active_reqs)
    # 只有存在于当前 REQ清单的 AC 才算覆盖；否则覆盖率会按引用条数虚高。
    by_ac = Counter(ac for case in cases for ac in case.get("验收标准") or [] if ac in active_ac_set)
    link_issues = design.get("link_issues") or []
    if not design.get("links_registered"):
        link_status = "unregistered"
    elif link_issues:
        link_status = "failed"
    else:
        link_status = "passed"
    counts = [by_req.get(req, 0) for req in active_reqs]
    mean = sum(counts) / len(counts) if counts else 0
    spread = math.sqrt(sum((value - mean) ** 2 for value in counts) / len(counts)) if counts else 0
    cv = (spread / mean) if mean else 0.0

    scopes, methods = Counter(), Counter()
    for case in cases:
        declared_scope, declared_methods = case.get("场景类型"), case.get("设计方法") or []
        scope = heuristic_scope(case) if (force_heuristic or declared_scope not in SCOPES) else declared_scope
        case["_scope"] = scope
        scopes[scope] += 1
        picked = heuristic_methods(case) if (force_heuristic or not declared_methods) else declared_methods
        case["_methods"] = picked
        for method in picked:
            methods[method] += 1

    steps = [len(case["操作步骤"]) for case in cases]
    aligned = sum(1 for case in cases if len(case["操作步骤"]) == len(case["预期结果"]))
    quantified = sum(1 for case in cases if re.search(r"\d", case["测试数据"]))
    observable = sum(1 for case in cases if OBSERVATION_TARGET.search(" ".join(case["预期结果"])))
    negative = sum(1 for case in cases if NEGATIVE_ASSERTION.search(" ".join(case["预期结果"])))
    matrices = sum(1 for case in cases if _is_matrix(case["测试数据"]))
    combos = sum(1 for case in cases if COMBO_HINT.search(case["测试数据"] + case["用例名称"]))
    pure = 0
    for case in cases:
        if not any(ACTION_VERB.search(step) and not READ_PREFIX.match(step) for step in case["操作步骤"]):
            pure += 1
    constructed = sum(1 for case in cases if re.search(
        r"可构造|可分别|可反复|可把|可用快照|快照|夹具|管理员|观察号|观察端|状态设置|可观测|可枚举", case["前置条件"]))
    evidence = sum(1 for case in cases if re.search(
        r"保存|证据|快照|截图|留存|存档|原始返回|逐帧|命名|记录在|写入记录|记录表|记录到",
        " ".join(case["预期结果"]) + " ".join(case["操作步骤"])))
    gaps = sum(1 for case in cases if re.search(r"缺失|补齐|理由[:：]", case.get("自动化建议明细", "")))
    reviewed = [case for case in cases if case.get("评审结论") in ("通过", "需确认", "不通过")]

    def ratio(count: int) -> float:
        return count / total if total else 0.0

    return {
        "case_count": total,
        "classification": "启发式估算" if force_heuristic else "用例数据声明字段",
        "requirement_coverage": {"total": len(active_reqs), "covered": sum(1 for value in counts if value), "rate": (sum(1 for value in counts if value) / len(active_reqs)) if active_reqs else None},
        "requirement_case_counts": dict(by_req),
        "requirement_cv": cv,
        "ac_coverage": {"total": len(active_ac_set), "covered": sum(1 for value in by_ac.values() if value),
                        "registered": sum(1 for case in cases if case.get("验收标准"))},
        "link_issues": link_issues,
        "link_issue_count": len(link_issues),
        "link_status": link_status,
        "dangling_ac_count": sum(1 for item in link_issues if item["字段"] == "验收标准"),
        "dangling_tp_count": sum(1 for item in link_issues if item["字段"] == "关联测试点"),
        "scope_by_item": {name: scopes.get(name, 0) for name in SCOPES},
        "method_by_item": {name: methods.get(name, 0) for name in METHODS},
        "scope_covered": sum(1 for name in SCOPES if scopes.get(name, 0)),
        "method_covered": sum(1 for name in METHODS if methods.get(name, 0)),
        "abnormal_ratio": ratio(scopes.get("异常", 0)),
        "alternative_ratio": ratio(scopes.get("备选", 0)),
        "boundary_ratio": ratio(scopes.get("边界", 0)),
        "end_to_end_ratio": ratio(scopes.get("端到端", 0)),
        "state_method_ratio": ratio(methods.get("状态迁移", 0)),
        "negative_ratio": ratio(negative),
        "matrix_ratio": ratio(matrices),
        "combo_ratio": ratio(combos),
        "step_mean": (sum(steps) / total) if total else 0.0,
        "align_ratio": ratio(aligned),
        "quantified_ratio": ratio(quantified),
        "observable_ratio": ratio(observable),
        "observational_only_ratio": ratio(pure),
        "constructed_ratio": ratio(constructed),
        "evidence_ratio": ratio(evidence),
        "gap_ratio": ratio(gaps),
        "shared_line_count": len(design["shared_lines"]),
        "review": {"reviewed": len(reviewed), "counts": dict(Counter(case["评审结论"] for case in reviewed))} if reviewed else None,
    }


def _is_matrix(data: str) -> bool:
    """阈值/取值矩阵：测试数据给出多点取值，而不是单一场景值。"""
    if not data:
        return False
    numbers = set(re.findall(r"\d+(?:\.\d+)?", data))
    return ("/" in data) or ("分别" in data) or ("逐" in data) or ("vs" in data) or ("两种" in data) or len(numbers) >= 3


def scale(values: list[float]) -> list[float]:
    """相对归一：同一分项以本组最优者为 100。"""
    best = max(values) if values else 0
    if best <= 0:
        return [0.0 for _ in values]
    return [value / best * 100 for value in values]


def score(metrics: list[dict]) -> list[list[float]]:
    """六维得分；每个分项在本组内相对归一，再按权重加权。"""
    def column(key: str) -> list[float]:
        return [item[key] for item in metrics]

    req_rate = [item["requirement_coverage"]["rate"] or 0 for item in metrics]
    evenness = [1 - min(item["requirement_cv"], 1) for item in metrics]
    d1 = [0.70 * a + 0.30 * b for a, b in zip(scale(req_rate), scale(evenness))]
    d2 = [0.45 * a + 0.35 * b + 0.20 * c for a, b, c in
          zip(scale(column("abnormal_ratio")), scale(column("negative_ratio")), scale(column("alternative_ratio")))]
    d3 = [0.40 * a + 0.30 * b + 0.30 * c for a, b, c in
          zip(scale(column("boundary_ratio")), scale(column("state_method_ratio")), scale(column("matrix_ratio")))]
    d4 = [0.40 * a + 0.30 * b + 0.30 * c for a, b, c in
          zip(scale(column("method_covered")), scale(column("scope_covered")), scale(column("combo_ratio")))]
    step_fit = [min(item["step_mean"] / 2, 1) for item in metrics]
    d5 = [0.30 * a + 0.20 * b + 0.30 * c + 0.20 * d for a, b, c, d in
          zip(scale(step_fit), scale(column("align_ratio")), scale(column("quantified_ratio")),
              scale(column("observable_ratio")))]
    d6 = [0.35 * a + 0.30 * b + 0.15 * c + 0.20 * d for a, b, c, d in
          zip(scale(column("constructed_ratio")), scale(column("evidence_ratio")),
              scale(column("shared_line_count")), scale(column("gap_ratio")))]
    return [[round(value, 1) for value in row] for row in zip(d1, d2, d3, d4, d5, d6)]


PERCENT_ITEMS = (
    ("异常场景占比", "abnormal_ratio"), ("备选场景占比", "alternative_ratio"),
    ("边界场景占比", "boundary_ratio"), ("端到端场景占比", "end_to_end_ratio"),
    ("状态迁移方法占比", "state_method_ratio"), ("含负向断言占比", "negative_ratio"),
    ("阈值矩阵型占比", "matrix_ratio"), ("多条件组合占比", "combo_ratio"),
    ("步骤与预期编号对齐", "align_ratio"), ("测试数据量化率", "quantified_ratio"),
    ("预期结果可观察率", "observable_ratio"), ("纯观察无操作占比", "observational_only_ratio"),
    ("前置写明状态构造", "constructed_ratio"), ("单条写明取证落点", "evidence_ratio"),
    ("能力缺口登记率", "gap_ratio"),
)


def evaluate(designs: list[dict], active: list[str], active_acs: list[str], dimensions, *,
             force_heuristic: bool) -> dict:
    """按同一口径评估一组设计，返回逐维得分与原始指标。"""
    metrics = [measure(design, active, active_acs, force_heuristic=force_heuristic) for design in designs]
    scores = score(metrics)
    totals = [round(sum(weight * value for (_, weight), value in zip(dimensions, row)), 1) for row in scores]
    return {
        "classification": metrics[0]["classification"],
        "designs": [{"label": design["label"], "source": design["source"], "field_source": design["field_source"],
                     "case_count": item["case_count"], "scores": row, "total": total}
                    for design, item, row, total in zip(designs, metrics, scores, totals)],
        "metrics": [dict(item, label=design["label"]) for item, design in zip(metrics, designs)],
    }


def render_link_gate(metrics: list[dict], report: dict) -> list[str]:
    """关联编号门禁：先证明编号存在，再谈覆盖率。放在所有得分之前。"""
    lines = ["## 一、关联编号门禁（先看这里，再看分数）", "",
             "AC 与测试点覆盖率只在编号真实存在时才成立。下表先核对编号，再给出有效覆盖；",
             "按引用条数统计的覆盖率在编号修正前不成立。", "",
             "| 设计 | 编号门禁 | 不存在的编号 | 验收标准 | 测试点 | 编号有效的 AC 覆盖 | 已登记 AC 的用例 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    verdict = {"passed": "通过", "failed": "**不通过**", "unregistered": "未登记"}
    for item in metrics:
        ac = item["ac_coverage"]
        lines.append("| " + " | ".join([
            item["label"], verdict.get(item["link_status"], item["link_status"]),
            str(item["link_issue_count"]), str(item["dangling_ac_count"]), str(item["dangling_tp_count"]),
            f"{ac['covered']}/{ac['total']}", f"{ac['registered']}/{item['case_count']}"]) + " |")
    lines.append("")
    for item in metrics:
        if item["link_status"] == "failed":
            lines.extend([f"**{item['label']} 的问题编号：**", ""])
            for issue in item["link_issues"]:
                lines.append(f"- {issue['用例编号']} 的{issue['字段']} `{issue['编号']}`：{issue['问题']}。")
            lines.append("")
        elif item["link_status"] == "unregistered":
            lines.extend([f"> {item['label']} 没有可读的 `追溯` 派生表，验收标准与测试点关联为**未登记**，",
                          "> 其场景/方法两维按启发式估算；未登记不等于通过。", ""])
    if report.get("link_gate_overridden"):
        lines.extend(["> 本次以 `--allow-link-issues` 继续运行：上面判为不通过的候选，其覆盖率与总分只反映已登记字段，",
                      "> **不能**当作关联覆盖已成立。", ""])
    lines.extend(["",
                  "本门禁只核对**编号是否存在、归属是否成立**，不判断「这条用例是否真的在验证该 AC」；"
                  "编号齐全但语义错挂的设计仍会显示为 100% 覆盖，语义层面必须逐条独立核对后才能引用。",
                  "先用 `check_workbook_links.py --module-dir <模块目录> --input <候选>` 对齐编号，再引用本报告的比例。"])
    lines.append("")
    return lines


def _score_table(variant: dict, dimensions) -> list[str]:
    labels = [design["label"] for design in variant["designs"]]
    lines = ["| 设计 | 用例数 | " + " | ".join(name for name, _ in dimensions) + " | 加权总分 |",
             "| --- | ---: | " + " | ".join("---:" for _ in dimensions) + " | ---: |"]
    for design in variant["designs"]:
        cells = ([design["label"], str(design["case_count"])]
                 + [f"{design['scores'][i]:g}" for i in range(len(dimensions))]
                 + [f"{design['total']:g}"])
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render(report: dict) -> str:
    dimensions = list(zip(report["dimensions"], report["weights"]))
    primary = report["primary"]
    labels = [design["label"] for design in primary["designs"]]
    lines = [f"# {report['module']}候选用例设计对比", "",
             f"基准：{report['baseline_label']}。对比对象：" + "、".join(report["alternative_labels"]) + "。", "",
             "本报告比较同一批需求下的候选设计完整度，不评审单份设计能否发布，也不执行被测系统。", ""]
    lines.extend(render_link_gate(primary["metrics"], report))
    if report["sensitivity"] is not None:
        lines.extend(["> **口径提醒**：候选之间的 `场景类型`/`设计方法` 元数据深度不同"
                      "（有的带声明字段，有的只有 13＋1 列工作簿）。下面同时给出两种读法：",
                      "> 「口径 A」按各候选自己的声明字段，缺字段的用启发式补齐（信息利用最充分，",
                      "> 但场景/方法两维不是严格同源）；「口径 B」全部候选统一用启发式归类（同源可比，",
                      "> 但更粗）。**两处结论不一致时以人工复核为准，不要只引用其中一个总分。**", ""])
    lines.extend(["## 二、加权总分（口径 A：优先声明字段）", ""] + _score_table(primary, dimensions))
    lines.extend(["", "各分项以本组较优者为 100 相对归一后加权，权重 "
                  + "/".join(f"{weight:.0%}" for _, weight in dimensions)
                  + "。总分反映候选之间的相对差距，不是绝对质量分，也不能抵消缺失或需确认事项。", ""])
    if report["sensitivity"] is not None:
        lines.extend([f"## 附：加权总分（口径 B：{report['sensitivity']['classification']}，同源可比）", ""]
                     + _score_table(report["sensitivity"], dimensions))
        lines.extend(["", "口径 B 用同一套启发式规则重算，因此只有在本口径下「场景类型」「设计方法」"
                          "两维才严格可比；启发式由关键字判定，必须人工核对。", ""])
    lines.extend(["## 三、维度结论（口径 A）", "", "| 维度 | " + " | ".join(labels) + " | 最优 |", "| --- | "
                  + " | ".join("---:" for _ in labels) + " | --- |"])
    for position, (name, _) in enumerate(dimensions):
        values = [design["scores"][position] for design in primary["designs"]]
        best = max(values)
        winner = "、".join(design["label"] for design, value in zip(primary["designs"], values) if abs(value - best) < 0.05)
        lines.append("| " + name + " | " + " | ".join(f"{value:g}" for value in values) + f" | {winner} |")
    lines.append("")

    lines.extend(["## 四、分项原始指标（口径 A）", "", "| 指标 | " + " | ".join(labels) + " |", "| --- | "
                  + " | ".join("---:" for _ in labels) + " |"])
    rows = [("用例条数", lambda m: m["case_count"]),
            ("覆盖 REQ", lambda m: f"{m['requirement_coverage']['covered']}/{m['requirement_coverage']['total']}"),
            ("编号有效的 AC 覆盖", lambda m: f"{m['ac_coverage']['covered']}/{m['ac_coverage']['total']}"),
            ("已登记 AC/测试点的用例", lambda m: f"{m['ac_coverage']['registered']}/{m['case_count']}"),
            ("编号门禁", lambda m: {"passed": "通过", "failed": "不通过",
                                    "unregistered": "未登记"}[m["link_status"]]),
            ("不存在的编号数", lambda m: m["link_issue_count"]),
            ("需求分布 CV", lambda m: round(m["requirement_cv"], 3)),
            ("场景类型覆盖", lambda m: f"{m['scope_covered']}/{len(SCOPES)}"),
            ("设计方法覆盖", lambda m: f"{m['method_covered']}/{len(METHODS)}"),
            ("平均操作步骤数", lambda m: round(m["step_mean"], 2)),
            ("模块级公共段条数", lambda m: m["shared_line_count"])]
    for label, getter in rows:
        lines.append("| " + label + " | " + " | ".join(str(getter(item)) for item in primary["metrics"]) + " |")
    for label, key in PERCENT_ITEMS:
        lines.append("| " + label + " | " + " | ".join(f"{item[key]:.1%}" for item in primary["metrics"]) + " |")
    lines.append("")

    lines.extend(["## 五、逐项用例分布", ""])
    for item in primary["metrics"]:
        lines.append(f"### {item['label']}")
        scopes = "、".join(f"{name}{count}" for name, count in item["scope_by_item"].items())
        methods = "、".join(f"{name}{count}" for name, count in item["method_by_item"].items())
        lines.extend(["", f"- 场景类型（{item['classification']}）：{scopes}",
                      f"- 设计方法（{item['classification']}）：{methods}"])
        if item["review"]:
            counts = item["review"]["counts"]
            lines.append("- 评审结论：" + "、".join(f"{key}{value}" for key, value in sorted(counts.items())))
        else:
            lines.append("- 评审结论：无已发布评审（未评审不作为通过）。")
    lines.append("")

    lines.extend(["## 六、数据来源与口径", ""])
    for design in primary["designs"]:
        lines.append(f"- {design['label']}：{design['field_source']}；来源 `{design['source']}`。")
    lines.extend(["",
                  "场景类型与设计方法的归类只影响「异常与拒绝路径覆盖」「边界与状态覆盖」"
                  "「测试设计方法覆盖」三维；其余三维按文本与结构计算，不受口径影响。",
                  "启发式不猜端到端——需要跨模块协作知识，关键字无法判定。",
                  "评审通过率不进入加权总分：通常只有基准设计带已发布评审，计入会使口径不可比；",
                  "评审结论单独列在「逐项用例分布」里。",
                  "关联编号门禁独立于总分：编号不存在的候选，其覆盖率与总分都不成立，",
                  "即使加了 --allow-link-issues 也只是允许继续比较，不代表编号问题消失。",
                  "覆盖率、完整度与总分都不证明用例正确、可执行或软件通过测试；",
                  "候选间的关联覆盖差异不能替代单份设计的正式评审。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="比较同一模块的多份候选用例设计；不修改任何输入。")
    parser.add_argument("--module-dir", required=True, type=Path, help="模块目录，用于取基准设计与 active REQ 清单。")
    parser.add_argument("--baseline", type=Path, help="基准设计，默认模块的 用例数据.json。")
    parser.add_argument("--baseline-label", help="基准设计的显示名，默认取文件名。")
    parser.add_argument("--alternative", action="append", default=[], type=Path,
                        help="候选设计，可重复；支持 用例数据.json 或 13/14 列工作簿。")
    parser.add_argument("--label", action="append", default=[], help="与 --alternative 顺序对应的显示名。")
    parser.add_argument("--scope-source", choices=("auto", "declared", "heuristic"), default="auto",
                        help="场景类型/设计方法来源：auto 优先声明字段，元数据深度不一时另给同源对照；"
                             "declared 只用声明字段；heuristic 全部用启发式。")
    parser.add_argument("--weights", help="六个维度权重，逗号分隔，默认 0.20,0.20,0.20,0.15,0.15,0.10。")
    parser.add_argument("--out-dir", type=Path, help="输出目录，默认模块目录。")
    parser.add_argument("--allow-link-issues", action="store_true",
                        help="候选存在不存在的编号时仍继续比较；报告会把编号门禁列为不通过。")
    args = parser.parse_args()
    try:
        module_dir = args.module_dir.resolve()
        baseline_path = (args.baseline or module_dir / "用例数据.json").resolve()
        if not args.alternative:
            raise ValidationError("至少提供一个 --alternative 候选设计。")
        spec_path = module_dir / "REQ清单.json"
        if not spec_path.is_file():
            raise ValidationError("模块缺少 REQ清单.json，无法确定 active 需求分母。")
        spec = load_json(spec_path)
        active = [req["id"] for req in spec["requirements"] if req["status"] == "active"]
        if not active:
            raise ValidationError("没有 active 需求，无法比较候选设计。")
        active_acs = [ac["id"] for req in spec["requirements"] if req["status"] == "active"
                      for ac in req.get("acceptance_criteria") or []]
        targets = load_link_targets(module_dir)

        dimensions = list(DIMENSIONS)
        if args.weights:
            parts = [part.strip() for part in args.weights.split(",") if part.strip()]
            if len(parts) != len(DIMENSIONS):
                raise ValidationError(f"--weights 必须为 {len(DIMENSIONS)} 个数值。")
            try:
                weights = [float(part) for part in parts]
            except ValueError as exc:
                raise ValidationError("--weights 必须为数值。") from exc
            if any(weight < 0 for weight in weights) or abs(sum(weights) - 1) > 1e-6:
                raise ValidationError("--weights 必须非负且合计为 1。")
            dimensions = [(name, weight) for (name, _), weight in zip(DIMENSIONS, weights)]

        designs = [load_design(baseline_path, targets)]
        if args.baseline_label and args.baseline_label.strip():
            designs[0]["label"] = args.baseline_label.strip()
        for position, path in enumerate(args.alternative):
            design = load_design(path, targets)
            if position < len(args.label) and args.label[position].strip():
                design["label"] = args.label[position].strip()
            designs.append(design)
        seen = [design["label"] for design in designs]
        if len(set(seen)) != len(seen):
            raise ValidationError("基准与候选的显示名重复，请用 --label/--baseline-label 区分。")
        attach_review(designs[0], module_dir / "评审结果.json")

        declared_all = all(case.get("场景类型") in SCOPES and case.get("设计方法")
                           for design in designs for case in design["cases"])
        if args.scope_source == "declared" and not declared_all:
            raise ValidationError("存在缺少 场景类型/设计方法 的候选，--scope-source declared 无法保证同口径；"
                                  "请改用 auto 或 heuristic。")
        primary_heuristic = args.scope_source == "heuristic"
        failed = [design for design in designs if design.get("link_issues")]
        if failed and not args.allow_link_issues:
            detail = "；".join(
                f"{design['label']} 编号问题 {len(design['link_issues'])} 处"
                f"（验收标准 {sum(1 for i in design['link_issues'] if i['字段'] == '验收标准')}、"
                f"测试点 {sum(1 for i in design['link_issues'] if i['字段'] == '关联测试点')}）"
                for design in failed)
            raise ValidationError(
                "候选设计存在不存在的编号，覆盖率不能按引用条数计算：" + detail
                + "。先跑 check_workbook_links.py 对齐编号，或在明确接受该缺陷时加 --allow-link-issues 继续比较。")
        primary = evaluate(designs, active, active_acs, dimensions, force_heuristic=primary_heuristic)
        sensitivity = None
        if args.scope_source == "auto" and not declared_all and not primary_heuristic:
            sensitivity = evaluate(designs, active, active_acs, dimensions, force_heuristic=True)

        report = {
            "schema_version": "1.0",
            "module": spec.get("module"),
            "code": spec.get("code"),
            "baseline_label": designs[0]["label"],
            "link_gate_overridden": bool(args.allow_link_issues),
            "alternative_labels": [design["label"] for design in designs[1:]],
            "weights": [weight for _, weight in dimensions],
            "dimensions": [name for name, _ in dimensions],
            "relative_scale": "各分项以本组较优者为100归一化后加权；总分是候选间的相对分，不是绝对质量分。",
            "not_a_review": "本结果不评审单份设计能否发布，也不执行被测系统。",
            "primary": primary,
            "sensitivity": sensitivity,
        }
        out_dir = (args.out_dir or module_dir).resolve()
        write_text(out_dir / "设计对比数据.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        write_text(out_dir / "设计对比报告.md", render(report))
        summary = "；".join(f"{design['label']} {design['total']:g}（{design['case_count']}条）"
                            for design in primary["designs"])
        print("候选设计对比（口径 A）：" + summary + f"。已写入 {out_dir}。")
        for item in primary["metrics"]:
            if item["link_status"] == "failed":
                print(f"关联编号门禁不通过：{item['label']} 有 {item['link_issue_count']} 处不存在的编号"
                      f"（验收标准 {item['dangling_ac_count']}、测试点 {item['dangling_tp_count']}）；"
                      f"有效 AC 覆盖 {item['ac_coverage']['covered']}/{item['ac_coverage']['total']}。")
            elif item["link_status"] == "unregistered":
                print(f"关联编号未登记：{item['label']} 没有可读的 `追溯` 派生表，AC/测试点覆盖无法核对。")
        if sensitivity is not None:
            print("口径 B（全组统一启发式）：" + "；".join(
                f"{design['label']} {design['total']:g}" for design in sensitivity["designs"])
                + "。两种口径结论不一致时以人工复核为准。")
        print("总分是候选间的相对分，不评审单份设计能否发布。")
        return 0
    except (ValidationError, OSError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
