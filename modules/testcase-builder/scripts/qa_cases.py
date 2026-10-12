"""Shared validation and read-only XLSX checks for the QA skill commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HEADERS = ["用例编号", "优先级", "用例名称", "前置条件", "操作步骤", "测试数据", "预期结果", "实际结果",
           "关联需求", "是否可自动化", "自动化建议", "自动化建议明细", "自动化脚本"]
# 追溯表的列：只呈现前 13 列之外、按契约仅存 JSON 的关联字段。
METRICS_HEADERS = ["用例编号", "优先级", "关联需求", "验收标准", "场景类型", "设计方法", "关联测试点"]
AUTOMATION_ADVICES = ("UI自动化可用", "UI自动化可做，需采集新资产", "仅api自动化可用", "双通道均可用",
                       "不建议自动化", "本项目尚无自动化框架", "当前框架能力不足", "待核对框架能力")
# 每种取值要求 自动化建议明细 必须包含的段落前缀，避免自由文本退化成一句空话。
ADVICE_DETAIL_SECTIONS = {
    "UI自动化可用": ("理由：", "已有函数/资源："),
    "UI自动化可做，需采集新资产": ("理由：", "已有函数/资源：", "需采集新资产："),
    "仅api自动化可用": ("理由：", "已有函数/资源："),
    "双通道均可用": ("理由：", "已有函数/资源："),
    "不建议自动化": ("理由：", "判定依据："),
    # 项目尚未引入自动化框架时使用：无通道可判，只要求写明理由，避免为凑枚举而谎称通道可用。
    "本项目尚无自动化框架": ("理由：",),
    "当前框架能力不足": ("理由：", "缺失能力：", "补齐办法："),
    "待核对框架能力": ("理由：", "待核对能力：", "核对办法："),
}
SCOPES = ["正常", "备选", "异常", "边界", "非功能", "端到端"]
METHODS = ["场景法", "等价类", "边界值", "错误推测", "判定表", "状态迁移"]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class ValidationError(ValueError):
    pass


def require_final(data: dict, label: str) -> None:
    if "draft" in data and data["draft"] is not False:
        raise ValidationError(f"{label}为草稿或draft标记不合法。")
    if "update_state" in data and data["update_state"] != "resolved":
        raise ValidationError(f"{label}仍需更新处理：{data['update_state']}。")
    if data.get("missing_fields") or data.get("needs_redesign"):
        raise ValidationError(f"{label}仍有缺字段或待重设计标记。")


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path.name}: 顶层必须为对象。")
    return data


def write_text(path: Path, value: str) -> None:
    """Atomically publish tool-derived output, not input data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".qa-", delete=False) as stream:
        temp_path = Path(stream.name)
        stream.write(value)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def case_text_fragments(value) -> list[str]:
    """Split a precondition into fragments for readability checks.

    A fragment is the unit that should appear only once per module. Both ``；``
    and ``。`` end a fragment: real data joined whole clauses with periods, and
    without splitting on them a shared sentence followed by a case-specific one
    looked like a single "unique" fragment. The same split is used by the
    readability checker and its tests so that the two can never drift apart.
    """
    if isinstance(value, list):
        candidates = [str(item) for item in value]
    else:
        candidates = re.split(r"[；;。\n]", str(value))
    return [part.strip() for part in candidates if len(part.strip()) > 1]


def module_shared_lines(data: dict) -> dict:
    """Read the optional module-level preconditions and evidence setup.

    Absent fields mean "not registered", which stays readable for legacy data;
    the readability checker reports the resulting duplication instead of
    rejecting the module outright.
    """
    shared = {}
    for key in ("module_preconditions", "evidence_setup"):
        value = data.get(key)
        if value is None:
            shared[key] = []
        elif isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
            shared[key] = [item.strip() for item in value]
        else:
            raise ValidationError(f"{key} 必须为非空文本数组。")
    return shared


def merged_precondition(data: dict, case: dict) -> str:
    """Compose the readable precondition: shared lines first, then case lines."""
    shared = module_shared_lines(data)
    lines = shared["module_preconditions"] + case_text_fragments(case["前置条件"])
    if shared["evidence_setup"]:
        lines += ["取证要求：" + item for item in shared["evidence_setup"]]
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))


def validate_spec(module_dir: Path, project_dir: Path | None = None) -> dict:
    validator = Path(__file__).resolve().parents[2] / "req-processor" / "scripts" / "validate_spec.py"
    if not validator.is_file():
        raise ValidationError("缺少同包的需求校验模块，请保持整个技能文件夹完整。")
    command = [sys.executable, str(validator), "--module-dir", str(module_dir)]
    if project_dir is not None:
        command += ["--project-dir", str(project_dir)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValidationError(f"需求规范校验失败：{detail or '请检查 REQ清单.json 和需求规范.md。'}")
    spec = load_json(module_dir / "REQ清单.json")
    if spec.get("schema_version") != "1.0":
        raise ValidationError("REQ清单 schema_version 必须为1.0。")
    return spec


def require_text(value, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValidationError(f"{label} 必须为{'可为空的' if allow_empty else '非空'}文本。")
    return value


def unique_list(value, label: str, *, allow_empty: bool = False, allow_string: bool = True) -> list[str]:
    if isinstance(value, str) and allow_string:
        value = [part for part in re.split(r"[,，;；、\s]+", value.strip()) if part]
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValidationError(f"{label} 必须为{'可为空的' if allow_empty else '非空'}数组。")
    result = [require_text(item, label).strip() for item in value]
    if len(result) != len(set(result)):
        raise ValidationError(f"{label} 含重复项。")
    return result


def steps(value, label: str) -> list[str]:
    if isinstance(value, str):
        return [require_text(value, label)]
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{label} 必须为非空步骤数组或文本。")
    return [require_text(item, label) for item in value]


def validate_cases(data: dict, spec: dict | None = None, *, draft: bool = False,
                   module_dir: Path | None = None) -> list[dict]:
    if not draft:
        require_final(data, "用例数据")
    if data.get("schema_version") != "1.0":
        raise ValidationError("用例数据 schema_version 必须为1.0。")
    code = data.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2,4}", code):
        raise ValidationError("用例模块 code 必须为2–4位大写字母。")
    require_text(data.get("module"), "module")
    # 公共段只做格式校验：缺省表示未登记（旧资料保持可读），登记了就必须是文本数组。
    module_shared_lines(data)
    if spec is not None and (data["module"] != spec.get("module") or code != spec.get("code")):
        raise ValidationError("用例 module/code 与 REQ清单不一致。")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValidationError("cases 必须为非空数组。")
    reqs = {req["id"]: req for req in spec["requirements"]} if spec else {}
    ac_owner = {ac["id"]: req["id"] for req in reqs.values() for ac in req.get("acceptance_criteria", [])}
    result = []
    seen = set()
    for index, item in enumerate(raw_cases, 1):
        if not draft and isinstance(item, dict):
            require_final(item, f"第{index}条用例")
        if not isinstance(item, dict):
            raise ValidationError(f"第{index}条用例必须为对象。")
        missing = [key for key in HEADERS if key not in item]
        if missing:
            raise ValidationError(f"第{index}条用例缺少字段：{'、'.join(missing)}。")
        case = dict(item)
        case_id = require_text(case["用例编号"], f"第{index}条用例编号")
        match = re.fullmatch(rf"TC_{re.escape(code)}_([0-9]{{3,}})", case_id)
        if not match or int(match.group(1)) < 1:
            raise ValidationError(f"用例编号格式错误：{case_id}。")
        if case_id in seen:
            raise ValidationError(f"重复用例编号：{case_id}。")
        seen.add(case_id)
        for key in HEADERS:
            if key not in ("操作步骤", "预期结果", "关联需求"):
                require_text(case[key], f"{case_id}/{key}", allow_empty=key in ("实际结果", "自动化脚本"))
        if case["优先级"] not in ("P0", "P1", "P2", "P3"):
            raise ValidationError(f"{case_id} 优先级必须为P0/P1/P2/P3。")
        if case["是否可自动化"] not in ("是", "否", "待定"):
            raise ValidationError(f"{case_id} 是否可自动化必须为是/否/待定。")
        if case["自动化建议"] not in AUTOMATION_ADVICES:
            raise ValidationError(f"{case_id} 自动化建议必须为：{'、'.join(AUTOMATION_ADVICES)}。")
        if (case["是否可自动化"] == "否") != (case["自动化建议"] == "不建议自动化"):
            raise ValidationError(
                f"{case_id} 是否可自动化与自动化建议不一致：仅「否」可配「不建议自动化」，其余取值必须给出可自动化通道。")
        advice = case["自动化建议"]
        if advice in ("当前框架能力不足", "待核对框架能力") and case["是否可自动化"] != "待定":
            raise ValidationError(f"{case_id} 的「{advice}」必须配「是否可自动化=待定」。")
        missing_sections = [section for section in ADVICE_DETAIL_SECTIONS[advice]
                            if section not in case["自动化建议明细"]]
        if missing_sections:
            raise ValidationError(
                f"{case_id}/自动化建议明细 缺少必要段落：{'、'.join(missing_sections)}"
                f"（{advice} 要求的格式）。")
        from automation_evidence import evaluate_automation
        try:
            automation = evaluate_automation(case, module_dir)
        except ValueError as exc:
            raise ValidationError(f"{case_id}/自动化能力证据：{exc}") from exc
        case["_automation_verified"] = automation["verified"]
        case["_automation_ready"] = automation["ready"]
        case["_automation_reasons"] = automation["reasons"]
        case["操作步骤"] = steps(case["操作步骤"], f"{case_id}/操作步骤")
        case["预期结果"] = steps(case["预期结果"], f"{case_id}/预期结果")
        case["关联需求"] = unique_list(case["关联需求"], f"{case_id}/关联需求", allow_empty=draft)
        case["验收标准"] = unique_list(case.get("验收标准"), f"{case_id}/验收标准", allow_empty=draft)
        if case.get("场景类型") not in SCOPES:
            raise ValidationError(f"{case_id} 场景类型必须为：{'、'.join(SCOPES)}。")
        case["设计方法"] = unique_list(case.get("设计方法"), f"{case_id}/设计方法")
        if set(case["设计方法"]) - set(METHODS):
            raise ValidationError(f"{case_id} 含未知设计方法。")
        if not draft:
            if spec is None:
                raise ValidationError("正式用例必须提供经校验的 REQ清单。")
            for req_id in case["关联需求"]:
                if req_id not in reqs:
                    raise ValidationError(f"{case_id} 关联了未知需求：{req_id}。")
                if reqs[req_id]["status"] != "active":
                    raise ValidationError(f"{case_id} 只能关联 active 需求，{req_id} 为{reqs[req_id]['status']}。")
            for ac_id in case["验收标准"]:
                if ac_owner.get(ac_id) not in case["关联需求"]:
                    raise ValidationError(f"{case_id} 验收标准 {ac_id} 未属于该用例关联的需求。")
        result.append(case)
    # Imported lazily: reporting also reuses this module's basic validators.
    from case_reporting import validate_preparation
    validate_preparation(result)
    return result


def case_matrix(cases: list[dict], *, draft: bool = False) -> list[list[str]]:
    rows = [HEADERS]
    for case in cases:
        row = []
        for key in HEADERS:
            value = case[key]
            if key in ("操作步骤", "预期结果"):
                value = "\n".join(f"{index}. {text}" for index, text in enumerate(value, 1))
            elif key == "关联需求":
                value = "\n".join(value) or ("未核验（草稿）" if draft else "")
            row.append(value)
        rows.append(row)
    return rows


def linked_test_points(case: dict) -> list[str]:
    """测试点关联在旧资料里可能是数组或分隔字符串；统一成数组，缺失即为空。"""
    value = case.get("关联测试点")
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[,，;；、\s]+", value.strip()) if part]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValidationError("关联测试点 必须为文本数组或分隔字符串。")


def trace_rows(cases: list[dict], *, draft: bool = False) -> list[list[str]]:
    """逐条追溯行：呈现只存 JSON 的关联字段。

    前 13 列契约不变；本视图让单独交付的工作簿仍能读出需求、验收标准、场景
    类型、设计方法与测试点关联——否则"未登记"和"登记为空"在工作簿里无法区分。
    """
    blank = "未核验（草稿）" if draft else "未登记"
    rows = []
    for case in cases:
        methods = case.get("设计方法") or []
        rows.append([
            case["用例编号"],
            case["优先级"],
            "\n".join(case.get("关联需求") or []) or blank,
            "\n".join(case.get("验收标准") or []) or blank,
            case.get("场景类型") or blank,
            "、".join(methods) if isinstance(methods, list) else str(methods),
            "\n".join(linked_test_points(case)) or blank,
        ])
    return rows


def load_link_targets(module_dir: Path) -> dict:
    """读编号基准：REQ清单 与 测试点数据，供编号存在性校验。

    ``point_ids`` 为空且 ``has_points`` 为假时表示模块尚无测试点数据，测试点
    关联不参与判定——而不是把"无法核对"当成"核对通过"。
    """
    spec = load_json(module_dir / "REQ清单.json")
    reqs = {req["id"]: req for req in spec.get("requirements") or []}
    ac_owner = {}
    for req_id, req in reqs.items():
        for ac in req.get("acceptance_criteria") or []:
            ac_id = ac["id"] if isinstance(ac, dict) else str(ac)
            ac_owner[ac_id] = req_id
    point_ids, has_points = set(), False
    points_path = module_dir / "测试点数据.json"
    if points_path.is_file():
        has_points = True
        for point in load_json(points_path).get("testpoints") or []:
            if point.get("测试点编号"):
                point_ids.add(point["测试点编号"])
    return {"spec": spec, "reqs": reqs, "ac_owner": ac_owner,
            "point_ids": point_ids, "has_points": has_points}


def read_trace_sheet(path: str | Path) -> dict:
    """反向读取工作簿的 `追溯` 派生表；没有该表时返回空映射。

    单独交付的工作簿是唯一来源，前 13 列按契约不含 AC/场景/方法/测试点，
    因此缺 `追溯` 时这些字段按"未登记"处理，不能推断成已登记。
    """
    try:
        sheet = read_xlsx(path, columns=len(METRICS_HEADERS), sheet_name="追溯")
    except ValidationError:
        return {}
    header = [str(value).strip() for value in sheet["rows"][0]]
    if header[:len(METRICS_HEADERS)] != METRICS_HEADERS:
        return {}
    trace = {}
    for row in sheet["rows"][1:]:
        case_id = str(row[0]).strip()
        if not case_id:
            continue
        trace[case_id] = {
            "关联需求": split_registered(row[2]),
            "验收标准": split_registered(row[3]),
            "场景类型": "" if unregistered(row[4]) else str(row[4]).strip(),
            "设计方法": split_registered(row[5], separator="、"),
            "关联测试点": split_registered(row[6]),
        }
    return trace


def unregistered(value) -> bool:
    """追溯表用占位文本表示"未登记/未核验"，不是真实编号。"""
    return str(value or "").strip() in ("", "未登记", "未核验（草稿）", "未核验(草稿)")


def split_registered(value, *, separator: str = r"[,，;；、\s]+") -> list[str]:
    return [] if unregistered(value) else [part for part in re.split(separator, str(value)) if part.strip()]


def link_list(value) -> list[str]:
    """把关联字段规范成去空数组；用于核对，不重复做 validate_cases 的严格校验。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[,，;；、\s]+", value.strip()) if part]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValidationError("关联字段必须为文本数组或分隔字符串。")


def audit_case_links(cases: list[dict], targets: dict) -> list[dict]:
    """逐条核对 REQ/AC/测试点编号是否存在、归属是否成立。

    这是 JSON 校验器之外的补充出口：`validate_cases` 只在 JSON 侧抛错，一旦
    交付物是别人给的工作簿，就必须靠这里把"引用了一个不存在的编号"查出来，
    否则覆盖率会按引用条数虚高。
    """
    reqs, ac_owner = targets["reqs"], targets["ac_owner"]
    point_ids, has_points = targets["point_ids"], targets["has_points"]
    issues = []
    for case in cases:
        case_id = str(case.get("用例编号") or "").strip()
        req_links = link_list(case.get("关联需求"))
        ac_links = link_list(case.get("验收标准"))
        point_links = linked_test_points(case)
        for req_id in req_links:
            if req_id not in reqs:
                issues.append(issue(case_id, "关联需求", req_id, "需求在 REQ清单中不存在"))
            elif reqs[req_id].get("status") != "active":
                issues.append(issue(case_id, "关联需求", req_id,
                                    f"需求为 {reqs[req_id].get('status')}，不可作为正式用例依据"))
        for ac_id in ac_links:
            if ac_id not in ac_owner:
                issues.append(issue(case_id, "验收标准", ac_id, "验收标准在 REQ清单中不存在"))
            elif ac_owner[ac_id] not in req_links:
                issues.append(issue(case_id, "验收标准", ac_id,
                                    f"验收标准属于 {ac_owner[ac_id]}，未列在该用例的关联需求中"))
        if has_points:
            for point_id in point_links:
                if point_id not in point_ids:
                    issues.append(issue(case_id, "关联测试点", point_id, "测试点在测试点数据中不存在"))
    return issues


def issue(case_id: str, field: str, value: str, reason: str) -> dict:
    return {"用例编号": case_id, "字段": field, "编号": value, "问题": reason}


def case_scope_counts(cases: list[dict]) -> dict:
    """按六类场景统计用例数。未登记的旧资料不进任何一类。"""
    counts = {name: 0 for name in SCOPES}
    for case in cases:
        scope = case.get("场景类型")
        if scope in counts:
            counts[scope] += 1
    return counts


def case_method_counts(cases: list[dict]) -> dict:
    """按六种设计方法统计引用次数；一条用例可计多项。"""
    counts = {name: 0 for name in METHODS}
    for case in cases:
        for method in case.get("设计方法") or []:
            if method in counts:
                counts[method] += 1
    return counts


def distribution_summary(counts: dict, order: list[str]) -> dict:
    by_item = {name: counts.get(name, 0) for name in order}
    return {"by_item": by_item, "without_cases": [name for name in order if not by_item[name]]}


def coverage_data(spec: dict, cases: list[dict]) -> dict:
    active = [req for req in spec["requirements"] if req["status"] == "active"]
    req_ids = {req["id"] for req in active}
    ac_ids = {ac["id"] for req in active for ac in req["acceptance_criteria"]}
    used_reqs = {item for case in cases for item in case["关联需求"]}
    used_acs = {item for case in cases for item in case["验收标准"]}
    def rate_info(all_ids, used):
        covered = all_ids & used
        return {"total": len(all_ids), "covered": len(covered), "rate": len(covered) / len(all_ids) if all_ids else None, "missing": sorted(all_ids - used)}
    excluded = {"blocked": [], "deprecated": []}
    for req in spec["requirements"]:
        if req["status"] in excluded:
            questions = [question["id"] for question in spec.get("questions", []) if question.get("status") == "open" and (not question.get("affected_requirements") or req["id"] in question["affected_requirements"])]
            excluded[req["status"]].append({"id": req["id"], "title": req["title"], "reason": req.get("status_reason") or ("待澄清：" + "、".join(questions) if questions else req["description"])})
    passed = bool(active) and req_ids <= used_reqs and ac_ids <= used_acs
    # 本返回值同时被测试点评审结果缓存并逐字复核（point_common.load_current_review），
    # 因此只放稳定字段：往里加键会让所有既有测试点评审一夜之间"与当前数据不一致"。
    # 场景/方法分布属于报告层，由 check_coverage.py 另行计算。
    return {"schema_version": "1.0", "module": spec["module"], "code": spec["code"], "scope": "已确认范围（active需求）", "status": "passed" if passed else ("not_designable" if not active else "failed"), "case_count": len(cases), "requirement_coverage": rate_info(req_ids, used_reqs), "acceptance_coverage": rate_info(ac_ids, used_acs), "excluded": excluded}


def percent(value) -> str:
    return "不可计算" if value is None else f"{value:.1%}"


def render_distribution(distribution: dict | None, *, approved: bool = False) -> list[str]:
    """场景类型与设计方法的实际分布；缺类只告警，不改变关联覆盖门禁。

    设计规则要求端到端、非功能等按适用性设计，不适用要说明理由。用例分布本身
    不能判定适用性（判断在评审的 scope_checks 里），所以这里只把"整类没有用例"
    显式列出，避免 100% 关联覆盖掩盖场景缺口。
    """
    if not distribution:
        return []
    suffix = "（设计评审通过用例）" if approved else "（全部用例）"
    lines = []
    for title, key, unit in (("场景类型分布", "scope", "六类场景"),
                             ("设计方法分布", "method", "六种方法")):
        summary = distribution.get(key)
        if not summary:
            continue
        lines.extend(["", f"## {title}{suffix}", "", "| 项目 | 用例数 |", "| --- | ---: |"])
        lines.extend(f"| {name} | {count} |" for name, count in summary["by_item"].items())
        if summary["without_cases"]:
            lines.extend(["", f"用例数为 0 的{unit}：" + "、".join(summary["without_cases"]) + "。",
                          "该提示不判定适用性：如经评审确认为不适用，请在评审数据的 scope_checks/"
                          "method_checks 写明理由；适用但无用例则属于设计缺失。"])
        else:
            lines.extend(["", f"全部{unit}均有用例。"])
    return lines


def coverage_report(data: dict, distribution: dict | None = None) -> str:
    req, ac = data["requirement_coverage"], data["acceptance_coverage"]
    lines = [f"# {data['module']}需求关联覆盖率报告", "", f"范围：{data['scope']}。用例数：{data['case_count']}。", "", f"REQ关联：{req['covered']}/{req['total']}，{percent(req['rate'])}。", f"验收标准关联：{ac['covered']}/{ac['total']}，{percent(ac['rate'])}。", "", "该指标仅检查关联完整性，不能证明用例正确、测试充分或软件已通过测试。", ""]
    if data["status"] == "passed":
        lines.append("关联覆盖门禁通过：REQ和验收标准关联均为100%。")
    elif data["status"] == "not_designable":
        lines.append("不可设计：没有active需求，不能报告100%覆盖率。")
    else:
        lines.append("关联覆盖门禁未通过。")
    for label, info in (("未覆盖REQ", req), ("未覆盖验收标准", ac)):
        lines.extend(["", f"{label}：" + ("、".join(info["missing"]) or "无。")])
    lines.extend(render_distribution(distribution))
    if distribution and distribution.get("approved_scope"):
        lines.extend(render_distribution({"scope": distribution["approved_scope"],
                                          "method": distribution.get("approved_method")}, approved=True))
    for status, label in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        lines.extend(["", f"{label}（{len(data['excluded'][status])}条，不计入分母）："])
        lines.extend([f"- {item['id']} {item['title']}：{item['reason']}" for item in data["excluded"][status]] or ["无。"])
    return "\n".join(lines) + "\n"


def _run_xlsx_node(payload: dict, temporary: Path, runtime: dict) -> str:
    """走 Node 作者；返回空串表示成功，返回原因表示因缺依赖需要回退。"""
    node = os.environ.get("QA_NODE_EXECUTABLE") or runtime.get("node_executable") or shutil.which("node")
    if node and not Path(node).is_file():
        node = shutil.which(node)
    if not node or not Path(node).is_file():
        return "缺少 Node.js"
    environment = os.environ.copy()
    if not environment.get("QA_NODE_MODULES") and runtime.get("node_modules"):
        environment["QA_NODE_MODULES"] = runtime["node_modules"]
    result = subprocess.run([node, str(Path(__file__).with_name("xlsx_io.mjs"))],
                            input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                            text=True, encoding="utf-8", env=environment)
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        # 只有“依赖缺失”才回退；业务校验失败必须照原样报错，不能被回退掩盖。
        if "@oai/artifact-tool" in message:
            return message
        raise ValidationError("Excel生成失败：" + message)
    return ""


def _run_xlsx_python(payload: dict, temporary: Path) -> None:
    """Python 回退作者：同一 13 列契约，读取侧仍是原有的 read_xlsx。"""
    from xlsx_fallback import XlsxWriteError, write_workbook
    try:
        write_workbook(payload, temporary)
    except XlsxWriteError as exc:
        raise ValidationError(f"Excel生成失败：{exc}") from exc


def run_xlsx(payload: dict, output: Path, *, publication_guard=None) -> None:
    runtime_file = Path(__file__).with_name("runtime.json")
    runtime = load_json(runtime_file) if runtime_file.exists() else {}
    for key in ("node_executable", "node_modules"):
        if key in runtime:
            require_text(runtime[key], f"本机运行环境/{key}")
    if output.suffix.lower() != ".xlsx":
        raise ValidationError("Excel输出文件必须以 .xlsx 结尾。")
    requested = os.environ.get("QA_XLSX_FALLBACK", "").strip().lower()
    if requested not in ("", "node", "python"):
        raise ValidationError("QA_XLSX_FALLBACK 只能取 node 或 python。")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=output.parent, prefix=".qa-xlsx-", suffix=".xlsx")
    os.close(handle)
    temporary = Path(temp_name)
    payload = dict(payload, output=str(temporary))
    # 默认先走 Node 作者，仅在缺少 @oai/artifact-tool（或本机没有 Node）时回退；
    # QA_XLSX_FALLBACK=python 可显式强制回退，便于在无该依赖的环境验证同一契约。
    try:
        fallback_reason = "QA_XLSX_FALLBACK=python 指定使用回退作者" if requested == "python" else _run_xlsx_node(payload, temporary, runtime)
        if fallback_reason:
            if requested == "node":
                raise ValidationError("QA_XLSX_FALLBACK=node 禁止使用Python回退：" + fallback_reason)
            _run_xlsx_python(payload, temporary)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValidationError("Excel作者未生成有效文件。")
        if payload["operation"] == "review":
            name = payload["sheetName"]
            if read_xlsx(payload["input"], sheet_name=name)["cells"] != read_xlsx(temporary, sheet_name=name)["cells"]:
                raise ValidationError("评审导出改变了原13列的值或公式，已拒绝发布。")
            saved = read_xlsx(temporary, columns=14, sheet_name=name)["rows"]
            if saved[0][13] != "AI评审" or [row[13] for row in saved[1:]] != payload["reviews"]:
                raise ValidationError("导出评审列与评审数据不一致，已拒绝发布。")
        else:
            # sheetName 缺省时交给 read_xlsx 的首选表逻辑（如 "用例"/"草稿用例"/首表），
            # 调用方可以只传 rows；显式指定时按指定表校验。
            expected_sheet = payload.get("sheetName")
            saved = (read_xlsx(temporary, sheet_name=expected_sheet) if expected_sheet
                     else read_xlsx(temporary))
            if len(saved["rows"]) != len(payload["rows"]):
                raise ValidationError("Excel导出行数不正确，已拒绝发布。")
            for expected, actual in zip(payload["rows"], saved["rows"]):
                for wanted, value in zip(expected, actual):
                    # 回退写入器（xlsx_fallback.py）逐字落盘 "=" 开头的文本；Node/@oai
                    # 作者可能把撇号当 quotePrefix 保留，故两种形态都接受。
                    if value != wanted and not (wanted.startswith("=") and value == "'" + wanted):
                        raise ValidationError("Excel导出内容与用例数据不一致，已拒绝发布。")
            if any(formula for _, formula in saved["cells"].values()):
                raise ValidationError("用户文本意外导出为公式，已拒绝发布。")
        if publication_guard is not None:
            publication_guard()
        os.replace(temporary, output)
    except OSError as exc:
        raise ValidationError(f"无法运行Excel作者：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def read_xlsx(path: str | Path, *, columns: int = 13, sheet_name: str | None = None) -> dict:
    """Read one bounded worksheet without authoring Excel.

    ``sheet_name`` selects an exact sheet; otherwise a case-list sheet is
    preferred, then the first sheet. Public-section sheets added by the
    renderer carry no case rows, so "first sheet wins" would silently read the
    wrong table.
    """
    preferred = ("用例", "草稿用例", "Sheet1")
    try:
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = workbook.findall("s:sheets/s:sheet", NS)
            if not sheets:
                raise ValidationError("工作簿没有工作表。")
            names = [sheet.attrib["name"] for sheet in sheets]
            if sheet_name is not None:
                if sheet_name not in names:
                    raise ValidationError(f"工作簿缺少工作表：{sheet_name}。")
                chosen = sheet_name
            else:
                chosen = next((name for name in preferred if name in names), names[0])
            sheet = next(item for item in sheets if item.attrib["name"] == chosen)
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target = next(item.attrib["Target"] for item in relationships if item.attrib["Id"] == rel_id)
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(item.itertext()) for item in shared_root.findall("s:si", NS)]
            root = ET.fromstring(archive.read(target))
            cells, rows = {}, {}
            last_row = 1
            for cell in root.findall("s:sheetData/s:row/s:c", NS):
                address = cell.attrib["r"]
                match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                col = 0
                for char in match.group(1):
                    col = col * 26 + ord(char) - 64
                if col > columns:
                    continue
                row = int(match.group(2))
                last_row = max(last_row, row)
                value = cell.find("s:v", NS)
                value = value.text if value is not None and value.text is not None else ""
                cell_type = cell.attrib.get("t")
                if cell_type == "s":
                    value = shared[int(value)]
                elif cell_type == "inlineStr":
                    # 内联字符串可能没有 <is> 节点（写入方按普通串落盘），此处必须容错：
                    # 早期实现直接 itertext()，遇到该形态会抛 AttributeError。
                    inline = cell.find("s:is", NS)
                    if inline is not None:
                        value = "".join(inline.itertext())
                elif cell_type == "str":
                    value = value or ""
                elif cell_type in ("b", "e"):
                    value = value or ""
                formula = cell.find("s:f", NS)
                formula = formula.text if formula is not None else ""
                if value or formula:
                    cells[address] = (value, formula or "")
                rows.setdefault(row, [""] * columns)[col - 1] = value
            matrix = [rows.get(row, [""] * columns) for row in range(1, last_row + 1)]
            return {"name": sheet.attrib["name"], "cells": cells, "rows": matrix}
    # IndexError：手改过或别的工具生成的工作簿里 t="s" 可能越界引用共享字符串表，
    # 此时应报出可读错误，而不是把 traceback 抛给只拿到外部 xlsx 的用户。
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, StopIteration, ValueError) as exc:
        raise ValidationError(f"无法读取用例Excel {path}: {exc}") from exc


def cli_error(exc: Exception) -> int:
    print(f"错误：{exc}", file=sys.stderr)
    return 1
