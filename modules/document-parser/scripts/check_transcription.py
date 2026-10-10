#!/usr/bin/env python3
"""Validate a recorded visual check; never auto-approve an OCR transcription."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testcase-builder" / "scripts"))
from qa_cases import ValidationError, cli_error


def check(module_dir, requirements=None, project_dir=None):
    module = Path(module_dir).resolve()
    project = Path(project_dir).resolve() if project_dir is not None else module.parent.parent
    path = module / "解析来源.json"
    if not path.exists():
        return None  # Historical Markdown inputs follow their existing checks.
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry, sha in ((manifest["original"]["file"], manifest["original"]["sha256"]),
                       (manifest["draft"], manifest["draft_sha256"])):
        source = (project / entry).resolve()
        if not source.is_relative_to(project) or not source.is_file():
            raise ValidationError("解析来源文件越界或不存在。")
        if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
            raise ValidationError("原件或不可变转录底稿已变，必须重新解析并核对。")
    if not manifest.get("needs_review"):
        return manifest
    review_path = module / "解析核对.json"
    if not review_path.is_file():
        raise ValidationError("OCR/复杂结构尚未视觉核对：请对照原件记录解析核对.json，不可直接作为正式需求。")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (review.get("schema_version") != "1.0" or review.get("original_sha256") != manifest["original"]["sha256"]
            or review.get("draft_sha256") != manifest["draft_sha256"]):
        raise ValidationError("解析核对记录版本无效或已过期。")
    if review.get("status") not in ("confirmed", "confirmed_with_issues") or not str(review.get("reviewer", "")).strip() or not str(review.get("notes", "")).strip():
        raise ValidationError("解析核对必须记录核对者、实际核对说明和明确状态。")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise ValidationError("解析核对 findings 必须为数组。")
    reqs = {r["id"]: r for r in (requirements or [])}
    for finding in findings:
        if not isinstance(finding, dict) or not str(finding.get("notes", "")).strip() or finding.get("status") not in ("resolved", "uncertain"):
            raise ValidationError("每个解析问题须记录 resolved/uncertain 及核对说明。")
        if finding["status"] == "uncertain":
            affected = finding.get("affected_requirements")
            if review["status"] != "confirmed_with_issues" or not isinstance(affected, list) or not affected:
                raise ValidationError("未确认转录须明确受影响REQ，并标 confirmed_with_issues。")
            if requirements is not None and any(key not in reqs or reqs[key]["status"] != "blocked" for key in affected):
                raise ValidationError("转录仍不确定的受影响REQ必须存在且 blocked。")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        req_path = args.module_dir / "REQ清单.json"
        reqs = json.loads(req_path.read_text(encoding="utf-8")).get("requirements", []) if req_path.exists() else None
        check(args.module_dir, reqs)
        print("解析来源指纹及核对记录检查通过；记录仍需基于真实视觉核对。")
        return 0
    except (ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
