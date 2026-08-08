#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源：jieba 中文分词词典（MIT License）
─────────────────────────────────
从 GitHub 下载 jieba 的 dict.txt（MIT），解析为 (词, 拼音, 词频) 三元组。
拼音由 pypinyin（MIT）自动注音，无声调小写格式（如 zhongguo）。
"""

import os
import re
import sys
import urllib.request

try:
    from pypinyin import pinyin, Style
except ImportError:
    sys.exit("需要 pypinyin: pip install pypinyin")

JIEBA_URL = "https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/dict.txt"
UA = {"User-Agent": "Mozilla/5.0"}
MAX_WORD_LEN = 6
BATCH_SIZE = 2000


def download(dest: str) -> None:
    """下载 jieba dict.txt（已存在则跳过）。"""
    if os.path.exists(dest):
        print(f"  [jieba] 已存在: {dest}")
        return
    print(f"  [jieba] 下载: {JIEBA_URL}")
    req = urllib.request.Request(JIEBA_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"  [jieba] 完成: {len(data):,} bytes")


def collect(cache_dir: str = "tmp") -> list[tuple[str, str, int]]:
    """
    采集 jieba 词条 → [(word, pinyin_no_tone, freq)]。

    步骤：下载 → 解析 → 过滤纯汉字 1-6 字 → pypinyin 批量注音。
    """
    path = os.path.join(cache_dir, "jieba_dict.txt")
    download(path)

    print("  [jieba] 解析...")
    entries_raw: list[tuple[str, int]] = []
    hanzi_re = re.compile(r"^[\u4e00-\u9fff]{1,%d}$" % MAX_WORD_LEN)
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            word, freq_str = parts[0], parts[1]
            if not freq_str.isdigit():
                continue
            if hanzi_re.match(word):
                entries_raw.append((word, int(freq_str)))

    print(f"  [jieba] 纯汉字词: {len(entries_raw):,}，pypinyin 注音...")

    # 批量注音
    entries: list[tuple[str, str, int]] = []
    for i in range(0, len(entries_raw), BATCH_SIZE):
        chunk = entries_raw[i : i + BATCH_SIZE]
        words = [w for w, _ in chunk]
        py_flat = pinyin(words, style=Style.NORMAL, heteronym=False)
        idx = 0
        for word, freq in chunk:
            n = len(word)
            syls = py_flat[idx : idx + n]
            idx += n
            py_joined = "".join(s[0] for s in syls if s)
            if py_joined and py_joined.isascii() and py_joined.islower():
                entries.append((word, py_joined, freq))

    print(f"  [jieba] 注音成功: {len(entries):,}")
    return entries
