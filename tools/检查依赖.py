#!/usr/bin/env python3
"""探测 requirements-testing 的依赖是否齐全，并说明缺哪个会影响什么。

只探测，不安装、不修改任何文件。

用法：
    python3 检查依赖.py                 # 列出全部依赖状态（缺了也返回 0）
    python3 检查依赖.py --strict        # 有任何必需/复验依赖缺失即返回 1
    python3 检查依赖.py --skill <目录>  # 指定技能目录（默认 ~/.agents/skills/requirements-testing）

影响说明里的数字来自 2026-10-10 本机 `python3 -m unittest discover -s tests` 的实测：
    齐全基线   Ran 247 → errors=2, skipped=4
    缺 openpyxl Ran 222 → failures=3, errors=8, skipped=5
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

# 默认检查本脚本所属的技能目录：<skill>/tools/检查依赖.py → <skill>
DEFAULT_SKILL = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# (键, 展示名, 等级, 缺失影响, 怎么补)
CHECKS = [
    ("openpyxl", "openpyxl", "必需",
     "2 个测试文件无法导入、整轮少收 25 项；Ran 222 → failures=3, errors=8",
     "pip install openpyxl"),
    ("pdfplumber", "pdfplumber", "运行用",
     "PDF 解析报能力不可用并停止（DOCX 路径不受影响）",
     "pip install pdfplumber"),
    ("PIL", "Pillow", "运行用",
     "pdfplumber 渲染依赖缺失，PDF 路径同样不可用",
     "pip install Pillow"),
    ("pypdfium2", "pypdfium2", "运行用",
     "pdfplumber 渲染依赖缺失",
     "pip install pypdfium2"),
    ("pypdf", "pypdf", "复验用",
     "1 项 ERROR：test_password_pdf_reports_clear_failure",
     "pip install pypdf"),
    ("docx", "python-docx", "复验用",
     "解析复验无法生成内部临时 DOCX 检查文件",
     "pip install python-docx"),
]


def probe_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def probe_node():
    exe = os.environ.get("QA_NODE_EXECUTABLE") or shutil.which("node")
    if not exe or not os.path.exists(exe):
        return False, None
    return True, exe


def probe_artifact_tool(node_exe):
    if not node_exe:
        return False
    try:
        r = subprocess.run([node_exe, "-e", "require.resolve('@oai/artifact-tool')"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def probe_ocr():
    if sys.platform == "darwin":
        return shutil.which("xcrun") is not None, "macOS Vision（xcrun/swift）"
    return shutil.which("tesseract") is not None, "Tesseract + chi_sim/eng"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default=DEFAULT_SKILL)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    skill = os.path.expanduser(args.skill)

    print(f"技能目录：{skill}")
    print(f"Python：{sys.version.split()[0]}  ({'✅ ≥3.10' if sys.version_info >= (3, 10) else '❌ 需 3.10+'})")
    for f in ("requirements.txt", "依赖说明.md"):
        print(f"  {f}：{'✅ 存在' if os.path.exists(os.path.join(skill, f)) else '❌ 缺失'}")

    missing_required, missing_soft = [], []
    print("\n依赖状态：")
    for mod, label, level, impact, fix in CHECKS:
        ok = probe_module(mod)
        if not ok:
            (missing_required if level == "必需" else missing_soft).append(label)
        print(f"  {'✅' if ok else '❌'} {label:14s} [{level}]")
        if not ok:
            print(f"        影响：{impact}")
            print(f"        补：  {fix}")

    node_ok, node_exe = probe_node()
    tool_ok = probe_artifact_tool(node_exe)
    print(f"  {'✅' if node_ok else '❌'} {'Node.js':14s} [运行用]"
          + (f"  {node_exe}" if node_exe else ""))
    if not node_ok:
        print("        影响：Excel 测试整组被 setUpClass 拦住（pytest 记 23 个 error / unittest 记 1 个）；"
              "用例工作簿自动回退 xlsx_fallback.py，通用 Excel 模块报能力不可用")
        print("        补：  安装 Node，或设 QA_NODE_EXECUTABLE 指向工作区 Node")
        missing_soft.append("Node.js")
    print(f"  {'✅' if tool_ok else '❌'} {'@oai/artifact-tool':14s} [可选]")
    if node_ok and not tool_ok:
        print("        影响：用例工作簿改用包内 Python 回退（契约一致，视觉细节不承诺）；"
              "通用 Excel 模块无回退、报能力不可用")
        print("        补：  由工作区提供该包，或用 QA_XLSX_FALLBACK=python 明确走回退")
    ocr_ok, ocr_name = probe_ocr()
    print(f"  {'✅' if ocr_ok else '❌'} {('OCR（' + ocr_name + '）'):14s} [可选]")
    if not ocr_ok:
        print("        影响：截图/扫描件转录不可用；3 项 OCR 验收 skipped（需 QA_TEST_OCR=1）")
        missing_soft.append("OCR")

    print()
    if missing_required:
        print(f"❌ 缺少必需依赖：{', '.join(missing_required)}")
    elif missing_soft:
        print(f"⚠️  必需依赖齐全；可选/复验依赖缺少：{', '.join(missing_soft)}")
        print("     —— 不影响 DOCX 资料的主流程，相关能力会明确报告不可用")
    else:
        print("✅ 依赖齐全，可跑完整复验")

    sys.exit(1 if (args.strict and (missing_required or missing_soft)) else 0)


if __name__ == "__main__":
    main()
