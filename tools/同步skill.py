#!/usr/bin/env python3
"""把权威 skill 同步到其它安装位置：版本核对 → 备份 → 覆盖 → 哈希核验。

背景：这个 skill 曾经同时存在 4 份（工作区 3 份 + 安装 2 处），版本各不相同，
"哪份是最新的"只能靠人工比对才发现。这个脚本把这件事变成一条命令。

用法（脚本在 <skill>/tools/ 下）：
    python3 tools/同步skill.py                      # 默认 dry-run：只报告差异
    python3 同步skill.py --apply              # 真正写入（会先备份目标）
    python3 同步skill.py --apply --prune      # 同时删除目标里源已没有的文件
    python3 同步skill.py --source <目录> --targets <目录> [<目录>...]

默认：
    源   = ~/.agents/skills/requirements-testing   （权威源）
    目标 = ~/.codex/skills/requirements-testing    （第二处安装位置）
    备份 = <工作区>/_归档/skill同步备份-<时间戳>/<目标名>.zip

安全策略：
    - 目标版本高于源版本时拒绝覆盖（除非 --force）
    - 目标里"源已没有"的文件默认只报告、不删除（要删得加 --prune）
    - 每次写入前先整包备份，写入后逐文件核 hash
退出码：0 = 已一致或同步成功；1 = 存在差异未处理 / 同步后校验失败
"""
import argparse
import hashlib
import os
import shutil
import sys
import time
import zipfile

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_FILES = {".DS_Store"}


def read_version(skill_dir):
    p = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(p):
        return "unknown"
    for line in open(p, encoding="utf-8", errors="ignore"):
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    return "unknown"


def scan(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f in SKIP_FILES:
                continue
            p = os.path.join(dp, f)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def backup(target, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    out = os.path.join(backup_dir, os.path.basename(target.rstrip("/")) + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, dn, fn in os.walk(target):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f in SKIP_FILES:
                    continue
                p = os.path.join(dp, f)
                z.write(p, os.path.join(os.path.basename(target.rstrip("/")), os.path.relpath(p, target)))
    return out


def sync_one(src, src_files, target, apply, prune, force, backup_dir):
    target = os.path.expanduser(target)
    src_ver, dst_ver = read_version(src), (read_version(target) if os.path.isdir(target) else "（不存在）")
    dst_files = scan(target) if os.path.isdir(target) else {}

    only_src = sorted(r for r in src_files if r not in dst_files)
    diff = sorted(r for r in src_files if r in dst_files and src_files[r] != dst_files[r])
    only_dst = sorted(r for r in dst_files if r not in src_files)

    print(f"\n  目标 {target}")
    print(f"    版本：源 {src_ver} → 目标 {dst_ver}")
    print(f"    待新增 {len(only_src)} | 内容不同 {len(diff)} | 目标多出 {len(only_dst)}")

    if not (only_src or diff or only_dst):
        print("    ✅ 已一致，无需同步")
        return True, False

    if not apply:
        for r in (only_src + diff)[:6]:
            print(f"      · {r}")
        if len(only_dst):
            print(f"      · 目标多出 {len(only_dst)} 个文件（加 --prune 才会删）")
        print("    （dry-run，未写入；加 --apply 执行）")
        return True, True

    if dst_ver != "（不存在）" and dst_ver > src_ver and not force:
        print(f"    ⛔ 目标版本 {dst_ver} 高于源版本 {src_ver}，拒绝覆盖（要强行覆盖加 --force）")
        return False, True

    os.makedirs(target, exist_ok=True)
    if os.path.isdir(target) and scan(target):
        z = backup(target, backup_dir)
        print(f"    备份 → {z}")

    for rel, digest in src_files.items():
        s, d = os.path.join(src, rel), os.path.join(target, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)

    if prune:
        for rel in only_dst:
            os.remove(os.path.join(target, rel))
        print(f"    已删除目标多出的 {len(only_dst)} 个文件")

    after = scan(target)
    mismatch = [r for r in src_files if after.get(r) != src_files[r]]
    if mismatch:
        print(f"    ❌ 同步后校验失败：{len(mismatch)} 个文件 hash 不一致")
        return False, True
    print(f"    ✅ 同步完成并核验通过（{len(src_files)} 个文件）")
    return True, True


def main():
    # 备份默认放在用户目录下：脚本位于 <skill>/tools/，若按 cwd 或脚本位置推导，
    # 一旦在技能目录里执行就会把备份写进技能包自身（实测踩到过）。所以固定放 ~ 下。
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="~/.agents/skills/requirements-testing")
    ap.add_argument("--targets", nargs="+", default=["~/.codex/skills/requirements-testing"])
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--prune", action="store_true", help="删除目标里源已没有的文件")
    ap.add_argument("--force", action="store_true", help="目标版本更高时也覆盖")
    ap.add_argument("--backup-dir",
                    default=os.path.expanduser("~/requirements-testing-归档/skill同步备份"))
    args = ap.parse_args()

    src = os.path.expanduser(args.source)
    if not os.path.isdir(src):
        sys.exit(f"源目录不存在：{src}")
    backup_dir = os.path.realpath(os.path.expanduser(args.backup_dir))
    if backup_dir == os.path.realpath(src) or backup_dir.startswith(os.path.realpath(src) + os.sep):
        sys.exit(f"拒绝把备份写进技能源目录：{backup_dir}\n  请用 --backup-dir 指定技能目录之外的位置。")
    args.backup_dir = backup_dir
    src_files = scan(src)
    print(f"权威源：{src}")
    print(f"  版本 {read_version(src)} | {len(src_files)} 个文件")
    print(f"  模式：{'写入' if args.apply else 'dry-run（只报告）'}")

    backup_dir = os.path.join(args.backup_dir, time.strftime("%Y%m%dT%H%M%S"))
    ok, changed = True, False
    for t in args.targets:
        good, ch = sync_one(src, src_files, t, args.apply, args.prune, args.force, backup_dir)
        ok, changed = ok and good, changed or ch

    print()
    if not ok:
        sys.exit(1)
    sys.exit(1 if (changed and not args.apply) else 0)


if __name__ == "__main__":
    main()
