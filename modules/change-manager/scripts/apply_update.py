#!/usr/bin/env python3
"""验证完整候选资料后备份发布，归档过期派生产物；失败恢复原资料。"""
import argparse
from pathlib import Path
from change_common import ValidationError, apply, cli_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", required=True, type=Path)
    parser.add_argument("--draft-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = apply(args.module_dir, args.draft_dir)
        print(f"设计更新已应用，旧资料和过期结果已保存：{result}。请重新生成并评审Excel、XMind及覆盖报告。")
        return 0
    except (ValidationError, OSError, TypeError, KeyError, ValueError) as exc:
        return cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
