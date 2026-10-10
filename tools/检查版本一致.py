#!/usr/bin/env python3
"""校验 skill 的版本号只有一处权威来源（SKILL.md 的 metadata），其它地方必须与它一致。

规则：
    权威来源 = <skill>/SKILL.md 里 front-matter 的 `version:`
    必须一致：
      1) README.md 首行      `# 需求到测试 · v<版本>单技能完整包`
      2) README.md 有该版本的变更节  `## v<版本>本版...`
      3) 验证结果.md 首行含        `截至 v<版本>`

历史版本的章节（v1.3/v1.4/v1.5 等）与数据契约里的 `schema_version` 不参与校验。

用法：
    python3 tools/检查版本一致.py [skill目录]   # 默认本脚本所属技能（tools/ 的上一级）
退出码：0 = 一致；1 = 不一致或文件缺失
"""
import os
import re
import sys

# 默认检查本脚本所属的技能目录：<skill>/tools/检查版本一致.py → <skill>
DEFAULT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def canonical_version(skill_dir):
    p = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(p):
        return None
    for line in open(p, encoding="utf-8", errors="ignore"):
        m = re.match(r'\s*version:\s*"?([0-9][^"\s]*)"?', line)
        if m:
            return m.group(1)
    return None


def main():
    skill_dir = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    ver = canonical_version(skill_dir)
    if not ver:
        sys.exit(f"❌ 读不到版本号：{skill_dir}/SKILL.md 里没有 `version:`")

    problems = []
    print(f"权威版本（SKILL.md）：{ver}")

    # 1) README 首行 + 2) README 变更节
    readme = os.path.join(skill_dir, "README.md")
    if not os.path.exists(readme):
        problems.append("README.md 不存在")
    else:
        text = open(readme, encoding="utf-8").read()
        first = text.splitlines()[0] if text.splitlines() else ""
        expect = f"# 需求到测试 · v{ver}单技能完整包"
        if first.strip() != expect:
            problems.append(f"README.md 首行不一致\n      实际：{first.strip()}\n      期望：{expect}")
        else:
            print(f"  ✅ README.md 首行：{first.strip()}")
        if not re.search(rf"^##\s*v{re.escape(ver)}\s*本版", text, re.M):
            problems.append(f"README.md 缺少当前版本的变更节（期望形如 `## v{ver}本版…`）")
        else:
            print(f"  ✅ README.md 有 `## v{ver}本版…` 变更节")

    # 3) 验证结果.md 首行
    result = os.path.join(skill_dir, "验证结果.md")
    if not os.path.exists(result):
        problems.append("验证结果.md 不存在")
    else:
        first = open(result, encoding="utf-8").read().splitlines()[0]
        if f"截至 v{ver}" not in first:
            problems.append(f"验证结果.md 首行未对齐\n      实际：{first.strip()}\n      期望含：截至 v{ver}")
        else:
            print(f"  ✅ 验证结果.md 首行：{first.strip()}")

    if problems:
        print("\n❌ 版本号不一致：")
        for p in problems:
            print(f"    - {p}")
        sys.exit(1)

    print("\n✅ 版本号一致（唯一权威来源：SKILL.md 的 metadata）")
    sys.exit(0)


if __name__ == "__main__":
    main()
