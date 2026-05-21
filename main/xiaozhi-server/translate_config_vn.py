#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import time
import argparse
from pathlib import Path
from typing import Dict

from deep_translator import GoogleTranslator


CJK_RE = re.compile(r"[\u4e00-\u9fff]")
URL_RE = re.compile(r"(https?://|wss?://|ws://)", re.IGNORECASE)


def has_chinese(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def safe_translate(text: str, translator: GoogleTranslator, cache: Dict[str, str], retries: int, sleep_sec: float) -> str:
    key = text.strip()
    if not key or not has_chinese(key):
        return text

    if key in cache:
        return text.replace(key, cache[key], 1)

    for _ in range(retries):
        try:
            translated = translator.translate(key)
            cache[key] = translated
            return text.replace(key, translated, 1)
        except Exception:
            time.sleep(sleep_sec)

    # Nếu dịch lỗi nhiều lần thì giữ nguyên
    return text


def translate_yaml_like_line(line: str, translator: GoogleTranslator, cache: Dict[str, str], retries: int, sleep_sec: float) -> str:
    stripped = line.lstrip()

    # 1) Dịch comment
    if stripped.startswith("#"):
        indent = line[: len(line) - len(stripped)]
        m = re.match(r"(#+\s*)(.*)$", stripped)
        if not m:
            return line
        hash_prefix, content = m.group(1), m.group(2)
        if has_chinese(content):
            content = safe_translate(content, translator, cache, retries, sleep_sec)
        return f"{indent}{hash_prefix}{content}"

    new_line = line

    # 2) Dịch value của dạng key: value (không đụng key)
    # key có thể gồm chữ/số/_/-/khoảng trắng nhưng không chứa dấu ':'
    m = re.match(r"^(\s*[^:#\n]+:\s*)(.*)$", new_line)
    if m:
        key_part, value_part = m.group(1), m.group(2)
        if has_chinese(value_part) and not URL_RE.search(value_part):
            value_part = safe_translate(value_part, translator, cache, retries, sleep_sec)
        new_line = key_part + value_part

    # 3) Dịch list item "- xxx"
    m2 = re.match(r"^(\s*-\s+)(.*)$", new_line)
    if m2:
        prefix, item = m2.group(1), m2.group(2)
        if has_chinese(item) and not URL_RE.search(item):
            item = safe_translate(item, translator, cache, retries, sleep_sec)
        new_line = prefix + item

    return new_line


def main():
    parser = argparse.ArgumentParser(description="Translate YAML-like config text (Chinese -> Vietnamese) while preserving keys/structure.")
    parser.add_argument("-i", "--input", required=True, help="Path to input YAML file")
    parser.add_argument("-o", "--output", required=True, help="Path to output YAML file")
    parser.add_argument("--source", default="zh-CN", help="Source language (default: zh-CN)")
    parser.add_argument("--target", default="vi", help="Target language (default: vi)")
    parser.add_argument("--retries", type=int, default=4, help="Retries per sentence (default: 4)")
    parser.add_argument("--sleep", type=float, default=0.8, help="Sleep seconds between retries (default: 0.8)")
    parser.add_argument("--throttle", type=float, default=0.05, help="Delay after each translated line to reduce rate-limit (default: 0.05)")
    parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N lines (default: 100)")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    lines = input_path.read_text(encoding="utf-8").splitlines()
    translator = GoogleTranslator(source=args.source, target=args.target)
    cache: Dict[str, str] = {}

    out_lines = []
    total = len(lines)

    for idx, line in enumerate(lines, start=1):
        new_line = translate_yaml_like_line(line, translator, cache, args.retries, args.sleep)
        out_lines.append(new_line)

        if new_line != line:
            time.sleep(args.throttle)

        if idx % args.progress_every == 0 or idx == total:
            print(f"[{idx}/{total}] done")

    output_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"\nDone. Output: {output_path}")
    print(f"Unique translated phrases (cache): {len(cache)}")


if __name__ == "__main__":
    main()