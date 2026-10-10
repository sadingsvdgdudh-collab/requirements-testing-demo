#!/usr/bin/env python3
"""按稳定ID比较REQ/AC/TP/TC并输出依赖影响，不自动修改业务预期。"""
import argparse
from pathlib import Path
from change_common import ValidationError, analyze, cli_error, render_analysis, write_json, write_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.module_dir, args.baseline)
        write_json(args.module_dir / "变更影响.json", result)
        write_text(args.module_dir / "变更影响报告.md", render_analysis(result))
        print(f"已生成变更影响报告：受影响测试点{len(result['affected_testpoints'])}条，用例{len(result['affected_cases'])}条。语义差异仍需评审。")
        return 0
    except (ValidationError, OSError, TypeError, KeyError, ValueError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
