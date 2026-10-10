#!/usr/bin/env python3
"""Run the small real-file change example, with optional Artifact Tool Excel."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "modules" / "change-manager" / "scripts"))
import change_common as changes


def command(module: Path, group: str, script: str, *arguments, environment=None):
    result = subprocess.run([sys.executable, str(PACKAGE / "modules" / group / "scripts" / script), "--module-dir", str(module), *map(str, arguments)],
                            capture_output=True, text=True, timeout=120, env=environment)
    if result.returncode:
        raise changes.ValidationError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def update_requirements(module: Path, project: Path):
    """An explicit model-authored example revision, not a generic text rewrite."""
    data = changes.load_json(module / "REQ清单.json")
    rule = data["requirements"][0]
    rule["description"] = "英文字母名称最多30个字符，合法上限允许保存，超过上限显示指定提示且不创建商品。"
    rule["acceptance_criteria"][0]["text"] = "其他字段合法时，30个英文字母的名称可以保存商品。"
    rule["acceptance_criteria"][1]["text"] = "31个或更多英文字母的名称提示‘商品名称最多30个字符’，且不创建商品。"
    source_lines = (project / "input" / "商品名称_v2.md").read_text(encoding="utf-8").splitlines()
    for req, number in zip(data["requirements"], (5, 7)):
        req["source"] = {"file": "../input/商品名称_v2.md", "lines": [number, number], "quote": source_lines[number - 1], "images": []}
    data["requirement_version"] = "v2"
    changes.write_json(module / "REQ清单.json", data)
    changes.write_text(module / "需求规范.md", """# 名称长度需求规范 v2

## 0. 基本信息

模块名称长度，code LEN。合成需求的设计资料变更，未执行被测软件。来源input/商品名称_v2.md。

## 1. 业务背景与范围

此次只把名称上限20改为30个字符。原有名称必填规则保留，共两条REQ。长度样例只使用英文字母。

## 2. 功能需求

- **REQ_LEN_001** 名称最多30个字符。AC_LEN_001_01：30个英文字母允许保存；AC_LEN_001_02：31个或更多英文字母提示“商品名称最多30个字符”且不创建。
- **REQ_LEN_002** 名称必填。AC_LEN_002_01：名称为空提示“请输入商品名称”且不创建。

## 3. 业务流程

正常保存、空名称或超限被拒绝的业务意图没有改变。必填异常分别检查初始空值与清空已填写名称两种输入状态。

## 4. 输入与业务约束

新的长度上限为30，边界数据改为30个A和31个A。其他字段合法，执行前清理同名商品。字符集之外的行为没有定义。

## 5. 非功能需求

本例没有明确非功能指标，不自行添加性能验收结论。

## 6. 依赖与关联

需要打开新增商品页并可核对提示及列表。没有接入真实运行环境。

## 7. 附图与来源

没有附图。REQ逐条引用v2实际Markdown第5、7行，两个版本原件分别保留。

## 8. 待澄清问题

已确认范围没有阻塞问题。其他字符集没有定义，不自行生成断言。
""")
    changes.write_text(module / "解析.md", "# 名称长度v2解析底稿\n\n来源：input/商品名称_v2.md，两个版本原件分别保留。\n\n" + "\n\n".join(source_lines[number - 1] for number in (5, 7)) + "\n")


def resolve_example(draft: Path):
    """Concrete reviewed design edits for this one declared example rule."""
    points = changes.load_json(draft / "测试点数据.json")
    points["testpoints"][0].update(测试点名称="30字符合法上限允许保存", 检查内容="输入30个英文字母A并保存，核对商品创建。", 测试数据="名称：" + "A" * 30 + "（30个A）。", 预期结果=["允许保存商品。", "列表能找到名称为30个A的本次商品。"])
    points["testpoints"][1].update(测试点名称="31字符超过上限阻止创建", 检查内容="输入31个英文字母A并保存，核对v2指定提示和未创建结果。", 测试数据="名称：" + "A" * 31 + "（31个A）。", 预期结果=["提示‘商品名称最多30个字符’。", "没有创建本次商品。"])
    cases = changes.load_json(draft / "用例数据.json")
    cases["cases"][0].update(用例名称="30字符合法上限保存", 前置条件="已打开新增商品页，列表不存在名称为30个A的商品，其他字段合法。", 操作步骤=["输入名称" + "A" * 30 + "（30个A）。", "点击保存。", "在商品列表查找本次名称。"], 测试数据="名称：" + "A" * 30 + "（30个A）；其他字段合法。", 预期结果=["允许保存商品。", "列表能找到名称为30个A的本次商品。"])
    cases["cases"][1].update(用例名称="31字符超过上限保存被拒绝", 前置条件="已打开新增商品页，列表不存在名称为31个A的商品，其他字段合法。", 操作步骤=["输入名称" + "A" * 31 + "（31个A）。", "点击保存并观察提示。", "核对商品列表没有本次商品。"], 测试数据="名称：" + "A" * 31 + "（31个A）；其他字段合法。", 预期结果=["提示‘商品名称最多30个字符’。", "没有创建本次商品。"])
    for kind, data in (("testpoints", points), ("cases", cases)):
        data.update(draft=False, update_state="resolved")
        for row in data[kind]:
            state = row.get("update_state")
            if state:
                if state == "needs_redesign":
                    resolution = "已依据v2原文第5行把同一长度边界测试的数据20/21改为30/31，并核对新提示。业务意图仍是上限合法与上限外拒绝，保留原ID。"
                else:
                    resolution = "已核对v2第7行对应原文；名称必填规则与v1相同，初始空名称与清空已填名称均适用，仅更新证据依据，保留原业务内容。"
                row.update(update_state="resolved", update_resolution=resolution, same_test_intent=True)
        changes.write_json(draft / changes.FILES[kind], data)
    requirements = changes.load_json(draft / "REQ清单.json")
    requirements.update(draft=False, update_state="resolved")
    changes.write_json(draft / "REQ清单.json", requirements)
    plan = changes.load_json(draft / "更新计划.json")
    plan.update(draft=False, update_state="resolved")
    changes.write_json(draft / "更新计划.json", plan)


def run(project: Path, render: bool) -> dict:
    seeds = PACKAGE / "examples-v12" / "change-example" / "seeds-v1"
    if project.exists():
        raise changes.ValidationError("演示输出目录已存在，拒绝覆盖。请指定一个尚不存在的目录。")
    shutil.copytree(seeds, project)
    project = project.resolve()
    module = project / "output" / "名称长度"
    old_cases = copy.deepcopy(changes.load_json(module / "用例数据.json")["cases"])
    old_case = old_cases[0]
    originals = {path.name: changes.digest(path) for path in (project / "input").iterdir() if path.is_file()}
    spec = changes.validate_spec(module)
    assert len(spec["requirements"]) == 2
    assert len(old_cases) == 4
    command(module, "testpoints", "export_testpoints.py")
    command(module, "testpoints", "export_xmind.py")
    command(module, "testcase-builder", "check_coverage.py")
    if render:
        environment = os.environ.copy()
        environment["QA_XLSX_PREVIEW_DIR"] = str(project / "preview-v1")
        command(module, "testcase-builder", "render_xlsx.py", environment=environment)
    changes.write_text(module / "评审报告.md", "# v1待评审记录\n\n本例的设计尚未进行正式业务评审。此文件用于验证需求换版后旧评审资料会归档，不声明测试点、用例或软件已经通过。\n")
    baseline = changes.create_snapshot(module, "v1-before-rule-change")
    update_requirements(module, project)
    analysis = changes.analyze(module, baseline)
    changes.write_json(module / "变更影响.json", analysis)
    changes.write_text(module / "变更影响报告.md", changes.render_analysis(analysis))
    redesigned = [row["id"] for row in analysis["affected_cases"] if row["update_state"] == "needs_redesign"]
    evidence_only = [row["id"] for row in analysis["affected_cases"] if row["update_state"] == "needs_evidence_check"]
    assert redesigned == ["TC_LEN_001", "TC_LEN_002"]
    assert evidence_only == ["TC_LEN_003", "TC_LEN_004"]
    draft = changes.prepare(module, baseline)
    # Keep the actual initial pending draft as evidence; resolution must not
    # make this demonstration appear to have bypassed redesign blockers.
    pending = draft / "prepared-before-resolution"
    pending.mkdir()
    for path in draft.iterdir():
        if path.is_file():
            shutil.copy2(path, pending / path.name)
    try:
        changes.apply(module, draft)
    except changes.ValidationError as exc:
        assert "草稿" in str(exc)
    else:
        raise AssertionError("未完成草稿错误地发布。")
    resolve_example(draft)
    backup = changes.apply(module, draft)
    new_cases = changes.load_json(module / "用例数据.json")["cases"]
    new_case = new_cases[0]
    assert len(new_cases) == 4
    for original, proposed in zip(old_cases, new_cases):
        assert original["用例编号"] == proposed["用例编号"]
        for field in changes.EXECUTION_FIELDS:
            assert original.get(field) == proposed.get(field), "历史执行记录被更改。"
    assert originals == {path.name: changes.digest(path) for path in (project / "input").iterdir() if path.is_file()}, "原始文件被改写。"
    assert changes.load_json(pending / "用例数据.json")["draft"] is True
    assert changes.load_json(pending / "用例数据.json")["cases"][0]["update_state"] == "needs_redesign"
    published_spec = changes.validate_spec(module)
    changes.validate_points(changes.load_json(module / "测试点数据.json"), published_spec)
    changes.validate_cases(changes.load_json(module / "用例数据.json"), published_spec)
    assert new_case["测试数据"] == "名称：" + "A" * 30 + "（30个A）；其他字段合法。"
    assert new_cases[1]["测试数据"] == "名称：" + "A" * 31 + "（31个A）；其他字段合法。"
    assert not (module / "测试点.xmind").exists()
    assert (backup / "stale-derived" / "测试点.xmind").is_file()
    assert (backup / "stale-derived" / "评审报告.md").is_file()
    if render:
        assert (backup / "stale-derived" / "详细用例.xlsx").is_file()
        assert not (module / "详细用例.xlsx").exists()
    changes.write_text(module / "重新生成待办.md", "# v2派生产物待办\n\n当前REQ、测试点及用例设计已通过文件结构、原文来源和关联检查，尚未完成独立业务评审，也没有执行被测软件。\n\n- 重新输出测试点清单和覆盖率报告。\n- 对v2逐点独立评审，生成新的测试点评审结果。\n- 重新生成v2测试点.xmind和详细用例.xlsx。\n- 重新检查TP→TC关联及REQ/AC覆盖，然后逐条评审详细用例。\n- 接入实际环境后才可执行；TC_LEN_001中的旧备注和execution_records为v1演示导入资料，不是v2执行结果。\n\n旧Excel/XMind/评审/覆盖结果已归档至history/中的stale-derived/，不能充当v2有效结果。\n")
    summary = {"schema_version": "1.0", "example_only": True, "software_executed": False, "formal_business_review_completed": False,
               "requirement_count": 2, "testpoint_count": 4, "case_count": 4, "original_files_unchanged": True, "unresolved_draft_rejected": True,
               "changed_rule": "名称上限20→30个字符", "redesigned_cases": redesigned, "evidence_only_cases": evidence_only,
               "preserved_case_id": new_case["用例编号"], "execution_records_preserved": True,
               "baseline": str(baseline.relative_to(module)), "update_batch": str(draft.relative_to(module)), "backup": str(backup.relative_to(module)),
               "old_excel_generated_and_archived": render, "old_xmind_archived": True, "old_review_archived": True,
               "next_action": "重生成派生资料并独立评审，尚无v2执行结果。"}
    changes.write_json(project / "演示验证结果.json", summary)
    changes.write_text(project / "演示验证结果.md", "# 变更演示验证结果\n\n名称长度20→30字符的完整设计更新流程已跑通，共2条REQ、4条测试点、4条详细用例。两条长度边界用例重设计，两条必填用例只核对新版证据；四条用例均保留原编号。\n\n两个版本原文指纹保持不变。v1快照、prepared-before-resolution/中实际被拒绝的待处理草稿、完成说明、应用前备份及过期资料归档均保留。TC_LEN_001的未执行演示备注与执行记录字段保持原样。\n\n旧XMind和待评审报告已归档" + ("，旧Excel也实际生成并归档。\n" if render else "；此复验未生成Excel。\n") + "\n当前没有伪造v2评审通过、自动化执行或软件测试通过结果；后续工作见output/名称长度/重新生成待办.md。\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="尚不存在的输出项目目录；默认在临时目录复验。")
    parser.add_argument("--render", action="store_true", help="使用可用Artifact Tool运行时实际生成旧Excel及预览。")
    args = parser.parse_args()
    try:
        if args.output:
            result = run(args.output, args.render)
        else:
            with tempfile.TemporaryDirectory(prefix="qa-change-example-") as temp:
                result = run(Path(temp) / "project", args.render)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (changes.ValidationError, OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError) as exc:
        return changes.cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
