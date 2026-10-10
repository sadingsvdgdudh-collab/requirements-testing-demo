#!/usr/bin/env python3
"""Validate a UTF-8 Markdown parsing draft and its local image links."""

import argparse
import sys
from pathlib import Path

from validation_common import ValidationError, markdown_images, read_text, require


def check(md):
    md = Path(md).resolve()
    text = read_text(md)
    require(bool(text.strip()), "解析底稿为空")
    images = markdown_images(text, md.parent, str(md))
    return {"lines": len(text.splitlines()), "images": len(images)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--md", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = check(args.md)
    except ValidationError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"解析底稿校验通过：{result['lines']} 行，{result['images']} 个本地图片引用。原文准确性仍须对照原件核查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
