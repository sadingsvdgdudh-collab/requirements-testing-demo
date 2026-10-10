"""Shared validation and provenance for the optional testpoint stages."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testcase-builder" / "scripts"))
from qa_cases import (METHODS, SCOPES, ValidationError, cli_error, coverage_data,
                      load_json, percent, require_text, steps, unique_list,
                      validate_cases, validate_spec, write_text, require_final)

POINT_FIELDS = ["测试点编号", "优先级", "测试点名称", "前置条件", "检查内容", "测试数据",
                "预期结果", "关联需求", "验收标准", "场景类型", "设计方法"]


def validate_points(data: dict, spec: dict) -> list[dict]:
    if isinstance(data, dict):
        require_final(data, "测试点数据")
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValidationError("测试点数据 schema_version 必须为1.0。")
    code = data.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{2,4}", code):
        raise ValidationError("测试点模块 code 必须为2–4位大写字母。")
    require_text(data.get("module"), "module")
    if data["module"] != spec.get("module") or code != spec.get("code"):
        raise ValidationError("测试点 module/code 与 REQ清单不一致。")
    raw = data.get("testpoints")
    if not isinstance(raw, list):
        raise ValidationError("testpoints 必须为数组。")
    reqs = {req["id"]: req for req in spec["requirements"]}
    ac_owner = {ac["id"]: req["id"] for req in reqs.values() for ac in req["acceptance_criteria"]}
    points, seen = [], set()
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise ValidationError(f"第{index}条测试点必须为对象。")
        require_final(row, f"第{index}条测试点")
        missing = [key for key in POINT_FIELDS if key not in row]
        if missing:
            raise ValidationError(f"第{index}条测试点缺少字段：{'、'.join(missing)}。")
        point = dict(row)
        point_id = require_text(point["测试点编号"], f"第{index}条测试点编号")
        match = re.fullmatch(rf"TP_{re.escape(code)}_([0-9]{{3,}})", point_id)
        if not match or int(match.group(1)) < 1:
            raise ValidationError(f"测试点编号格式错误：{point_id}。")
        if point_id in seen:
            raise ValidationError(f"重复测试点编号：{point_id}。")
        seen.add(point_id)
        for key in ("测试点名称", "前置条件", "检查内容", "测试数据"):
            require_text(point[key], f"{point_id}/{key}")
        if point["优先级"] not in ("P0", "P1", "P2", "P3"):
            raise ValidationError(f"{point_id} 优先级必须为P0/P1/P2/P3。")
        if point["场景类型"] not in SCOPES:
            raise ValidationError(f"{point_id} 场景类型必须为：{'、'.join(SCOPES)}。")
        point["预期结果"] = steps(point["预期结果"], f"{point_id}/预期结果")
        for key in ("关联需求", "验收标准", "设计方法"):
            point[key] = unique_list(point[key], f"{point_id}/{key}")
        if set(point["设计方法"]) - set(METHODS):
            raise ValidationError(f"{point_id} 含未知设计方法。")
        for req_id in point["关联需求"]:
            if req_id not in reqs:
                raise ValidationError(f"{point_id} 关联了未知需求：{req_id}。")
            if reqs[req_id]["status"] != "active":
                raise ValidationError(f"{point_id} 只能关联 active 需求，{req_id} 为{reqs[req_id]['status']}。")
        for ac_id in point["验收标准"]:
            if ac_owner.get(ac_id) not in point["关联需求"]:
                raise ValidationError(f"{point_id} 验收标准 {ac_id} 未属于该测试点关联的需求。")
        points.append(point)
    return points


def load_context(module_dir: Path, input_path: Path | None = None) -> tuple[dict, dict, list[dict]]:
    module_dir = Path(module_dir).resolve()
    spec = validate_spec(module_dir)
    source = Path(input_path).resolve() if input_path is not None else module_dir / "测试点数据.json"
    data = load_json(source)
    return spec, data, validate_points(data, spec)


def point_coverage(spec: dict, points: list[dict]) -> dict:
    data = coverage_data(spec, points)
    data["point_count"] = data.pop("case_count")
    return data


def fingerprints(module_dir: Path, input_path: Path | None = None) -> dict:
    module_dir = Path(module_dir).resolve()
    source = Path(input_path).resolve() if input_path is not None else module_dir / "测试点数据.json"
    try:
        return {"requirements_sha256": hashlib.sha256((module_dir / "REQ清单.json").read_bytes()).hexdigest(),
                "testpoints_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    except OSError as exc:
        raise ValidationError(f"无法读取当前需求/测试点原文件：{exc}") from exc


def load_current_review(module_dir: Path, input_path: Path | None = None,
                        review_path: Path | None = None) -> dict | None:
    """Reject stale or malformed reviews and never trust cached derived scores."""
    module_dir = Path(module_dir).resolve()
    path = Path(review_path).resolve() if review_path is not None else module_dir / "测试点评审结果.json"
    if not path.is_file():
        if review_path is None and not path.exists():
            return None
        raise ValidationError(f"测试点评审结果不存在或不是文件：{path}。")
    spec, _, points = load_context(module_dir, input_path)
    result = load_json(path)
    if result.get("schema_version") != "1.0":
        raise ValidationError("测试点评审结果 schema_version 必须为1.0。")
    if result.get("module") != spec["module"] or result.get("code") != spec["code"]:
        raise ValidationError("测试点评审结果 module/code 与当前需求不一致。")
    if result.get("reviewed_source") != fingerprints(module_dir, input_path):
        raise ValidationError("测试点评审结果已过期：需求或测试点原文件已改变，请重新评审。")
    # Imported here because the review command itself uses the helpers above.
    from review_testpoints import score_point_review, validate_point_review
    reviews, scopes, methods = validate_point_review(result, points)
    coverage = point_coverage(spec, points)
    approved = point_coverage(spec, [point for point in points if reviews[point["测试点编号"]]["结论"] == "通过"])
    summary = score_point_review(approved, reviews, scopes, methods, points)
    for key, expected in (("coverage", coverage), ("approved_coverage", approved), ("summary", summary)):
        if result.get(key) != expected:
            raise ValidationError(f"测试点评审结果 {key} 与当前数据不一致，请重新生成评审结果。")
    return dict(result, reviews=list(reviews.values()), scope_checks=scopes, method_checks=methods,
                coverage=coverage, approved_coverage=approved, summary=summary)


def point_sources(spec: dict, point: dict) -> list[dict]:
    reqs = {req["id"]: req for req in spec["requirements"]}
    return [{"id": req_id, "title": reqs[req_id]["title"], "source": reqs[req_id]["source"]}
            for req_id in point["关联需求"]]


def markdown_text(value) -> str:
    """Keep arbitrary business text inside a Markdown table cell."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def write_json(path: Path, value: dict) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def protect_outputs(module_dir: Path, outputs: list[Path], input_path: Path | None = None,
                    extra_inputs: tuple[Path, ...] = ()) -> None:
    """Derived artifacts must not replace the files whose business data they use."""
    module_dir = Path(module_dir).resolve()
    inputs = {module_dir / "REQ清单.json", module_dir / "需求规范.md",
              (Path(input_path).resolve() if input_path is not None else module_dir / "测试点数据.json")}
    inputs.update(Path(path).resolve() for path in extra_inputs)
    if any(Path(output).resolve() in inputs for output in outputs):
        raise ValidationError("派生输出不可覆盖需求、测试点或评审业务输入。")


def point_coverage_report(data: dict) -> str:
    req, ac = data["requirement_coverage"], data["acceptance_coverage"]
    lines = [f"# {data['module']}测试点关联覆盖率报告", "", f"范围：{data['scope']}。测试点数：{data['point_count']}。", "",
             f"REQ关联：{req['covered']}/{req['total']}，{percent(req['rate'])}。",
             f"验收标准关联：{ac['covered']}/{ac['total']}，{percent(ac['rate'])}。", "",
             "该指标仅检查关联完整性，不能证明测试点正确、场景充分或软件测试通过。", ""]
    messages = {"passed": "关联覆盖检查通过：REQ与验收标准关联均为100%。",
                "failed": "关联覆盖检查未通过。", "not_designable": "不可设计：没有active需求，不能报告100%覆盖率。"}
    lines.append(messages[data["status"]])
    for label, info in (("未覆盖REQ", req), ("未覆盖验收标准", ac)):
        lines.extend(["", label + "：" + ("、".join(info["missing"]) or "无。")])
    for status, label in (("blocked", "阻塞需求"), ("deprecated", "废弃需求")):
        excluded = data["excluded"][status]
        lines.extend(["", f"{label}（{len(excluded)}条，不计入已确认范围分母）："])
        lines.extend([f"- {item['id']} {item['title']}：{item['reason']}" for item in excluded] or ["无。"])
    return "\n".join(lines) + "\n"
