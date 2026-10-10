#!/usr/bin/env python3
"""Re-run the packaged legacy REQ/TP/nine-column import example."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLE = PACKAGE / "examples-v12" / "import-example"
IMPORTER = PACKAGE / "modules" / "compatibility" / "scripts" / "import_material.py"
sys.path.insert(0, str(PACKAGE / "modules" / "testpoints" / "scripts"))
from point_common import validate_points
from qa_cases import validate_cases, validate_spec, write_text


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def invoke(script, *args):
    result = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def latest(module):
    return sorted(path for path in (module / "imports").iterdir() if path.is_dir())[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, help="新的空项目目录；省略时新建演示runs批次。")
    args = parser.parse_args()
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    project = (args.project_dir or EXAMPLE / "runs" / name).resolve()
    try:
        if project.exists() and any(project.iterdir()):
            raise ValueError("复验目录非空；请提供新的空目录，不覆盖已有项目。")
        project.mkdir(parents=True, exist_ok=True)
        module = project / "output" / "商品管理"
        targets = {"requirements": "REQ清单.json", "testpoints": "测试点数据.json", "cases": "用例数据.json"}
        sources = {"requirements": EXAMPLE / "source" / "旧需求.json", "testpoints": EXAMPLE / "source" / "旧测试点.json",
                   "cases": EXAMPLE / "source" / "旧9列用例.csv"}
        mapping = {"requirements": EXAMPLE / "mapping" / "需求映射.json", "testpoints": EXAMPLE / "mapping" / "测试点映射.json",
                   "cases": EXAMPLE / "mapping" / "用例映射.json"}
        hashes = {kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in sources.items()}
        results = []
        for kind in ("requirements", "testpoints", "cases"):
            args_common = ["--input", sources[kind], "--module-dir", module, "--kind", kind, "--code", "IMP"]
            invoke(IMPORTER, *args_common)
            draft_batch = latest(module)
            draft = read(draft_batch / targets[kind])
            assert draft["draft"] is True and draft["missing_fields"], "默认导入必须保留待补草稿。"
            assert not (module / targets[kind]).exists(), "默认导入不得提前发布正式资料。"
            if kind == "requirements":
                assert draft["requirements"][0]["status"] == "", "缺少状态不能自行判为active。"
            if kind == "testpoints":
                assert draft["testpoints"][0]["场景类型"] == "", "FT不能自动等同某种场景类型。"
                assert draft["testpoints"][0]["验收标准"] == ["AC_IMP_001_01"], "完整验收文本（含逗号）须唯一精确匹配。"
            if kind == "cases":
                assert draft["cases"][0]["验收标准"] == [], "九列表不得自动填满AC。"
                assert draft["cases"][0]["是否可自动化"] == "", "九列表缺失的自动化判断不得自行补写。"
            invoke(IMPORTER, *args_common, "--mapping", mapping[kind], "--apply")
            apply_batch = latest(module)
            formal = read(module / targets[kind])
            assert formal["draft"] is False and formal["import_status"] == "applied"
            assert read(apply_batch / targets[kind])["draft"] is True, "发布不得把保留的导入草稿改为正式数据。"
            prior_ids, applied_ids = read(draft_batch / "编号映射.json"), read(apply_batch / "编号映射.json")
            assert all(applied_ids.get(group, {}).get(old) == new for group in ("REQ", "AC", "TP", "TC")
                       for old, new in prior_ids.get(group, {}).items()), "复跑映射不得更换稳定编号。"
            assert hashes[kind] == hashlib.sha256(sources[kind].read_bytes()).hexdigest(), "源文件发生了变化。"
            assert (draft_batch / "input" / sources[kind].name).read_bytes() == sources[kind].read_bytes()
            assert (apply_batch / "input" / sources[kind].name).read_bytes() == sources[kind].read_bytes()
            results.append({"kind": kind, "source": f"source/{sources[kind].name}", "source_sha256": hashes[kind],
                            "preserved_source": str((draft_batch / "input" / sources[kind].name).relative_to(project)),
                            "draft_batch": str(draft_batch.relative_to(project)), "draft_missing_fields": draft["missing_fields"],
                            "applied_batch": str(apply_batch.relative_to(project)), "published": f"output/商品管理/{targets[kind]}"})
        spec = validate_spec(module)
        points = validate_points(read(module / targets["testpoints"]), spec)
        cases = validate_cases(read(module / targets["cases"]), spec)
        assert points[0]["优先级"] == "P3" and cases[0]["优先级"] == "P3"
        assert points[0]["验收标准"] == ["AC_IMP_001_01"] and points[1]["验收标准"] == ["AC_IMP_001_02"]
        assert cases[0]["实际结果"] == "未执行"
        assert cases[0]["关联测试点"] == ["TP_IMP_001", "TP_IMP_002"]
        assert cases[0]["是否可自动化"] == "待定" and cases[0]["自动化脚本"] == ""
        invoke(PACKAGE / "modules" / "testpoints" / "scripts" / "export_testpoints.py", "--module-dir", module)
        invoke(PACKAGE / "modules" / "testcase-builder" / "scripts" / "check_coverage.py", "--module-dir", module)
        coverage = read(module / "覆盖率数据.json")
        assert coverage["status"] == "passed", "本例两条AC的关联覆盖应完整。"
        report = {"schema_version": "1.0", "status": "passed", "basis": "illustrative_existing_structured_material",
                  "requirements": len(spec["requirements"]), "acceptance_criteria": 2, "testpoints": len(points), "cases": len(cases),
                  "priority_p3_preserved": True, "source_files_unchanged": True, "executed_system_tests": False, "runs": results}
        write_text(project / "复验结果.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        lines = ["# 旧资料导入复验报告", "", "结果：通过。1条REQ、2条AC、2条测试点、1条用例的结构与关联已验证。", "",
                 "默认导入的三份草稿均列出缺字段；明确映射后才正式发布。两个测试点分别精确绑定各自AC，九列表草稿未填任何AC。",
                 "测试点与用例的P3均保留。原件指纹与副本一致。实际结果保持“未执行”，没有执行游戏或产品。", "",
                 "## 各批次", ""]
        for item in results:
            lines += [f"- {item['kind']}：草稿 `{item['draft_batch']}`；正式发布批次 `{item['applied_batch']}`。"]
        lines += ["", "资料来源是按旧技能格式编写的示例；没有原始策划案页码，来源记录不认证原始业务真实性。",
                  "字段和关联校验通过不代表评审通过、测试充分或产品测试通过。", ""]
        write_text(project / "复验报告.md", "\n".join(lines))
        print(f"旧资料导入复验通过：{project}")
        return 0
    except (OSError, ValueError, AssertionError) as exc:
        print(f"复验失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
