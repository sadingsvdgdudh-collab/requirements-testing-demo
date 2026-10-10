"""Reproduce real file transcriptions without embedding personal runtime paths."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(project):
    inputs = ROOT / "examples-v12/document-inputs"
    script = ROOT / "modules/document-parser/scripts/parse_document.py"
    pairs = (("截图", "任务规则截图.png"), ("Word", "任务规则.docx"), ("文字PDF", "任务规则文本.pdf"), ("扫描PDF", "任务规则扫描.pdf"))
    for module, filename in pairs:
        subprocess.run([sys.executable, str(script), "--input", str(inputs / filename), "--project-dir", str(project),
                        "--module", module], check=True)
        directory = project / "output" / module
        manifest = json.loads((directory / "解析来源.json").read_text(encoding="utf-8"))
        text = (directory / "解析.md").read_text(encoding="utf-8")
        for token in ("3", "10", "1", "金币", "未完成"):
            if token not in text:
                raise RuntimeError(f"{module}关键转录内容缺失：{token}；需核对，不修改期望绕过。")
        if not manifest["needs_review"]:
            raise RuntimeError(f"{module}示例应保留OCR或表格核对状态。")
    print("四种真实文件转录及关键字检查完成；核对记录只能在真正对照原件后填写。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, help="保存演示项目；必须新目录或相同源内容的解析目录")
    args = parser.parse_args()
    if args.project_dir:
        run(args.project_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="qa-parser-example-") as directory:
            run(Path(directory))


if __name__ == "__main__":
    main()
