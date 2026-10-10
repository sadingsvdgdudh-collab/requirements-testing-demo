#!/usr/bin/env python3
"""准备带待处理标记的设计更新草稿，保留编号、原业务内容和执行记录。"""
import argparse
from pathlib import Path
from change_common import ValidationError, cli_error, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.module_dir, args.baseline)
        print(f"已生成更新草稿：{result}。受影响项须依据最新需求重新设计或核对证据后才能应用。")
        return 0
    except (ValidationError, OSError, TypeError, KeyError, ValueError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
