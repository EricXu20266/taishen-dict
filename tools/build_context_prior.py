#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上下文先验表构建：从纯文本语料统计相邻词共现，产出 context_prior.db。

产物结构（供引擎 engine_set_context_prior_path 读取）：

    CREATE TABLE context_prior (
        prev  TEXT NOT NULL,
        next  TEXT NOT NULL,
        count INTEGER NOT NULL,      -- 归一化档位 1-6，直接当前移权重
        PRIMARY KEY (prev, next)
    );

用法：

    python tools/build_context_prior.py --corpus corpus_dir --dict output/system_dict.db \
        --out output/context_prior.db [--top 200000] [--min-count 3]

说明：
- 语料：--corpus 指向目录，递归读取 .txt / .md（UTF-8）。
- 分词：以系统词库词表做正向最大匹配；连续非汉字作切句边界。
- 归一化：按共现次数的分位映射到 1-6 档，避免超高频对压死全部候选。
- 只保留 2-4 字的词，前词与后词都不能是单字虚词（过滤「的/了/是」这类噪声）。
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter

HAN_MIN, HAN_MAX = 0x4E00, 0x9FFF

# 高频虚词：作为前词或后词都会产生大量无意义搭配
STOP_WORDS = {
    "的", "了", "是", "在", "和", "与", "就", "都", "而", "及", "或",
    "也", "还", "又", "很", "更", "最", "被", "把", "为", "以", "对",
    "从", "到", "上", "下", "中", "个", "这", "那", "有", "没有",
}

WORD_MIN_LEN, WORD_MAX_LEN = 2, 4


def load_vocab(dict_db: str) -> set:
    """从系统词库读取词表（只用词，不用词频）。"""
    vocab = set()
    conn = sqlite3.connect(dict_db)
    try:
        cur = conn.execute("SELECT word FROM system_dict")
        for (word,) in cur:
            if WORD_MIN_LEN <= len(word) <= WORD_MAX_LEN:
                vocab.add(word)
    except sqlite3.Error as exc:
        sys.exit(f"读取词表失败: {exc}")
    finally:
        conn.close()
    if not vocab:
        sys.exit("词表为空，检查 --dict 是否指向 system_dict.db")
    print(f"词表: {len(vocab)} 词")
    return vocab


def iter_corpus(corpus_dir: str):
    """遍历语料目录的文本文件。"""
    for root, _dirs, files in os.walk(corpus_dir):
        for name in files:
            if name.lower().endswith((".txt", ".md")):
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        yield path, fh.read()
                except OSError as exc:
                    print(f"跳过 {path}: {exc}")


def is_han(ch: str) -> bool:
    return HAN_MIN <= ord(ch) <= HAN_MAX


def split_sentences(text: str):
    """连续汉字段视为一个待分词片段（标点/空白/字母数字均为边界）。"""
    buf = []
    for ch in text:
        if is_han(ch):
            buf.append(ch)
        elif buf:
            yield "".join(buf)
            buf = []
    if buf:
        yield "".join(buf)


def segment(segment_text: str, vocab: set, max_len: int):
    """正向最大匹配分词：未命中词表的字串按单字输出（单字不参与共现统计）。"""
    out = []
    i = 0
    n = len(segment_text)
    while i < n:
        hit = None
        for size in range(min(max_len, n - i), 0, -1):
            candidate = segment_text[i:i + size]
            if size == 1 or candidate in vocab:
                hit = candidate
                break
        out.append(hit if hit else segment_text[i])
        i += len(hit) if hit else 1
    return out


def count_pairs(corpus_dir: str, vocab: set, max_len: int) -> Counter:
    """统计相邻词共现（同一句内相邻）。"""
    pairs: Counter = Counter()
    files = 0
    for path, text in iter_corpus(corpus_dir):
        files += 1
        for seg_text in split_sentences(text):
            words = segment(seg_text, vocab, max_len)
            for left, right in zip(words, words[1:]):
                if left in STOP_WORDS or right in STOP_WORDS:
                    continue
                if not (WORD_MIN_LEN <= len(left) <= WORD_MAX_LEN):
                    continue
                if not (WORD_MIN_LEN <= len(right) <= WORD_MAX_LEN):
                    continue
                pairs[(left, right)] += 1
    print(f"语料文件: {files}，候选搭配: {len(pairs)}")
    return pairs


def normalize_tiers(counts: list, tiers: int = 6) -> list:
    """按共现次数分位映射到 1..tiers 档（同值同档，次数越高档位越高）。"""
    unique = sorted(set(counts))
    total = len(unique)
    tier_of = {
        value: min(tiers, 1 + idx * tiers // max(1, total))
        for idx, value in enumerate(unique)
    }
    return [tier_of[c] for c in counts]


def write_db(out_path: str, rows: list):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_path = out_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute(
            """
            CREATE TABLE context_prior (
                prev  TEXT NOT NULL,
                next  TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (prev, next)
            )
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO context_prior (prev, next, count) VALUES (?, ?, ?)",
            rows,
        )
        conn.execute("CREATE INDEX idx_prior_prev ON context_prior(prev, count DESC)")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    os.replace(tmp_path, out_path)
    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"已写出 {out_path}（{len(rows)} 对，{size_mb:.1f} MB）")


def main():
    parser = argparse.ArgumentParser(description="从语料构建上下文先验表")
    parser.add_argument("--corpus", required=True, help="语料目录")
    parser.add_argument("--dict", required=True, help="system_dict.db 路径")
    parser.add_argument("--out", required=True, help="输出 context_prior.db")
    parser.add_argument("--top", type=int, default=200000, help="最多保留的搭配对数")
    parser.add_argument("--min-count", type=int, default=3, help="最小共现次数")
    parser.add_argument("--max-word-len", type=int, default=WORD_MAX_LEN)
    args = parser.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit(f"语料目录不存在: {args.corpus}")

    vocab = load_vocab(args.dict)
    pairs = count_pairs(args.corpus, vocab, args.max_word_len)
    if not pairs:
        sys.exit("未统计到任何搭配，检查语料内容与词表是否匹配")

    kept = [(pair, c) for pair, c in pairs.items() if c >= args.min_count]
    kept.sort(key=lambda item: item[1], reverse=True)
    kept = kept[: args.top]
    if not kept:
        sys.exit(f"没有搭配达到最小共现次数 {args.min_count}")

    tiers = normalize_tiers([c for _, c in kept])
    rows = [(prev, nxt, tier) for (prev, nxt), tier in zip([p for p, _ in kept], tiers)]
    write_db(args.out, rows)
    print(f"频次区间: {kept[-1][1]} ~ {kept[0][1]}，归一化 {min(tiers)}~{max(tiers)} 档")


if __name__ == "__main__":
    main()
