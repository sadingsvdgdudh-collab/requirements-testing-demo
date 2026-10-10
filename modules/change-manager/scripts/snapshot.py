#!/usr/bin/env python3
"""保存模块文件、原文证据副本和SHA清单，不修改当前资料。"""
import argparse
from pathlib import Path
from change_common import ValidationError, cli_error, create_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    try:
        result = create_snapshot(args.module_dir, args.label)
        print(f"已保存快照：{result}")
        return 0
    except (ValidationError, OSError, TypeError, KeyError, ValueError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
