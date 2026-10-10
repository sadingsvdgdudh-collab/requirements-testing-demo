#!/usr/bin/env python3
"""修复 zip 里中文文件名的编码（macOS zip 不写 UTF-8 标志位导致的乱码）。

现象：用 macOS 自带 `zip` 打包时，非 ASCII 文件名以 UTF-8 字节写入，但没有置
zip 的 UTF-8 标志位（EFS, 0x800）。规范解包器会按 CP437 解出乱码，例如
`订单优惠.md` → `Φ«óσìòΣ╝ÿµâá.md`。

修复：把乱码名按 cp437 编回字节、再按 UTF-8 解码，得到原名，然后用 Python 的
zipfile 重写一遍（Python 对非 ASCII 名字会自动置位 0x800）。

用法：
    python3 修复zip编码.py <zip> [更多zip...] [--in-place|--outdir 目录] [--keep-original 目录]

默认：写出 <原名>.utf8.zip，不动原文件。加 --in-place 才原地替换（原文件移到
--keep-original 指定的目录，默认 ./_旧编码原件/）。
"""
import argparse
import os
import shutil
import sys
import zipfile


def recover(name):
    """尽量把 CP437 乱码名还原成 UTF-8 原名；还原不了就原样返回。"""
    try:
        fixed = name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name, False
    return fixed, fixed != name


def fix(zip_path, out_path=None, keep_original_dir=None, in_place=False):
    src = zipfile.ZipFile(zip_path)
    entries = src.infolist()
    recovered = 0
    new_entries = []
    for i in entries:
        name, changed = recover(i.filename) if any(ord(c) > 127 for c in i.filename) else (i.filename, False)
        recovered += 1 if changed else 0
        new_entries.append((name, i))

    if in_place:
        out_path = out_path or (zip_path + ".utf8.tmp")
    else:
        out_path = out_path or (zip_path + ".utf8.zip")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, info in new_entries:
            data = src.read(info)
            new_info = zipfile.ZipInfo(name, date_time=info.date_time)
            new_info.external_attr = info.external_attr
            new_info.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(new_info, data)

    # 校验：条目数一致、内容一致、标志位齐全
    dst = zipfile.ZipFile(out_path)
    new_infos = dst.infolist()
    ok = len(new_infos) == len(entries)
    mism = 0
    for new_info, old_info in zip(new_infos, entries):
        if dst.read(new_info.filename) != src.read(old_info):
            mism += 1
    bad_flag = [i for i in new_infos
                if any(ord(c) > 127 for c in i.filename) and not i.flag_bits & 0x800]

    print(f"  {os.path.basename(zip_path)}")
    print(f"    条目 {len(entries)} 个；恢复文件名 {recovered} 个；内容不一致 {mism} 个")
    print(f"    UTF-8 标志位缺失 {len(bad_flag)} 个 → {'❌' if bad_flag else '✅'}")
    good = ok and mism == 0 and not bad_flag

    if in_place:
        if not good:
            os.remove(out_path)
            sys.exit("  校验未通过，已放弃原地替换，原文件未动")
        if keep_original_dir:
            os.makedirs(keep_original_dir, exist_ok=True)
            shutil.move(zip_path, os.path.join(keep_original_dir, os.path.basename(zip_path)))
            shutil.move(out_path, zip_path)
            print(f"    已原地替换；旧文件保存在 {keep_original_dir}/")
        else:
            shutil.move(out_path, zip_path)
            print("    已原地替换（旧文件已丢弃）")
    else:
        print(f"    写出 {out_path}")
    return good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zips", nargs="+")
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--outdir")
    ap.add_argument("--keep-original", default="_旧编码原件")
    args = ap.parse_args()

    all_good = True
    for z in args.zips:
        if not os.path.exists(z):
            print(f"  跳过（不存在）：{z}")
            continue
        out = None
        if not args.in_place and args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            out = os.path.join(args.outdir, os.path.basename(z))
        all_good = fix(z, out, args.keep_original, args.in_place) and all_good
    sys.exit(0 if all_good else 1)


if __name__ == "__main__":
    main()
