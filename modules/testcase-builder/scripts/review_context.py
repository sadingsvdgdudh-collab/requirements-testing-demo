"""Read-only binding between case review judgments and their current evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath

from qa_cases import ValidationError, load_json, module_shared_lines

_EXECUTION_FIELDS = {"实际结果", "自动化脚本", "execution_records", "execution_history", "runs",
                     "执行记录", "执行历史", "执行状态", "执行人", "执行时间"}
_SET_FIELDS = {"关联需求", "验收标准", "设计方法", "affected_requirements",
               "automation_evidence", "images"}
_ID_FIELDS = ("id", "用例编号", "测试点编号", "path")


def _canonical(value, key: str = ""):
    if isinstance(value, dict):
        return {name: _canonical(item, name) for name, item in value.items()
                if not name.startswith("_") and name not in _EXECUTION_FIELDS}
    if isinstance(value, list):
        rows = [_canonical(item) for item in value]
        for identifier in _ID_FIELDS:
            if rows and all(isinstance(row, dict) and isinstance(row.get(identifier), str)
                            for row in rows):
                return sorted(rows, key=lambda row: row[identifier])
        if key in _SET_FIELDS and all(isinstance(row, str) for row in rows):
            return sorted(rows)
        return rows
    return value


def _digest(value) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _relative_file(base: Path, value, boundary: Path, label: str) -> Path:
    if (not isinstance(value, str) or not value.strip() or Path(value).is_absolute()
            or PureWindowsPath(value).drive or "\\" in value):
        raise ValidationError(f"{label} 必须使用可核验的本地相对文件路径。")
    path = (base / value).resolve()
    if not path.is_relative_to(boundary.resolve()) or not path.is_file():
        raise ValidationError(f"{label} 文件不存在或路径越界：{value}。")
    return path


def provenance_files(spec: dict, module_dir: Path) -> set[Path]:
    """Use the contract's real paths: source.file is relative to output/, not module/."""
    module = Path(module_dir).resolve()
    output_root, project = module.parent, module.parent.parent
    paths = set()
    for req in spec.get("requirements", []):
        # source 被手改成字符串时不能在这里抛 AttributeError。
        source = req.get("source") if isinstance(req.get("source"), dict) else {}
        paths.add(_relative_file(output_root, source.get("file"), project,
                                 f"{req.get('id', 'REQ')}/source.file"))
        for image in source.get("images", []):
            paths.add(_relative_file(module, image, module, "source.images"))
    for image in spec.get("images", []):
        paths.add(_relative_file(module, image.get("path"), module, "images.path"))
    for name in ("需求规范.md", "解析.md", "解析来源.json", "解析核对.json"):
        path = module / name
        if path.exists():
            paths.add(_relative_file(module, name, module, name))
    manifest_path = module / "解析来源.json"
    if manifest_path.is_file():
        manifest = load_json(manifest_path)
        original = manifest.get("original")
        if not isinstance(original, dict):
            raise ValidationError("解析来源 original 必须为对象。")
        paths.add(_relative_file(project, original.get("file"), project, "解析来源/original.file"))
        paths.add(_relative_file(project, manifest.get("draft"), project, "解析来源/draft"))
    return paths


def _file_records(paths: set[Path], base: Path, *, canonical_json: bool = False) -> list[dict]:
    records = []
    for path in sorted(paths):
        try:
            sha = _digest(load_json(path)) if canonical_json and path.suffix == ".json" else hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValidationError(f"无法读取评审证据 {path.name}：{exc}") from exc
        records.append({"file": path.relative_to(base).as_posix(), "sha256": sha})
    return records


def _framework_context(cases: list[dict], module_dir: Path) -> tuple[dict, set[Path]]:
    # Lazy import avoids introducing a dependency for pure in-memory review checks.
    from automation_evidence import framework_root, load_manifest, manifest_path

    module = Path(module_dir).resolve()
    try:
        manifest = load_manifest(module)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    path = manifest_path(module)
    wanted = {item for case in cases for item in case.get("automation_evidence", [])}
    if manifest is None:
        if wanted:
            raise ValidationError("用例引用了自动化证据，但缺少框架能力清单。")
        return {"manifest": None, "referenced_files": []}, set()
    try:
        root = framework_root(manifest, module).resolve()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, dict) for item in capabilities):
        raise ValidationError("框架能力清单 capabilities 必须为对象数组。")
    lookup = {item.get("id"): item for item in capabilities}
    missing = wanted - set(lookup)
    if missing:
        raise ValidationError("用例引用不存在的框架能力：" + "、".join(sorted(missing)) + "。")
    files = {_relative_file(root, lookup[key].get("file"), root, f"框架能力/{key}/file")
             for key in wanted}
    result = {"manifest": _canonical(manifest), "referenced_files": _file_records(files, root)}
    return result, files | ({path.resolve()} if path is not None else set())


def review_fingerprints(spec: dict, cases: list[dict] | dict,
                        module_dir: Path | None = None) -> dict:
    """Fingerprint design, source state and actual capability files; never change input."""
    if isinstance(cases, dict):
        case_data, rows = cases, cases.get("cases", [])
        shared = module_shared_lines(cases)
    else:
        rows = cases
        case_data = {"cases": rows}
        shared = (module_shared_lines(load_json(Path(module_dir).resolve() / "用例数据.json"))
                  if module_dir is not None else {})
    result = {"requirements_sha256": _digest(spec), "cases_sha256": _digest(case_data)}
    # List callers have already separated rows from the public design setup.
    if any(shared.values()):
        result["module_setup_sha256"] = _digest(shared)
    if module_dir is not None:
        module = Path(module_dir).resolve()
        files = provenance_files(spec, module)
        result["provenance_sha256"] = _digest(_file_records(files, module.parent.parent, canonical_json=True))
        context, _ = _framework_context(rows, module)
        result["framework_evidence_sha256"] = _digest(context)
    return result


def validate_review_context(review: dict, spec: dict, cases: list[dict] | dict,
                            module_dir: Path | None = None) -> dict:
    """Completed legacy reviews must be reviewed again, never silently stamped."""
    if not isinstance(review.get("reviewed_source"), dict):
        raise ValidationError("用例评审缺少 reviewed_source；须对当前需求与用例重新评审，不能给旧结论补签指纹。")
    expected = review_fingerprints(spec, cases, module_dir)
    if review["reviewed_source"] != expected:
        raise ValidationError("用例评审已过期：需求、用例设计、来源或框架证据已改变，请重新评审当前内容。")
    return expected


def build_review_template(spec: dict, cases: list[dict] | dict,
                          module_dir: Path | None = None) -> dict:
    """Begin a new review; the binding alone is never evidence of a completed review."""
    return {"schema_version": "1.0", "module": spec.get("module"), "code": spec.get("code"),
            "draft": True, "reviewed_source": review_fingerprints(spec, cases, module_dir),
            "reviewer": "", "reviews": [], "scope_checks": [], "method_checks": []}


def protected_inputs(spec: dict, module_dir: Path) -> set[Path]:
    module = Path(module_dir).resolve()
    paths = provenance_files(spec, module)
    paths.update((module / name).resolve() for name in
                 ("REQ清单.json", "用例数据.json", "需求规范.md", "详细用例.xlsx"))
    # The manifest itself is input, even when no case currently references it.
    for base in (module, module.parent, module.parent.parent):
        candidate = base / "框架能力清单.json"
        if candidate.exists():
            paths.add(candidate.resolve())
            break
    from automation_evidence import load_manifest, framework_root
    try:
        manifest = load_manifest(module)
        if manifest is not None:
            root = framework_root(manifest, module)
            for item in manifest.get('capabilities', []):
                paths.add(_relative_file(root, item.get('file'), root, '框架能力/file'))
    except (ValueError, OSError) as exc:
        raise ValidationError(str(exc)) from exc
    return paths
