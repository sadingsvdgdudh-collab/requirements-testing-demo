"""Read-only coverage and preparation reports; never execute the tested system.

Review conclusions and framework verification must have been checked by their
own validators. The helpers keep design approval, preparation, capabilities,
script availability and execution records separate instead of collapsing them
into one misleading "passed" count.
"""

from __future__ import annotations

from pathlib import Path

from qa_cases import ValidationError, coverage_data, percent, require_text


PREPARATION_CATEGORIES = ("业务规则", "账号", "状态准备", "资产", "观测接口")
PREPARATION_EFFECTS = ("manual", "automation")
UNVERIFIED_CATEGORY = "未核验准备"
REVIEW_VERDICTS = ("通过", "需确认", "不通过")


def _review_map(reviews, cases: list[dict]) -> dict:
    """Accept validated review rows, their TC-keyed mapping, or no reviews."""
    if reviews is None:
        return {}
    if isinstance(reviews, dict) and "reviews" in reviews:
        reviews = reviews["reviews"]
    if isinstance(reviews, dict):
        rows = list(reviews.values())
        for case_id, row in reviews.items():
            if not isinstance(row, dict) or row.get("用例编号") != case_id:
                raise ValidationError("评审映射的键须与用例编号一致。")
    elif isinstance(reviews, list):
        rows = reviews
    else:
        raise ValidationError("评审须为逐条数组或用例编号映射。")
    known = {case["用例编号"] for case in cases}
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError("逐条评审必须为对象。")
        case_id = require_text(row.get("用例编号"), "评审用例编号")
        if case_id not in known or case_id in result:
            raise ValidationError(f"评审引用未知或重复用例：{case_id}。")
        if row.get("结论") not in REVIEW_VERDICTS:
            raise ValidationError(f"{case_id} 评审结论须为通过/需确认/不通过。")
        result[case_id] = row
    return result


def review_coverage(spec: dict, cases: list[dict], reviews=None) -> dict:
    """Return full and design-approved linkage coverage with the same denominator.

``all_coverage`` is the unmodified legacy ``coverage_data`` result. Blocked and
deprecated requirements remain excluded; active nonfunctional requirements are
counted by the same rules as every other active requirement.
    """
    review_map = _review_map(reviews, cases)
    approved = [case for case in cases if review_map.get(case["用例编号"], {}).get("结论") == "通过"]

    def row(identifier: str, linked: list[dict], **labels) -> dict:
        case_ids = sorted(case["用例编号"] for case in linked)
        by_verdict = {
            verdict: [case_id for case_id in case_ids if review_map.get(case_id, {}).get("结论") == verdict]
            for verdict in REVIEW_VERDICTS
        }
        unreviewed = [case_id for case_id in case_ids if case_id not in review_map]
        return {
            "id": identifier,
            **labels,
            "all_case_ids": case_ids,
            "approved_case_ids": by_verdict["通过"],
            "pending_case_ids": by_verdict["需确认"],
            "rejected_case_ids": by_verdict["不通过"],
            "unreviewed_case_ids": unreviewed,
            "status": "covered" if by_verdict["通过"] else ("only_unapproved" if case_ids else "uncovered"),
        }

    req_rows, ac_rows = [], []
    for req in spec["requirements"]:
        if req["status"] != "active":
            continue
        req_rows.append(row(req["id"], [case for case in cases if req["id"] in case["关联需求"]], title=req["title"]))
        for ac in req["acceptance_criteria"]:
            ac_rows.append(row(ac["id"], [case for case in cases if ac["id"] in case["验收标准"]],
                               requirement_id=req["id"], text=ac["text"]))
    return {
        "all_coverage": coverage_data(spec, cases),
        "approved_coverage": coverage_data(spec, approved),
        "matrix": {"requirements": req_rows, "acceptance_criteria": ac_rows},
    }


def render_review_coverage(report: dict) -> str:
    """Render both rates and distinguish missing links from unapproved support."""
    all_data, approved = report["all_coverage"], report["approved_coverage"]
    lines = [f"# {all_data['module']}用例关联覆盖与评审覆盖", "",
             "范围：active需求；两种覆盖使用相同REQ/AC分母。", "",
             "| 统计范围 | 用例数 | REQ关联 | AC关联 |", "| --- | ---: | --- | --- |"]
    for label, data in (("全部用例", all_data), ("设计评审通过用例", approved)):
        req, ac = data["requirement_coverage"], data["acceptance_coverage"]
        lines.append(f"| {label} | {data['case_count']} | {req['covered']}/{req['total']} ({percent(req['rate'])}) | "
                     f"{ac['covered']}/{ac['total']} ({percent(ac['rate'])}) |")
    lines.extend(["", "关联覆盖不证明用例充分、可执行或软件测试通过；未评审、需确认和不通过用例不计入通过用例覆盖。", ""])
    for key, title in (("requirements", "REQ关联矩阵"), ("acceptance_criteria", "AC关联矩阵")):
        lines.extend([f"## {title}", "", "| 编号 | 全部TC | 通过TC | 需确认TC | 不通过TC | 未评审TC | 状态 |",
                      "| --- | --- | --- | --- | --- | --- | --- |"])
        for item in report["matrix"][key]:
            cells = [item["id"]] + ["、".join(item[field]) or "无" for field in
                                    ("all_case_ids", "approved_case_ids", "pending_case_ids", "rejected_case_ids", "unreviewed_case_ids")] + [item["status"]]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def render_scope_applicability(scopes: list[dict] | None, methods: list[dict] | None) -> str:
    """把评审的六类场景/六种方法适用性结论并入覆盖报告。

    适用性判断属于评审，不属于关联覆盖；两者放在同一份交付报告里，避免收件人
    只看到 REQ/AC 100% 就以为场景与方法都已覆盖。
    """
    if not scopes and not methods:
        return ""
    lines = ["", "## 场景类型与设计方法适用性（取自已发布评审）", "",
             "本节是评审结论的转录，不重新判定适用性；缺失项必须作为阻塞处理。", ""]
    for title, items, note in (("六类场景", scopes or [], "场景"), ("六种设计方法", methods or [], "方法")):
        lines.extend([f"### {title}", "", "| 项目 | 状态 | 证据 | 理由 |", "| --- | --- | --- | --- |"])
        for item in items:
            evidence = "、".join(item["evidence"]) or "无"
            reason = str(item["reason"]).replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {item['item']} | {item['status']} | {evidence.replace('|', chr(92) + '|')} | {reason} |")
        absent = [item["item"] for item in items if item["status"] == "missing"]
        lines.extend(["", f"缺失的{note}：" + ("、".join(absent) or "无。")])
        not_applicable = [item["item"] for item in items if item["status"] == "not_applicable"]
        lines.extend([f"不适用的{note}：" + ("、".join(not_applicable) or "无。") + "", ""])
    return "\n".join(lines)


def validate_preparation(cases: list[dict]) -> None:
    """Validate optional ``preparation`` metadata and shared issue definitions.

Omitting metadata preserves legacy readability but means readiness is unknown.
An explicit empty array declares that preparation was checked and no blocking
items were identified; it is distinct from an absent field.
    """
    definitions = {}
    for case in cases:
        case_id = case["用例编号"]
        if "preparation" not in case:
            continue
        items = case["preparation"]
        if not isinstance(items, list):
            raise ValidationError(f"{case_id}/preparation 须为数组。")
        seen = set()
        for index, item in enumerate(items, 1):
            label = f"{case_id}/preparation第{index}项"
            if not isinstance(item, dict):
                raise ValidationError(f"{label} 须为对象。")
            identifier = require_text(item.get("id"), label + "/id").strip()
            if identifier in seen:
                raise ValidationError(f"{case_id} 含重复准备项：{identifier}。")
            seen.add(identifier)
            if item.get("category") not in PREPARATION_CATEGORIES:
                raise ValidationError(f"{label}/category 须为：{'、'.join(PREPARATION_CATEGORIES)}。")
            description = require_text(item.get("description"), label + "/description").strip()
            action = require_text(item.get("action"), label + "/action").strip()
            if item.get("status") not in ("open", "resolved"):
                raise ValidationError(f"{label}/status 须为open/resolved。")
            effects = item.get("affects")
            if not isinstance(effects, list) or not effects or any(effect not in PREPARATION_EFFECTS for effect in effects):
                raise ValidationError(f"{label}/affects 须为含manual或automation的非空数组。")
            if len(effects) != len(set(effects)):
                raise ValidationError(f"{label}/affects 含重复项。")
            resolution = require_text(item.get("resolution", ""), label + "/resolution", allow_empty=item["status"] == "open").strip()
            definition = (item["category"], description, action, item["status"], tuple(sorted(effects)), resolution)
            if identifier in definitions and definitions[identifier] != definition:
                raise ValidationError(f"准备项 {identifier} 在不同用例中定义不一致。")
            definitions[identifier] = definition


def _script_availability(script, project_root, framework_root) -> tuple[bool | None, str, str | None]:
    """Check one exact local file path without running or importing the script."""
    if not isinstance(script, str) or not script.strip():
        return False, "未提供自动化脚本文件路径。", None
    try:
        roots = [Path(root).resolve() for root in (project_root, framework_root) if root is not None]
        if not roots:
            return None, "未提供项目/框架根目录，脚本位置无法核验。", None
        source = Path(script.strip())
        candidates = [source.resolve()] if source.is_absolute() else [(root / source).resolve() for root in roots]
        valid = sorted({candidate for candidate in candidates
                        if any(candidate.is_relative_to(root) for root in roots) and candidate.is_file()}, key=str)
    except (OSError, ValueError, RuntimeError, TypeError):
        return False, "自动化脚本不是可核验的本地文件路径。", None
    if not valid:
        return False, "脚本不存在或位于允许的项目/框架目录之外。", None
    if len(valid) != 1:
        return None, "相对脚本路径匹配多个文件，请使用明确的本地路径。", None
    return True, "脚本文件存在且在允许目录内；未运行，也未证明脚本实现正确。", str(valid[0])


def build_preparation_report(spec: dict, cases: list[dict], reviews=None, *, project_root=None, framework_root=None) -> dict:
    """Group explicit open items and report independent readiness dimensions.

Only the framework validator's computed flags are used; free-text review
reasons, automation details and execution text never establish readiness.
    """
    validate_preparation(cases)
    review_map = _review_map(reviews, cases)
    checklist = {}
    readiness = []
    for case in cases:
        case_id = case["用例编号"]
        declared = "preparation" in case
        preparations = case.get("preparation", [])
        open_items = [item for item in preparations if item["status"] == "open"]
        if not declared:
            open_items = [{"id": "unverified-preparation:" + case_id, "category": UNVERIFIED_CATEGORY,
                           "description": "未提供结构化准备记录，人工与自动化执行准备尚未核验。",
                           "action": "核对业务规则、账号、状态准备、资产和观测接口；登记准备项，确认无缺口时显式填写preparation: []。",
                           "status": "open", "affects": list(PREPARATION_EFFECTS)}]
        for item in open_items:
            # Prefix grouping keys so a user-defined ID cannot swallow a synthetic issue.
            key = ("declared" if declared else "unverified", item["id"].strip())
            if key not in checklist:
                checklist[key] = {field: item[field] for field in ("id", "category", "description", "action", "status", "affects")}
                checklist[key].update(unverified=not declared, affected_case_ids=[], affected_requirement_ids=[], affected_acceptance_criteria_ids=[])
            combined = checklist[key]
            combined["affected_case_ids"].append(case_id)
            combined["affected_requirement_ids"].extend(case["关联需求"])
            combined["affected_acceptance_criteria_ids"].extend(case["验收标准"])

        approved = review_map.get(case_id, {}).get("结论") == "通过"
        manual_blockers = [item["id"] for item in open_items if "manual" in item["affects"]]
        automation_blockers = [item["id"] for item in open_items if "automation" in item["affects"]]
        verified = case.get("_automation_verified") is True
        framework_ready = case.get("_automation_ready") is True
        automation_eligible = case.get("是否可自动化") == "是"
        script_verified, script_note, script_path = _script_availability(case.get("自动化脚本"), project_root, framework_root)
        manual_ready = (not manual_blockers if declared else None) if approved else False
        # Evidence verification alone does not establish scenario capability or readiness.
        capability_ready = (verified and framework_ready and not automation_blockers if declared else None) if approved and automation_eligible else False
        if not approved or not automation_eligible:
            automation_ready = False
        elif not declared or "_automation_verified" not in case or "_automation_ready" not in case:
            automation_ready = None
        elif not capability_ready:
            automation_ready = False
        else:
            automation_ready = script_verified
        readiness.append({
            "case_id": case_id,
            "review_verdict": review_map.get(case_id, {}).get("结论", "未评审"),
            "design_approved": approved,
            "preparation_declared": declared,
            "manual_ready": manual_ready,
            "manual_blocker_ids": manual_blockers,
            "capability_verified": verified,
            "capability_ready": capability_ready,
            "automation_ready": automation_ready,
            "automation_blocker_ids": automation_blockers,
            "automation_reasons": list(case.get("_automation_reasons", [])),
            "script_verified": script_verified,
            "script_note": script_note,
            "script_path": script_path,
            "execution_records_present": isinstance(case.get("实际结果"), str) and bool(case["实际结果"].strip()),
        })

    items = list(checklist.values())
    for item in items:
        for field in ("affected_case_ids", "affected_requirement_ids", "affected_acceptance_criteria_ids"):
            item[field] = sorted(set(item[field]))
    items.sort(key=lambda item: (item["category"], item["id"]))
    groups = [{"category": category, "items": [item for item in items if item["category"] == category]}
              for category in (*PREPARATION_CATEGORIES, UNVERIFIED_CATEGORY)]
    counts = {field: sum(item[field] is True for item in readiness)
              for field in ("design_approved", "manual_ready", "capability_verified", "capability_ready", "automation_ready", "execution_records_present")}
    counts.update(case_count=len(cases), preparation_unknown=sum(not item["preparation_declared"] for item in readiness),
                  manual_readiness_unknown=sum(item["manual_ready"] is None for item in readiness),
                  automation_readiness_unknown=sum(item["automation_ready"] is None for item in readiness),
                  open_preparation_items=len(items))
    return {
        "schema_version": "1.0",
        "module": spec["module"],
        "code": spec["code"],
        "counts": counts,
        "case_readiness": readiness,
        "open_preparation": items,
        "groups": groups,
        "execution_note": "实际结果非空仅表示存在执行记录；未执行系统，不从自由文本推定测试通过。",
    }


def render_preparation_report(report: dict) -> str:
    """Render an actionable checklist outside the fixed 13-column workbook."""
    counts = report["counts"]
    lines = [f"# {report['module']}执行准备清单", "", f"用例总数：{counts['case_count']}。", "",
             f"- 设计评审通过：{counts['design_approved']}。",
             f"- 人工执行准备已就绪：{counts['manual_ready']}；状态未核验：{counts['manual_readiness_unknown']}。",
             f"- 框架能力证据已核验：{counts['capability_verified']}；场景能力与准备已就绪：{counts['capability_ready']}。",
             f"- 自动化执行准备已就绪（含本地脚本文件）：{counts['automation_ready']}；状态未核验：{counts['automation_readiness_unknown']}。",
             f"- 含实际结果记录：{counts['execution_records_present']}。", "", report["execution_note"], "",
             "脚本存在和框架能力已核验不证明脚本实现正确或实际运行成功；准备就绪与测试通过是独立结论。", ""]
    for group in report["groups"]:
        if not group["items"]:
            continue
        lines.extend([f"## {group['category']}", ""])
        for item in group["items"]:
            lines.extend([f"### {item['id']}", "", item["description"], "",
                          f"补齐办法：{item['action']}",
                          "影响通道：" + "、".join(item["affects"]),
                          "涉及用例：" + "、".join(item["affected_case_ids"]),
                          "涉及AC：" + ("、".join(item["affected_acceptance_criteria_ids"]) or "无"), ""])
    if not report["open_preparation"]:
        lines.extend(["没有登记中的未决准备项；是否可执行仍须核对逐条设计与自动化能力状态。", ""])
    lines.extend(["## 逐条准备状态", "",
                  "| TC | 设计通过 | 人工就绪 | 场景能力就绪 | 自动化就绪 | 含执行记录 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    def label(value):
        return "未核验" if value is None else ("是" if value else "否")
    for item in report["case_readiness"]:
        values = [item["case_id"]] + [label(item[field]) for field in
                                      ("design_approved", "manual_ready", "capability_ready", "automation_ready", "execution_records_present")]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"
