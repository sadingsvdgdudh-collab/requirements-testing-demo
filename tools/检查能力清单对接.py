#!/usr/bin/env python3
"""核对「自动化建议 ↔ 框架能力清单」是否真的对得上 —— 跨框架与技能的一次性检查。

做三件事（只读，不修改任何文件，除非显式 --copy-to）：
    1) 让框架自己重算能力清单并比对（跑框架的 scripts/capabilities_manifest.py --check），
       确认清单不是手工抄来的过期产物；
    2) 按清单核对每个模块用例里的 automation_evidence：编号是否存在、文件摘要是否一致、
       函数是否真在该文件里、**建议与所引能力的通道是否一致**；
    3) 反查"当前框架能力不足"：清单里已有的能力不能被声称缺失。

用法：
    python3 检查能力清单对接.py --framework <框架根目录> [--module <模块目录>]...
    python3 检查能力清单对接.py --framework <框架根> --scan <项目根>       # 递归找模块
    python3 检查能力清单对接.py --framework <框架根> --copy-to <项目根>    # 顺手放好清单
    python3 检查能力清单对接.py --manifest <清单.json> --module <模块目录> # 只查技能侧

退出码：0 全部通过；1 有对不上的项；2 用法/环境错误。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "modules" / "testcase-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))

MANIFEST_NAME = "框架能力清单.json"
CASES_NAME = "用例数据.json"


def load_module_api():
    try:
        from automation_evidence import evaluate_automation, validate_manifest, load_manifest  # noqa
    except ImportError as exc:
        sys.exit(f"❌ 无法导入技能脚本（{SCRIPTS}）：{exc}")
    return evaluate_automation, validate_manifest, load_manifest


def find_generator(framework: Path) -> Path | None:
    for candidate in (framework / "scripts" / "capabilities_manifest.py",
                      framework / "scripts" / "capabilities.py"):
        if candidate.is_file():
            return candidate
    return None


def check_generator(framework: Path) -> tuple[bool, str]:
    """让框架自己重算清单；--check 不通过说明清单已过期。"""
    generator = find_generator(framework)
    if generator is None:
        return False, f"{framework}/scripts 下没有 capabilities_manifest.py"
    python = os.environ.get("QA_PYTHON") or sys.executable
    result = subprocess.run([python, str(generator), "--check"], cwd=framework,
                            capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip().splitlines()
    message = output[-1] if output else f"退出码 {result.returncode}"
    return result.returncode == 0, message


def module_dirs(root: Path) -> list[Path]:
    """项目根下所有含 用例数据.json 的目录。"""
    return sorted({path.parent for path in root.rglob(CASES_NAME) if ".git" not in path.parts})


def check_module(module: Path, framework: Path | None, evaluate, validate, load) -> list[str]:
    problems = []
    manifest_file = module / MANIFEST_NAME
    if not manifest_file.is_file():
        for base in (module.parent, module.parent.parent):
            if (base / MANIFEST_NAME).is_file():
                manifest_file = base / MANIFEST_NAME
                break
    if not manifest_file.is_file():
        return [f"{module}：找不到 {MANIFEST_NAME}（技能无法核对，自动化就绪一律不成立）"]
    manifest = load(module)
    if manifest is None:
        return [f"{module}：读不到 {MANIFEST_NAME}"]
    if framework is not None:
        os.environ.setdefault("QA_FRAMEWORK_ROOT", str(framework))
    try:
        checked = validate(manifest, module)
    except ValueError as exc:
        return [f"{module}：清单本身不合法 —— {exc}"]
    known = {cap["id"] for cap in checked["capabilities"]}
    schema = checked.get("schema_version")
    data = json.loads((module / CASES_NAME).read_text(encoding="utf-8"))
    tally = {}
    for case in data.get("cases", []):
        case_id = case.get("用例编号", "?")
        advice = case.get("自动化建议", "")
        tally[advice] = tally.get(advice, 0) + 1
        try:
            result = evaluate(case, module_dir=module, manifest=manifest)
        except ValueError as exc:
            problems.append(f"{module.name}/{case_id}：{exc}")
            continue
        for ref in result["evidence_ids"]:
            if ref not in known:
                problems.append(f"{module.name}/{case_id}：引用了清单里没有的能力 {ref}")
        if advice in {"UI自动化可用", "UI自动化可做，需采集新资产", "仅api自动化可用",
                      "双通道均可用"} and not result["ready"]:
            problems.append(f"{module.name}/{case_id}：{advice} 但未就绪 —— "
                            + "；".join(result["reasons"]) or "无原因说明")
    print(f"  {module}")
    print(f"    清单 {manifest_file.name}（schema {schema}，能力 {len(known)} 条）"
          f" | 用例 {len(data.get('cases', []))} 条")
    if tally:
        print("    自动化建议分布：" + "、".join(f"{k or '（空）'}×{v}" for k, v in sorted(tally.items())))
    return problems


def main():
    parser = argparse.ArgumentParser(description="核对自动化建议与框架能力清单是否对得上")
    parser.add_argument("--framework", help="框架根目录（含 scripts/capabilities_manifest.py）")
    parser.add_argument("--manifest", help="直接指定清单文件（只查技能侧时用）")
    parser.add_argument("--module", action="append", default=[], help="模块目录，可重复")
    parser.add_argument("--scan", help="项目根目录：递归找所有含 用例数据.json 的模块")
    parser.add_argument("--copy-to", help="确认无误后，把框架生成的清单复制到该目录")
    parser.add_argument("--skip-framework-check", action="store_true",
                        help="跳过“框架清单是否过期”这一步（框架不在本机时用）")
    args = parser.parse_args()

    evaluate, validate, load = load_module_api()
    framework = Path(args.framework).expanduser().resolve() if args.framework else None
    if framework is None and args.manifest:
        manifest_data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        value = os.environ.get("QA_FRAMEWORK_ROOT") or manifest_data.get("framework_root")
        framework = Path(value).expanduser().resolve() if value else None

    problems = []
    if framework is not None and not args.skip_framework_check and args.framework:
        print("① 框架侧：清单是否与代码一致")
        ok, message = check_generator(framework)
        print(f"  {'✅' if ok else '❌'} {message}")
        if not ok:
            problems.append(f"框架能力清单过期：{message}")
    elif args.framework:
        print("① 框架侧：已按 --skip-framework-check 跳过")

    generated = framework / "docs" / MANIFEST_NAME if framework else None
    if args.copy_to:
        target = Path(args.copy_to).expanduser().resolve()
        if not generated or not generated.is_file():
            sys.exit(f"❌ 没有可复制的清单：{generated}")
        target.mkdir(parents=True, exist_ok=True)
        (target / MANIFEST_NAME).write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"② 已把框架生成的清单复制到 {target}")

    modules = [Path(item).expanduser().resolve() for item in args.module]
    if args.scan:
        modules += module_dirs(Path(args.scan).expanduser().resolve())
    modules = sorted(set(modules))
    if not modules:
        print("③ 未指定模块（--module / --scan），只做了框架侧检查")
    else:
        print(f"③ 技能侧：{len(modules)} 个模块的建议与清单核对")
        for module in modules:
            problems += check_module(module, framework, evaluate, validate, load)

    print()
    if problems:
        print(f"❌ 发现 {len(problems)} 处对不上：")
        for item in problems:
            print(f"  · {item}")
        return 1
    print("✅ 未发现“建议与清单对不上”的项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
