#!/usr/bin/env python3
"""把 skill 目录打成 UTF-8 安全的 zip。

为什么需要它：macOS 自带的 `zip` 命令在写中文文件名时不设置 zip 的 UTF-8 标志位
（EFS, flag 0x800），换一台机器解包会得到乱码文件名（如 `Φ«óσìòΣ╝ÿµâá.md`）。
Python 的 zipfile 对非 ASCII 名字会自动置位 0x800，所以用它打包。

用法：
    python3 打包skill.py <skill目录> [--outdir 输出目录] [--name 前缀] [--check 已有zip]

    --check 只校验已有 zip 的 UTF-8 标志位，不重新打包
"""
import argparse
import os
import re
import sys
import zipfile

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_FILES = {".DS_Store"}


def read_version(skill_dir):
    p = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(p):
        return "unknown"
    text = open(p, encoding="utf-8", errors="ignore").read()
    m = re.search(r'^\s*version:\s*"?([^"\n]+)"?', text, re.M)
    return m.group(1).strip() if m else "unknown"


def iter_files(skill_dir):
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES:
                continue
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, os.path.dirname(skill_dir.rstrip("/")))


def check_zip(path):
    zf = zipfile.ZipFile(path)
    non_ascii = [i for i in zf.infolist() if any(ord(c) > 127 for c in i.filename)]
    bad = [i for i in non_ascii if not i.flag_bits & 0x800]
    print(f"  {path}")
    print(f"    条目 {len(zf.infolist())} 个，其中非 ASCII 名 {len(non_ascii)} 个")
    print(f"    UTF-8 标志位缺失 {len(bad)} 个 → {'❌ 换机会乱码' if bad else '✅ 正常'}")
    return not bad


def pack(skill_dir, outdir, name):
    skill_dir = os.path.realpath(skill_dir)
    if not os.path.isdir(skill_dir):
        sys.exit(f"目录不存在：{skill_dir}")
    version = read_version(skill_dir)
    base = name or os.path.basename(skill_dir)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f"{base}-v{version}.zip")
    root = os.path.basename(skill_dir)

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, rel in iter_files(skill_dir):
            zf.write(full, os.path.join(root, rel))
            count += 1

    print(f"  已打包 {count} 个文件 → {out}")
    print(f"  版本：{version}（读自 SKILL.md）")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("skill_dir")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--name")
    ap.add_argument("--check", action="store_true", help="只校验已有 zip 的标志位")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check_zip(args.skill_dir) else 1)
    out = pack(args.skill_dir, args.outdir, args.name)
    sys.exit(0 if check_zip(out) else 1)


if __name__ == "__main__":
    main()
