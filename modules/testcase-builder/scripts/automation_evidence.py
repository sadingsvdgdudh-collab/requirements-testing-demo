"""Read-only verification of declared framework evidence; never import its code.

evaluate_automation(case, module_dir=None, manifest=None) returns verified/ready,
reasons, evidence_ids and capabilities. Invalid structured evidence raises
ValueError. Legacy prose is accepted but always remains unverified and not ready.
Non-Python symbols are explicitly unavailable for this verifier.
"""

from __future__ import annotations

import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

MANIFEST_NAME = "框架能力清单.json"
AVAILABLE_ADVICES = {"UI自动化可用", "UI自动化可做，需采集新资产", "仅api自动化可用", "双通道均可用"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须为非空文本。")
    return value.strip()


def _relative(value, label):
    value = _text(value, label)
    # Use one portable separator convention; Windows absolute/drive paths must
    # not become apparently relative paths on macOS/Linux.
    if "\\" in value or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute():
        raise ValueError(f"{label} 必须为使用 / 的相对路径。")
    if ".." in PurePosixPath(value).parts or value in (".", ""):
        raise ValueError(f"{label} 禁止路径穿越。")
    return Path(value)


def manifest_path(module_dir: Path | None) -> Path | None:
    if module_dir is None:
        return None
    module_dir = Path(module_dir)
    for base in (module_dir, module_dir.parent, module_dir.parent.parent):
        path = base / MANIFEST_NAME
        if path.is_file():
            return path
    return None


def load_manifest(module_dir: Path | None) -> dict | None:
    path = manifest_path(module_dir)
    if path is None:
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 {MANIFEST_NAME}：{exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{MANIFEST_NAME} 顶层必须为对象。")
    return result


def framework_root(manifest: dict, module_dir: Path | None = None) -> Path:
    declared = _text(manifest.get("framework_root"), "framework_root")
    value = os.environ.get("QA_FRAMEWORK_ROOT") or declared
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _relative(value, "framework_root")
        if module_dir is None:
            raise ValueError("相对 framework_root 需要 module_dir。")
        path = Path(module_dir) / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"自动化框架根目录不存在：{path}")
    return path


def _python_symbols(source: bytes, label: str) -> set[str]:
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise ValueError(f"无法静态核对 Python 函数 {label}：{exc}") from exc
    names = set()

    def declarations(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                declarations(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                declarations(node.body, prefix)
                declarations(getattr(node, "orelse", []), prefix)
                declarations(getattr(node, "finalbody", []), prefix)
                for handler in getattr(node, "handlers", []):
                    declarations(handler.body, prefix)
    declarations(tree.body)
    return names


def validate_manifest(manifest: dict, module_dir: Path | None = None) -> dict:
    """Validate all manifest entries, return a copy with verification metadata."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ValueError(f"{MANIFEST_NAME} schema_version 必须为1.0。")
    _text(manifest.get("reviewer"), "框架能力清单/reviewer")
    checked_at = _text(manifest.get("checked_at"), "框架能力清单/checked_at")
    try:
        datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("框架能力清单/checked_at 必须为 ISO 日期或时间。") from exc
    root = framework_root(manifest, module_dir)
    entries = manifest.get("capabilities")
    if not isinstance(entries, list):
        raise ValueError("框架能力清单/capabilities 必须为数组。")
    result = dict(manifest)
    result["capabilities"] = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        label = f"框架能力清单/第{index}项"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} 必须为对象。")
        capability = dict(entry)
        cap_id = _text(capability.get("id"), label + "/id")
        if cap_id in seen:
            raise ValueError(f"框架能力编号重复：{cap_id}")
        seen.add(cap_id)
        kind = capability.get("kind")
        if kind not in ("function", "asset"):
            raise ValueError(f"{cap_id}/kind 必须为 function 或 asset。")
        relative = _relative(capability.get("file"), cap_id + "/file")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"{cap_id}/file 解析后逃逸框架根目录。")
        if not path.is_file():
            raise ValueError(f"{cap_id} 引用文件不存在：{relative}")
        sha = capability.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            raise ValueError(f"{cap_id}/sha256 必须为完整64位文件摘要。")
        if "notes" not in capability or not isinstance(capability["notes"], str):
            raise ValueError(f"{cap_id}/notes 必须为文本。")
        try:
            source = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"无法读取能力文件 {relative}：{exc}") from exc
        if hashlib.sha256(source).hexdigest() != sha.lower():
            raise ValueError(f"{cap_id} 文件摘要不一致，框架能力证据已变化：{relative}")
        capability["_verified"] = True
        capability["_reason"] = ""
        if kind == "function":
            symbol = _text(capability.get("symbol"), cap_id + "/symbol")
            if path.suffix.lower() == ".py":
                if symbol not in _python_symbols(source, str(relative)):
                    raise ValueError(f"{cap_id} 引用的函数未在文件中定义：{relative}/{symbol}")
            else:
                capability["_verified"] = False
                capability["_reason"] = f"{relative}/{symbol}：当前静态核对器不支持该语言，需核对框架能力。"
        result["capabilities"].append(capability)
    return result


def evaluate_automation(case: dict, module_dir: Path | None = None, manifest: dict | None = None) -> dict:
    """Compute readiness without trusting prose or caller-supplied private flags."""
    explicit = "automation_evidence" in case
    refs = case.get("automation_evidence", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("automation_evidence 必须为框架能力编号数组。")
    if len(refs) != len(set(refs)):
        raise ValueError("automation_evidence 含重复编号。")
    if manifest is None:
        manifest = load_manifest(module_dir)
    selected = []
    reasons = []
    if manifest is not None:
        checked = validate_manifest(manifest, module_dir)
        capabilities = {cap["id"]: cap for cap in checked["capabilities"]}
        unknown = [ref for ref in refs if ref not in capabilities]
        if unknown:
            raise ValueError("automation_evidence 引用了未知框架能力：" + "、".join(unknown))
        selected = [capabilities[ref] for ref in refs]
    elif refs:
        raise ValueError(f"automation_evidence 缺少 {MANIFEST_NAME}，无法核对编号。")
    if not selected:
        reasons.append("未提供可核对的结构化函数/资源证据；自由文本不能证明框架能力。")
    reasons.extend(cap["_reason"] for cap in selected if not cap["_verified"])
    advice = case.get("自动化建议")
    # A project manifest must not turn an unreferenced legacy row into verified
    # evidence or make importing old prose implicitly opt into a new contract.
    opted_in = explicit
    verified_functions = [cap for cap in selected if cap["kind"] == "function" and cap["_verified"]]
    verified_assets = [cap for cap in selected if cap["kind"] == "asset" and cap["_verified"]]
    if opted_in and advice in AVAILABLE_ADVICES:
        if not verified_functions:
            raise ValueError(f"{advice} 必须关联至少一个已核对的函数定义。")
        if advice in ("UI自动化可用", "双通道均可用") and not verified_assets:
            raise ValueError(f"{advice} 必须关联已核对的场景资产文件。")
        if advice == "UI自动化可做，需采集新资产":
            detail = case.get("自动化建议明细", "")
            missing = detail.split("需采集新资产：", 1)[-1] if "需采集新资产：" in detail else ""
            if not re.search(r"(?:[\w.-]+/)[^\s，；;：:]+\.[A-Za-z0-9]{1,8}", missing):
                raise ValueError("需采集新资产必须明确说明缺失场景截图及其存放文件路径。")
    verified = bool(selected) and all(cap["_verified"] for cap in selected)
    ready = verified and advice in (AVAILABLE_ADVICES - {"UI自动化可做，需采集新资产"}) and case.get("是否可自动化") == "是"
    status_reasons = {
        "UI自动化可做，需采集新资产": "缺少场景资产，完成采集与核对后才具备自动化执行准备条件。",
        "当前框架能力不足": "当前框架缺少所需能力，补齐并重新核对后再判断可执行性。",
        "待核对框架能力": "框架能力仍待核对，不能报告自动化已就绪。",
        "不建议自动化": "此用例不建议自动化。",
        "本项目尚无自动化框架": "项目尚无自动化框架，不能报告自动化已就绪。",
    }
    if advice in status_reasons:
        reasons.append(status_reasons[advice])
    if case.get("是否可自动化") != "是" and ready is False and advice in AVAILABLE_ADVICES:
        reasons.append("是否可自动化尚未确认为是。")
    return {"verified": verified, "ready": ready, "reasons": reasons, "evidence_ids": list(refs), "capabilities": selected}
