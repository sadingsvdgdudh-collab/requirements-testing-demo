"""Small, standard-library helpers for the three requirement validators."""

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"无法读取 {path}: {exc}") from exc


def read_json(path):
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON格式错误 {path}: {exc}") from exc
    require(isinstance(data, dict), f"{path}: 根节点必须是对象")
    require(data.get("schema_version") == "1.0", f"{path}: schema_version 必须为 1.0")
    return data


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def relative_path(base, value, label, within=False):
    require(nonempty(value), f"{label}: 路径不能为空")
    raw = Path(value)
    require(not raw.is_absolute(), f"{label}: 必须使用相对路径")
    path = (Path(base) / raw).resolve()
    if within:
        require(path.is_relative_to(Path(base).resolve()), f"{label}: 路径越出目录")
    require(path.is_file(), f"{label}: 文件不存在 {value}")
    return path


def line_range(value, count, label):
    require(isinstance(value, list) and len(value) == 2
            and all(type(n) is int for n in value), f"{label}: lines 必须为两个整数")
    start, end = value
    require(1 <= start <= end <= count, f"{label}: 无效行范围 {value}，文件共 {count} 行")
    return start, end


# ponytail: supports ordinary Markdown inline/reference images; HTML images
# must be transcribed to Markdown before this deterministic validation step.
INLINE_IMAGE = re.compile(r'!\[([^\]\n]*)\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+("[^"\n]*"|\'[^\'\n]*\'|\([^\n)]*\)))?\s*\)')
REFERENCE_IMAGE = re.compile(r'!\[([^\]\n]*)\]\[([^\]\n]*)\]')
REFERENCE_DEF = re.compile(r'^\s{0,3}\[([^\]\n]+)\]:\s*(<[^>\n]+>|\S+)(?:\s+("[^"\n]*"|\'[^\'\n]*\'|\([^\n)]*\)))?\s*$', re.MULTILINE)


def image_path(value, base, label):
    target = value[1:-1] if value.startswith("<") else value
    parsed = urlsplit(target)
    require(not parsed.scheme and not parsed.netloc and not parsed.query
            and not parsed.fragment, f"{label}: 图片必须是可核验的本地路径 {target}")
    return relative_path(base, unquote(parsed.path), label)


def markdown_images(text, base, label):
    definitions = {m.group(1).casefold(): m for m in REFERENCE_DEF.finditer(text)}
    images = []
    for match in INLINE_IMAGE.finditer(text):
        images.append(image_path(match.group(2), base, label))
    for match in REFERENCE_IMAGE.finditer(text):
        key = (match.group(2) or match.group(1)).casefold()
        require(key in definitions, f"{label}: 图片引用未定义 [{key}]")
        images.append(image_path(definitions[key].group(2), base, label))
    matched = len(list(INLINE_IMAGE.finditer(text))) + len(list(REFERENCE_IMAGE.finditer(text)))
    require(matched == text.count("!["), f"{label}: 图片语法无法核验，请使用普通 Markdown 图片语法")
    require(not re.search(r'<img\b', text, re.IGNORECASE), f"{label}: 请将 HTML 图片转为 Markdown 图片")
    return images


def canonical_images(text):
    """Only image URL directories may change; captions/titles/filenames remain."""
    def replace_url(match):
        url = match.group(2).strip("<>")
        name = Path(unquote(urlsplit(url).path)).name
        start, end = match.start(2) - match.start(), match.end(2) - match.start()
        return match.group(0)[:start] + f'<IMAGE:{name}>' + match.group(0)[end:]

    referenced = {(m.group(2) or m.group(1)).casefold()
                  for m in REFERENCE_IMAGE.finditer(text)}
    def replace_definition(match):
        if match.group(1).casefold() not in referenced:
            return match.group(0)
        return replace_url(match)

    return REFERENCE_DEF.sub(replace_definition, INLINE_IMAGE.sub(replace_url, text))
