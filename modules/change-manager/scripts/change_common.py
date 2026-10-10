"""Small deterministic history, dependency analysis and safe update helpers.

Business interpretation belongs to the model and reviewer. These tools never
rewrite an expectation from a text replacement, or report software test results.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

MODULES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULES / "testpoints" / "scripts"))
from point_common import validate_points
from qa_cases import ValidationError, load_json, validate_cases, validate_spec, write_text

FILES = {"requirements": "REQ清单.json", "testpoints": "测试点数据.json", "cases": "用例数据.json"}
IDS = {"requirements": "id", "acceptance_criteria": "id", "testpoints": "测试点编号", "cases": "用例编号"}
EXECUTION_FIELDS = {"实际结果", "execution_records", "execution_history", "runs", "执行记录", "执行历史", "执行状态", "执行人", "执行时间"}
METADATA_FIELDS = {"draft", "update_state", "update_reasons", "update_resolution", "same_test_intent", "source", "legacy", "import_status", "missing_fields", "update_metadata"}
BUSINESS_FIELDS = {
    "requirements": {"title", "description", "kind", "status", "status_reason"},
    "acceptance_criteria": {"text", "status", "owner"},
    "testpoints": {"优先级", "测试点名称", "前置条件", "检查内容", "测试数据", "预期结果", "关联需求", "验收标准", "场景类型", "设计方法", "status"},
    "cases": {"优先级", "用例名称", "前置条件", "操作步骤", "测试数据", "预期结果", "关联需求", "验收标准", "关联测试点", "场景类型", "设计方法", "是否可自动化", "自动化建议", "自动化建议明细", "自动化脚本", "automation_evidence", "preparation", "status"},
}
DERIVED_NAMES = {"测试点.md", "测试点.xmind", "测试点覆盖矩阵.md", "更新应用报告.md", "执行准备数据.json", "执行准备清单.md", "可读性数据.json", "可读性报告.md"}
# 顶层模块级公共段：不属于某个 case，改动会影响全部用例，故单独登记，不并入 BUSINESS_FIELDS。
MODULE_SHARED_FIELDS = ("module_preconditions", "evidence_setup")
DERIVED_MARKERS = ("评审", "覆盖率", "覆盖矩阵", "关联报告", "关联数据")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]


def current_files(module_dir: Path) -> list[Path]:
    files = []
    for path in module_dir.rglob("*"):
        relative = path.relative_to(module_dir)
        if relative.parts[0] in {"history", "updates", "imports"} or any(part.startswith(".qa-") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValidationError(f"不能快照或更新符号链接：{relative}。请先保存普通文件副本。")
        if path.is_file():
            files.append(path)
    return sorted(files)


def core_fingerprints(module_dir: Path) -> dict:
    names = set(FILES.values()) | {"需求规范.md", "解析.md", "解析来源.json", "解析核对.json"}
    return {str(path.relative_to(module_dir)): digest(path) for path in current_files(module_dir)
            if path.name in names or path.relative_to(module_dir).parts[0] == "images"}


def source_file(req: dict) -> str | None:
    """REQ.source 必须是对象；返回其 file（缺省或非字符串时为 None）。

    手改过的 JSON 会把 source 写成字符串，直接 .get 会抛 AttributeError；
    这里统一取法并给出可定位到 REQ 的错误。
    """
    source = req.get("source")
    if source is None:
        return None
    if not isinstance(source, dict):
        raise ValidationError(f"{req.get('id')} source 必须为对象。")
    name = source.get("file")
    return name if isinstance(name, str) else None


def source_evidence(module_dir: Path, spec: dict, destination: Path | None = None) -> list[dict]:
    result, seen = [], set()
    project = module_dir.parent.parent.resolve()
    for req in spec.get("requirements", []):
        name = source_file(req)
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        entry = {"source": name, "requirements": [row.get("id") for row in spec.get("requirements", []) if source_file(row) == name]}
        referenced_path = module_dir.parent / name
        path = referenced_path.resolve()
        if Path(name).is_absolute() or not path.is_relative_to(project):
            entry["error"] = "来源越出用户项目，未读取。"
        elif not path.is_file() or referenced_path.is_symlink():
            entry["error"] = "来源不存在或不是可快照的普通文件。"
        else:
            entry["sha256"] = digest(path)
            if destination is not None:
                stored = Path("evidence") / (entry["sha256"][:16] + "-" + path.name)
                (destination / stored).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination / stored)
                entry["stored"] = str(stored)
        result.append(entry)
    return result


def create_snapshot(module_dir: Path, label: str = "") -> Path:
    module_dir = module_dir.resolve()
    if not module_dir.is_dir():
        raise ValidationError("模块目录不存在。")
    if label and (not re.fullmatch(r"[\w.\- ]{1,64}", label) or label in {".", ".."}):
        raise ValidationError("快照标签仅允许1–64位文字、数字、空格、点、下划线或短横线，不得包含路径。")
    if (module_dir / "history").is_symlink():
        raise ValidationError("history/不能为符号链接。")
    files = current_files(module_dir)
    if not (module_dir / FILES["requirements"]).is_file():
        raise ValidationError("模块缺少REQ清单.json，不能建立需求历史快照。")
    spec = load_json(module_dir / FILES["requirements"])
    target = module_dir / "history" / (stamp() + ("-" + label.replace(" ", "_") if label else ""))
    target.mkdir(parents=True, exist_ok=False)
    try:
        hashes = {}
        for source in files:
            relative = Path("module") / source.relative_to(module_dir)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            hashes[str(relative)] = digest(destination)
        evidence = source_evidence(module_dir, spec, target)
        for item in evidence:
            if "stored" in item:
                hashes[item["stored"]] = digest(target / item["stored"])
        write_json(target / "snapshot.json", {"schema_version": "1.0", "module": spec.get("module"), "code": spec.get("code"),
                   "label": label, "created_at": datetime.now(timezone.utc).isoformat(), "files": hashes, "evidence": evidence,
                   "note": "文件及已存在的来源副本；不认证历史需求或测试执行结论。"})
    except Exception:
        shutil.rmtree(target)
        raise
    return target


def load_snapshot(path: Path) -> dict:
    path = path.resolve()
    manifest = load_json(path / "snapshot.json")
    hashes = manifest.get("files")
    if manifest.get("schema_version") != "1.0" or not isinstance(hashes, dict):
        raise ValidationError("历史快照清单格式无效。")
    for name, expected in hashes.items():
        file = path / name
        if not isinstance(name, str) or Path(name).is_absolute() or not file.resolve().is_relative_to(path) or file.is_symlink():
            raise ValidationError("历史快照清单包含非法路径。")
        if not file.is_file() or digest(file) != expected:
            raise ValidationError(f"历史快照文件已改变或缺失：{name}。")
    if "module/REQ清单.json" not in hashes:
        raise ValidationError("历史快照缺少REQ清单。")
    return manifest


def load_materials(directory: Path) -> dict:
    result = {}
    for kind, name in FILES.items():
        path = directory / name
        if path.exists():
            data = load_json(path)
            if data.get("schema_version") != "1.0":
                raise ValidationError(f"{name} schema_version 必须为1.0。")
            result[kind] = data
    if "requirements" not in result:
        raise ValidationError("缺少REQ清单.json。")
    return result


def rows(materials: dict, kind: str) -> dict:
    if kind == "acceptance_criteria":
        raw = [dict(ac, owner=req.get("id")) for req in materials.get("requirements", {}).get("requirements", [])
               for ac in req.get("acceptance_criteria", [])]
    else:
        raw = materials.get(kind, {}).get(kind, [])
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValidationError(f"{kind} 必须为对象数组。")
    result = {}
    for row in raw:
        identifier = row.get(IDS[kind])
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValidationError(f"{kind} 条目缺少稳定编号。")
        if identifier in result:
            raise ValidationError(f"{kind} 含重复编号：{identifier}。")
        result[identifier] = row
    return result


def differences(before: dict, after: dict, kind: str) -> list[dict]:
    changes = []
    for identifier in sorted(before.keys() | after.keys()):
        old, new = before.get(identifier), after.get(identifier)
        if old is None:
            changes.append({"id": identifier, "category": "added", "business_change": True})
            continue
        if new is None:
            changes.append({"id": identifier, "category": "deleted", "business_change": True})
            continue
        changed = sorted(key for key in BUSINESS_FIELDS[kind] if old.get(key) != new.get(key))
        if changed:
            category = "deprecated" if old.get("status") != "deprecated" and new.get("status") == "deprecated" else "revised"
            changes.append({"id": identifier, "category": category, "business_change": True, "fields": changed,
                            "before": {key: old.get(key) for key in changed}, "after": {key: new.get(key) for key in changed},
                            "interpretation": "参数/描述/关联差异需模型确认；字符串不同不直接证明业务意图改变。"})
        if old.get("source") != new.get("source"):
            old_source, new_source = old.get("source", {}), new.get("source", {})
            old_content = {key: value for key, value in old_source.items() if key not in {"file", "lines", "page", "pages", "paragraph", "location"}} if isinstance(old_source, dict) else old_source
            new_content = {key: value for key, value in new_source.items() if key not in {"file", "lines", "page", "pages", "paragraph", "location"}} if isinstance(new_source, dict) else new_source
            changes.append({"id": identifier, "category": "source_relocated" if old_content == new_content else "source_evidence_changed",
                            "business_change": False, "evidence_recheck": True, "before": old_source, "after": new_source})
        execution_changed = sorted(key for key in EXECUTION_FIELDS if old.get(key) != new.get(key))
        if execution_changed:
            changes.append({"id": identifier, "category": "execution_record_changed", "business_change": False, "fields": execution_changed})
    return changes


def analyze_materials(old: dict, current: dict, evidence_changes=()) -> dict:
    """Pure stable-ID differences and dependency propagation for two versions."""
    maps = {kind: (rows(old, kind), rows(current, kind)) for kind in IDS}
    change_sets = {kind: differences(*maps[kind], kind) for kind in IDS}
    change_sets["requirements"].extend(evidence_changes)
    changed_reqs = {item["id"] for item in change_sets["requirements"] if item["business_change"] and item["category"] != "added"}
    changed_acs = {item["id"] for item in change_sets["acceptance_criteria"] if item["business_change"] and item["category"] != "added"}
    evidence_reqs = {item["id"] for item in change_sets["requirements"] if item.get("evidence_recheck")}
    changed_points = {item["id"] for item in change_sets["testpoints"] if item["business_change"]}
    changed_cases = {item["id"] for item in change_sets["cases"] if item["business_change"]}
    # 模块级公共段不属于任何单条用例，改动会影响全部用例。此前只登记不使用，
    # 一次真实的环境/取证变更会被报告成"无差异"，重新核对被静默跳过。
    shared_changed = [name for name in MODULE_SHARED_FIELDS
                      if old.get("cases", {}).get(name) != current.get("cases", {}).get(name)]
    if shared_changed:
        change_sets["cases"].append({"id": "（模块级）", "category": "module_shared_changed", "business_change": False,
                                     "evidence_recheck": True, "fields": shared_changed})
    point_impacts, case_impacts = {}, {}
    for identifier in sorted(maps["testpoints"][0].keys() | maps["testpoints"][1].keys()):
        linked = [mapping.get(identifier, {}) for mapping in maps["testpoints"]]
        reasons = (["测试点自身新增、修订或删除"] if identifier in changed_points else [])
        req_links = set(value for row in linked for value in row.get("关联需求", []))
        ac_links = set(value for row in linked for value in row.get("验收标准", []))
        reasons += ["关联REQ变化：" + item for item in sorted(req_links & changed_reqs)]
        reasons += ["关联AC变化：" + item for item in sorted(ac_links & changed_acs)]
        evidence = sorted(req_links & evidence_reqs)
        if reasons or evidence:
            point_impacts[identifier] = {"id": identifier, "update_state": "needs_redesign" if reasons else "needs_evidence_check",
                                         "reasons": reasons + ["需重新核对来源：" + item for item in evidence], "present": identifier in maps["testpoints"][1],
                                         "has_baseline": identifier in maps["testpoints"][0]}
    for identifier in sorted(maps["cases"][0].keys() | maps["cases"][1].keys()):
        linked = [mapping.get(identifier, {}) for mapping in maps["cases"]]
        reasons = (["用例自身新增、修订或删除"] if identifier in changed_cases else [])
        req_links = set(value for row in linked for value in row.get("关联需求", []))
        ac_links = set(value for row in linked for value in row.get("验收标准", []))
        point_links = set(value for row in linked for value in row.get("关联测试点", []))
        reasons += ["关联REQ变化：" + item for item in sorted(req_links & changed_reqs)]
        reasons += ["关联AC变化：" + item for item in sorted(ac_links & changed_acs)]
        reasons += ["经测试点传递：" + item for item in sorted(point_links & point_impacts.keys()) if point_impacts[item]["update_state"] == "needs_redesign"]
        evidence = sorted(req_links & evidence_reqs)
        evidence_points = sorted(item for item in point_links & point_impacts.keys() if point_impacts[item]["update_state"] == "needs_evidence_check")
        if reasons or evidence or evidence_points or shared_changed:
            case_impacts[identifier] = {"id": identifier, "update_state": "needs_redesign" if reasons else "needs_evidence_check",
                                        "reasons": reasons + ["需重新核对来源：" + item for item in evidence] + ["需核对测试点证据：" + item for item in evidence_points]
                                                   + ["模块级公共段变更：" + item for item in shared_changed],
                                        "present": identifier in maps["cases"][1], "has_baseline": identifier in maps["cases"][0]}
    unresolved = [item["id"] for item in change_sets["acceptance_criteria"] if item["category"] == "added"]
    return {"changes": change_sets, "affected_testpoints": list(point_impacts.values()), "affected_cases": list(case_impacts.values()),
            "unaffected_testpoints": sorted(maps["testpoints"][1].keys() - point_impacts.keys()),
            "unaffected_cases": sorted(maps["cases"][1].keys() - case_impacts.keys()), "new_acceptance_criteria_to_cover": unresolved}


def analyze(module_dir: Path, baseline: Path) -> dict:
    module_dir, baseline = module_dir.resolve(), baseline.resolve()
    manifest = load_snapshot(baseline)
    old, current = load_materials(baseline / "module"), load_materials(module_dir)
    if (manifest.get("module"), manifest.get("code")) != (current["requirements"].get("module"), current["requirements"].get("code")):
        raise ValidationError("快照 module/code 与当前模块不一致。")
    previous_sources = {entry["source"]: entry.get("sha256") for entry in manifest.get("evidence", []) if "source" in entry}
    evidence_changes = []
    for evidence in source_evidence(module_dir, current["requirements"]):
        if evidence.get("sha256") != previous_sources.get(evidence["source"]):
            evidence_changes.extend({"id": req_id, "category": "source_file_changed", "business_change": False,
                                     "evidence_recheck": True, "source": evidence["source"], "error": evidence.get("error")}
                                    for req_id in evidence["requirements"])
    return {"schema_version": "1.0", "module": current["requirements"]["module"], "code": current["requirements"]["code"],
            "baseline": str(baseline.relative_to(module_dir)) if baseline.is_relative_to(module_dir) else baseline.name,
            **analyze_materials(old, current, evidence_changes),
            "current_fingerprints": core_fingerprints(module_dir), "analysis_only": True,
            "note": "结构及依赖分析。来源移行只要求核对证据；业务意图、参数含义、测试充分性需模型和人工判断。"}


def render_analysis(data: dict) -> str:
    labels = {"requirements": "REQ", "acceptance_criteria": "AC", "testpoints": "测试点", "cases": "用例"}
    categories = {"added": "新增", "deleted": "删除", "deprecated": "废弃", "revised": "参数/描述/关联修订", "source_relocated": "来源位置调整", "source_evidence_changed": "来源证据修订", "source_file_changed": "来源文件变化", "execution_record_changed": "执行记录变化", "module_shared_changed": "模块级公共段变更（全部用例需重新核对）"}
    lines = [f"# {data['module']}需求变更影响报告", "", data["note"], "", "## 差异", ""]
    for kind, items in data["changes"].items():
        lines += [f"- {labels[kind]} {item['id']}：{categories[item['category']]}" + ("；字段：" + "、".join(item["fields"]) if item.get("fields") else "") for item in items]
    if not any(data["changes"].values()):
        lines.append("未发现结构差异。")
    for key, label in (("affected_testpoints", "受影响测试点"), ("affected_cases", "受影响用例")):
        lines += ["", "## " + label, ""]
        lines += [f"- {item['id']}：{item['update_state']}；" + "；".join(item["reasons"]) for item in data[key]] or ["无。"]
    lines += ["", "未受影响测试点：" + ("、".join(data["unaffected_testpoints"]) or "无"), "未受影响用例：" + ("、".join(data["unaffected_cases"]) or "无"),
              "新增AC需设计并覆盖：" + ("、".join(data["new_acceptance_criteria_to_cover"]) or "无"), "", "本报告没有执行游戏，也没有自动修改任何业务预期。"]
    return "\n".join(lines) + "\n"


def prepare(module_dir: Path, baseline: Path) -> Path:
    module_dir, baseline = module_dir.resolve(), baseline.resolve()
    if not baseline.is_relative_to(module_dir / "history"):
        raise ValidationError("更新基线必须是本模块history/中的快照；外部快照只支持只读差异分析。")
    if (module_dir / "updates").is_symlink():
        raise ValidationError("updates/不能为符号链接。")
    analysis = analyze(module_dir, baseline)
    current, old = load_materials(module_dir), load_materials(baseline / "module")
    target = module_dir / "updates" / stamp()
    target.mkdir(parents=True, exist_ok=False)
    affected = {"testpoints": {item["id"]: item for item in analysis["affected_testpoints"]},
                "cases": {item["id"]: item for item in analysis["affected_cases"]}}
    for kind, data in current.items():
        candidate = copy.deepcopy(data)
        candidate.update(draft=True, update_state="pending", update_metadata={"batch": target.name, "origin": "change-manager"})
        for row in candidate.get(kind, []):
            if kind in affected and row[IDS[kind]] in affected[kind]:
                impact = affected[kind][row[IDS[kind]]]
                row.update(update_state=impact["update_state"], update_reasons=impact["reasons"], update_resolution="")
        write_json(target / FILES[kind], candidate)
    if (module_dir / "需求规范.md").is_file():
        shutil.copy2(module_dir / "需求规范.md", target / "需求规范.md")
    retirements, removed = [], []
    for kind in IDS:
        old_rows, new_rows = rows(old, kind), rows(current, kind)
        for identifier in sorted(old_rows.keys() - new_rows.keys()):
            retirements.append({"kind": kind, "id": identifier, "update_state": "needs_decision", "disposition": "archive", "reason": ""})
            removed.append({"kind": kind, "id": identifier, "record": old_rows[identifier]})
    write_json(target / "移除记录.json", {"schema_version": "1.0", "draft": True, "records": removed})
    write_json(target / "更新计划.json", {"schema_version": "1.0", "draft": True, "module": analysis["module"], "code": analysis["code"],
               "batch": target.name, "update_state": "pending", "base_fingerprints": analysis["current_fingerprints"],
               "base_source_fingerprints": {entry["source"]: entry.get("sha256") for entry in source_evidence(module_dir, current["requirements"])},
               "baseline": str(baseline.relative_to(module_dir)),
               "baseline_manifest_sha256": digest(baseline / "snapshot.json"), "retirement_decisions": retirements,
               "affected_items": affected, "new_acceptance_criteria_to_cover": analysis["new_acceptance_criteria_to_cover"],
               "removed_records_sha256": digest(target / "移除记录.json")})
    write_json(target / "变更影响.json", analysis)
    write_text(target / "变更影响报告.md", render_analysis(analysis))
    write_text(target / "更新说明.md", "# 待完成的更新草稿\n\n所有候选JSON为draft=true，不能直接发布。按最新需求重新设计needs_redesign条目，核对needs_evidence_check的原文依据；保留稳定ID和执行记录。\n\n完成每个受影响条目后设update_state=resolved，填写update_resolution；重新设计仍保留原编号时声明same_test_intent=true（同一测试意图）。身份改变应新增编号并在更新计划中明确旧编号归档。\n\n删除项在更新计划retirement_decisions填写reason并设update_state=resolved。完成后将更新计划及三个候选JSON顶层draft=false、update_state=resolved，再执行apply_update；移除记录是不可变档案，不需要去掉draft。\n\n应用会再次校验结构、原文及REQ/AC/TP归属，拒绝未处理项或过期草稿；旧Excel、XMind和评审会归档，必须重新生成。结构通过不代表已完成业务评审或实际执行。\n")
    return target


def require_resolved(data: dict, label: str) -> None:
    if data.get("draft") is not False or data.get("update_state") != "resolved":
        raise ValidationError(f"{label}仍为草稿或未完成，必须显式draft=false且update_state=resolved。")
    for key in ("needs_redesign", "missing_fields"):
        if data.get(key):
            raise ValidationError(f"{label}仍有未处理的{key}。")


def check_item_resolution(data: dict, kind: str, affected: dict) -> None:
    for row in data.get(kind, []):
        identifier = row[IDS[kind]]
        state = row.get("update_state")
        if state is not None and state != "resolved":
            raise ValidationError(f"{identifier}仍需处理：{state}。")
        if row.get("draft") or row.get("missing_fields") or row.get("needs_redesign"):
            raise ValidationError(f"{identifier}含未完成草稿标记。")
        impact = affected.get(identifier)
        if impact:
            if state != "resolved" or not isinstance(row.get("update_resolution"), str) or not row["update_resolution"].strip():
                raise ValidationError(f"{identifier}缺少明确的更新/证据核对说明。")
            if impact["update_state"] == "needs_redesign" and impact.get("has_baseline", True) and row.get("same_test_intent") is not True:
                raise ValidationError(f"{identifier}保留旧编号时必须声明same_test_intent=true；另一测试意图须新增编号并归档旧项。")


def derived(path: Path) -> bool:
    return path.suffix.lower() in {".xlsx", ".xmind"} or path.name in DERIVED_NAMES or any(marker in path.name for marker in DERIVED_MARKERS)


def apply(module_dir: Path, draft_dir: Path) -> Path:
    module_dir, draft_dir = module_dir.resolve(), draft_dir.resolve()
    updates = module_dir / "updates"
    if draft_dir == updates or not draft_dir.is_relative_to(updates) or not draft_dir.is_dir():
        raise ValidationError("draft-dir必须为本模块updates/中的具体更新批次。")
    if any(path.is_symlink() for path in draft_dir.rglob("*")):
        raise ValidationError("更新批次包含符号链接，不能应用。")
    plan = load_json(draft_dir / "更新计划.json")
    require_resolved(plan, "更新计划")
    if plan.get("batch") != draft_dir.name:
        raise ValidationError("更新批次标识与目录不一致。")
    baseline_name = plan.get("baseline")
    if not isinstance(baseline_name, str) or Path(baseline_name).is_absolute():
        raise ValidationError("更新计划缺少合法基线。")
    baseline = (module_dir / baseline_name).resolve()
    if not baseline.is_relative_to(module_dir / "history"):
        raise ValidationError("更新计划基线越出本模块history/。")
    load_snapshot(baseline)
    if digest(baseline / "snapshot.json") != plan.get("baseline_manifest_sha256"):
        raise ValidationError("基线快照清单已改变，请重新准备更新。")
    if plan.get("base_fingerprints") != core_fingerprints(module_dir):
        raise ValidationError("更新草稿已过期：当前需求、测试点、用例或证据资产改变，请重新prepare_update。")
    current = load_materials(module_dir)
    source_hashes = {entry["source"]: entry.get("sha256") for entry in source_evidence(module_dir, current["requirements"])}
    if plan.get("base_source_fingerprints") != source_hashes:
        raise ValidationError("更新草稿已过期：原文来源文件改变，请重新核对并准备更新。")
    candidate = load_materials(draft_dir)
    identity = (current["requirements"].get("module"), current["requirements"].get("code"))
    if (plan.get("module"), plan.get("code")) != identity or (candidate["requirements"].get("module"), candidate["requirements"].get("code")) != identity:
        raise ValidationError("更新计划或候选资料的module/code与当前模块不一致。")
    for sibling in module_dir.parent.glob("*/REQ清单.json"):
        if sibling.parent.resolve() == module_dir:
            continue
        try:
            other = load_json(sibling)
        except ValidationError:
            # 半写坏或旧格式的邻居只影响它自己，不能锁死本模块的发布。
            continue
        if other.get("code") == identity[1]:
            raise ValidationError(f"跨模块code重复：{identity[1]}（{sibling.parent.name}）。")
    analysis = analyze(module_dir, baseline)
    expected_affected = {"testpoints": {row["id"]: row for row in analysis["affected_testpoints"]},
                         "cases": {row["id"]: row for row in analysis["affected_cases"]}}
    if plan.get("affected_items") != expected_affected or plan.get("new_acceptance_criteria_to_cover") != analysis["new_acceptance_criteria_to_cover"]:
        raise ValidationError("更新计划的受影响项与基线重新分析不一致，不得绕过待处理标记。")
    # Edits made inside the draft can change REQ/AC or a point after prepare.
    # Propagate those new dependencies too; checking only TP/TC own fields would
    # otherwise publish unchanged cases against newly revised requirements.
    candidate_analysis = analyze_materials(current, candidate)
    for kind in ("testpoints", "cases"):
        for impact in candidate_analysis["affected_" + kind]:
            previous = expected_affected[kind].get(impact["id"])
            if previous is None:
                expected_affected[kind][impact["id"]] = impact
            elif impact["update_state"] == "needs_redesign":
                previous["update_state"] = "needs_redesign"
                previous["has_baseline"] = previous.get("has_baseline", False) or impact["has_baseline"]
    for kind, data in candidate.items():
        require_resolved(data, FILES[kind])
        rows(candidate, kind)
        check_item_resolution(data, kind, expected_affected.get(kind, {}))
    if set(current) - set(candidate):
        raise ValidationError("候选资料缺少当前已有的数据文件，不能静默删除整份测试点或用例。")
    removed_file = draft_dir / "移除记录.json"
    if digest(removed_file) != plan.get("removed_records_sha256"):
        raise ValidationError("移除记录已改变，不能丢失旧项业务内容或执行记录。")
    removed = load_json(removed_file).get("records", [])
    required_removals = {(row["kind"], row["id"]) for row in removed}
    for kind in IDS:
        required_removals |= {(kind, identifier) for identifier in rows(current, kind).keys() - rows(candidate, kind).keys()}
    decisions = plan.get("retirement_decisions")
    if not isinstance(decisions, list) or any(not isinstance(item, dict) for item in decisions):
        raise ValidationError("retirement_decisions必须为数组。")
    decision_keys = [(item.get("kind"), item.get("id")) for item in decisions]
    if len(decision_keys) != len(set(decision_keys)) or set(decision_keys) != required_removals:
        raise ValidationError("所有删除项必须逐项有唯一的retirement_decisions，不能静默丢弃旧编号。")
    for item in decisions:
        if item.get("update_state") != "resolved" or item.get("disposition") != "archive" or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValidationError(f"删除项{item.get('id')}需明确归档原因并完成决策。")
    for identifier, original in rows(current, "cases").items():
        proposed = rows(candidate, "cases").get(identifier)
        if proposed is not None:
            for field in EXECUTION_FIELDS:
                if field in original and proposed.get(field) != original[field]:
                    raise ValidationError(f"{identifier}的执行记录{field}不可随设计更新删除或改写。")
    # Validate in an isolated stage. Rebase source paths only for validation;
    # published JSON retains the original project-relative evidence paths.
    with tempfile.TemporaryDirectory(prefix=".qa-change-stage-", dir=module_dir.parent) as temp:
        stage = Path(temp) / module_dir.name
        stage.mkdir()
        for name in ("需求规范.md", "解析.md", "解析来源.json", "解析核对.json"):
            source = draft_dir / name if (draft_dir / name).is_file() else module_dir / name
            if source.is_file():
                shutil.copy2(source, stage / name)
        if (module_dir / "images").is_dir():
            shutil.copytree(module_dir / "images", stage / "images")
        staged_spec = copy.deepcopy(candidate["requirements"])
        for req in staged_spec.get("requirements", []):
            name = source_file(req)
            if not isinstance(name, str) or Path(name).is_absolute():
                raise ValidationError(f"{req.get('id')}来源必须为项目相对路径。")
            referenced_source = module_dir.parent / name
            real_source = referenced_source.resolve()
            if not real_source.is_relative_to(module_dir.parent.parent.resolve()) or referenced_source.is_symlink():
                raise ValidationError(f"{req.get('id')}来源越出项目或为符号链接。")
            req["source"]["file"] = os.path.relpath(real_source, stage.parent)
        write_json(stage / FILES["requirements"], staged_spec)
        spec = validate_spec(stage, project_dir=module_dir.parent.parent)
        points = validate_points(candidate["testpoints"], spec) if "testpoints" in candidate else []
        cases = validate_cases(candidate["cases"], spec, module_dir=module_dir) if "cases" in candidate else []
        point_map = {row["测试点编号"]: row for row in points}
        for case in cases:
            links = case.get("关联测试点", [])
            if points and (not isinstance(links, list) or not links or any(not isinstance(item, str) for item in links) or len(links) != len(set(links))):
                raise ValidationError(f"{case['用例编号']}存在测试点时必须明确且唯一地关联测试点。")
            if links and not points:
                raise ValidationError(f"{case['用例编号']}关联测试点但候选测试点不存在。")
            for identifier in links:
                point = point_map.get(identifier)
                if point is None or set(point["关联需求"]) - set(case["关联需求"]) or set(point["验收标准"]) - set(case["验收标准"]):
                    raise ValidationError(f"{case['用例编号']}的测试点{identifier}不存在或REQ/AC归属不完整。")
        new_acs = set(plan.get("new_acceptance_criteria_to_cover", [])) | set(candidate_analysis["new_acceptance_criteria_to_cover"])
        active_acs = {ac["id"] for req in spec["requirements"] if req["status"] == "active" for ac in req["acceptance_criteria"]}
        new_acs &= active_acs
        for label, items in (("测试点", points), ("用例", cases)):
            if label == "测试点" and "testpoints" not in candidate or label == "用例" and "cases" not in candidate:
                continue
            missing = new_acs - {identifier for row in items for identifier in row["验收标准"]}
            if missing:
                raise ValidationError(f"新增验收标准尚缺{label}覆盖：{'、'.join(sorted(missing))}。")
    # No writes to current data happened before this complete validation.
    if plan["base_fingerprints"] != core_fingerprints(module_dir) or source_hashes != {entry["source"]: entry.get("sha256") for entry in source_evidence(module_dir, current["requirements"])}:
        raise ValidationError("校验期间当前资料已改变，请重新准备更新。")
    backup = create_snapshot(module_dir, "before-update")
    archive = backup / "stale-derived"
    replacements = {FILES[kind]: draft_dir / FILES[kind] for kind in candidate}
    for name in ("需求规范.md", "解析.md", "解析来源.json", "解析核对.json"):
        if (draft_dir / name).is_file():
            # Publish the exact transcription/review files used by the stage,
            # not just its REQ, so current evidence cannot lag behind validation.
            replacements[name] = draft_dir / name
    stale = [path for path in current_files(module_dir) if derived(path) and path.name not in replacements]
    originals = {name: (module_dir / name).exists() for name in replacements}
    moved = []
    try:
        for path in stale:
            target = archive / path.relative_to(module_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            moved.append((path, target))
        for name, source in replacements.items():
            write_text(module_dir / name, source.read_text(encoding="utf-8"))
        write_json(backup / "removed-records.json", {"schema_version": "1.0", "records": removed, "decisions": decisions})
        report = "# 更新应用报告\n\n候选设计资料已通过结构、原文和关联校验并发布。尚未进行业务评审或实际执行。\n\n备份：" + str(backup.relative_to(module_dir)) + "\n\n以下派生产物已过期并归档，必须重新生成：\n\n" + "\n".join("- " + str(path.relative_to(module_dir)) for path, _ in moved) + "\n"
        write_text(module_dir / "更新应用报告.md", report)
        write_json(draft_dir / "应用结果.json", {"schema_version": "1.0", "applied": True, "backup": str(backup.relative_to(module_dir)), "archived": [str(path.relative_to(module_dir)) for path, _ in moved]})
    except Exception as exc:
        rollback_errors = []
        for transient in (module_dir / "更新应用报告.md", draft_dir / "应用结果.json"):
            try:
                transient.unlink(missing_ok=True)
            except OSError as error:
                rollback_errors.append(f"{transient.name}: {error}")
        for name, existed in originals.items():
            original = backup / "module" / name
            try:
                if existed:
                    shutil.copy2(original, module_dir / name)
                else:
                    (module_dir / name).unlink(missing_ok=True)
            except OSError as error:
                rollback_errors.append(f"{name}: {error}")
        for path, target in reversed(moved):
            if target.exists():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, path)
                except OSError as error:
                    rollback_errors.append(f"{path.name}: {error}")
        if rollback_errors:
            raise ValidationError(f"发布失败且部分自动恢复失败；完整备份保留在history/{backup.name}，请先恢复再继续：{'; '.join(rollback_errors)}；原错误：{exc}") from exc
        raise ValidationError(f"发布失败，已恢复原资料；备份保留在history/{backup.name}：{exc}") from exc
    return backup


def cli_error(exc: Exception) -> int:
    print(f"错误：{exc}", file=sys.stderr)
    return 1
