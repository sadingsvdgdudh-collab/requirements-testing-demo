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
# 1.0：只核对"能力是否真实存在"；1.1：每条能力还必须声明 channel，据此核对通道一致性。
SCHEMAS = {"1.0", "1.1"}
CHANNELS = {"api", "ui", "both"}
UI_ADVICES = {"UI自动化可用", "UI自动化可做，需采集新资产"}
REVERSE_ADVICES = {"当前框架能力不足"}


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


def _capability_clash(detail: str, capabilities: dict) -> list[tuple[str, str]]:
    """在"缺失能力"明细里找出与清单中已核对能力同名的项（反向证据）。

    只做保守的整词匹配：token 归一化成 [a-z0-9_.-]，去掉括号里的参数，
    与能力的 id 或 symbol 末段完全相等才算冲突 —— 宁可漏报，也不要误伤。
    """
    # 先截掉后面的"补齐办法/核对办法"等段落：它们不是能力名，且可能紧贴在同一句里
    # （「缺失能力：Bridge.call。补齐办法：待补。」这种没有顿号的写法曾漏报）。
    for stop in ("补齐办法：", "补齐办法:", "核对办法：", "核对办法:",
                 "待核对能力：", "待核对能力:", "理由：", "理由:"):
        detail = detail.split(stop, 1)[0]
    # 注意分隔符里不能有 "."：能力编号本身带点（bridge.Bridge.call），拆开就认不出来了。
    tokens = re.split(r"[、，,;；。：:！？!?/|\s]+", detail or "")
    normalized = []
    for token in tokens:
        token = token.split("(", 1)[0].strip().strip("`*_（）()【】[]\"'")
        token = token.lower()
        if len(token) < 2 or not re.fullmatch(r"[a-z0-9_.\-]+", token):
            continue
        normalized.append(token)
    if not normalized:
        return []
    index = {}
    for cap in capabilities.values():
        if not cap.get("_verified"):
            continue
        cap_id = cap["id"]
        index.setdefault(cap_id.lower(), cap_id)
        index.setdefault(cap_id.split(".", 1)[-1].lower(), cap_id)
        if cap.get("symbol"):
            index.setdefault(str(cap["symbol"]).split(".")[-1].lower(), cap_id)
    clash = []
    for token in normalized:
        if token in index and (token, index[token]) not in clash:
            clash.append((token, index[token]))
    return clash


def validate_manifest(manifest: dict, module_dir: Path | None = None) -> dict:
    """Validate all manifest entries, return a copy with verification metadata."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in SCHEMAS:
        raise ValueError(f"{MANIFEST_NAME} schema_version 必须为 {' 或 '.join(sorted(SCHEMAS))}。")
    schema = manifest["schema_version"]
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
        if schema == "1.1":
            # 1.1 起通道是能力声明的一部分：没有它就无法核对"仅 api / UI / 双通道"是否成立。
            channel = capability.get("channel")
            if channel not in CHANNELS:
                raise ValueError(f"{cap_id}/channel 必须为 {'/'.join(sorted(CHANNELS))} 之一。")
        else:
            # 1.0 清单没有通道信息，通道类规则不参与判断（不猜测、不默认）。
            capability["channel"] = None
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
    capabilities = {}
    schema = manifest.get("schema_version") if isinstance(manifest, dict) else None
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
        # 通道一致性：只在 1.1 清单（每条能力声明了 channel）上判断。
        # 1.0 清单没有通道信息，这里不猜测——宁可少一条规则，也不要一条会误伤的规则。
        if schema == "1.1":
            channels = {cap["channel"] for cap in verified_functions if cap.get("channel")}

            def _has(*wanted):
                return any(channel == "both" or channel in wanted for channel in channels)

            if advice == "仅api自动化可用" and not _has("api"):
                raise ValueError(f"{advice} 必须关联已核对的 api 通道能力；"
                                 f"当前证据通道为 {sorted(channels) or ['（空）']}。")
            if advice in UI_ADVICES and not _has("ui"):
                raise ValueError(f"{advice} 必须关联已核对的 ui 通道能力；"
                                 f"当前证据通道为 {sorted(channels) or ['（空）']}。")
            if advice == "双通道均可用" and not (_has("api") and _has("ui")):
                raise ValueError(f"{advice} 必须同时关联已核对的 api 与 ui 通道能力；"
                                 f"当前证据通道为 {sorted(channels) or ['（空）']}。")
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
    # 反向证据：声称"当前框架能力不足"时要能被清单反驳。
    # 只说"缺什么"而不检查"是不是真的缺"，等于给了一条最省事的逃生通道（少设计、少测）。
    if advice in REVERSE_ADVICES and capabilities:
        detail = str(case.get("自动化建议明细", ""))
        missing = detail.split("缺失能力：", 1)[-1] if "缺失能力：" in detail else ""
        clash = _capability_clash(missing, capabilities)
        if clash:
            raise ValueError(
                f"{advice} 与能力清单冲突：清单中已存在 " +
                "、".join(f"{token}（{cap_id}）" for token, cap_id in clash[:5]) +
                "；请改判自动化建议，或写明这些现有能力为何不适用。")
    if case.get("是否可自动化") != "是" and ready is False and advice in AVAILABLE_ADVICES:
        reasons.append("是否可自动化尚未确认为是。")
    return {"verified": verified, "ready": ready, "reasons": reasons, "evidence_ids": list(refs), "capabilities": selected}
