#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 bigram.db 挖「上下文应有提升」的验证案例，供引擎侧探针复跑。

产出 TSV 供 taishenIME 的 engine/examples/probe_context.rs 读取：

    prev_word \\t prev_pinyin \\t next_pinyin \\t next_word \\t rank \\t tier

筛选口径（缺一不可）：
  1. context_prior 档位 >= --min-tier（默认 3，即有 4 次以上共现）
  2. prev 是其拼音的**第一候选**——探针用 select(0) 定前词，选不中它测不出效果
  3. next 在同拼音 top6 内且**非首位**——首位本来就赢，测不出提升
  4. 每个 next 拼音只取一例，避免同音干扰
  5. prev / next 均为 --min-len..--max-len 字（默认 2-4），排除单字碎片

用法：

    python tools/mine_context_cases.py --out tmp/context_cases.tsv

    cd taishenIME/engine
    cargo run --release --example probe_context -- ..\\tmp\\context_cases.tsv

为什么按档位分层取样而不是全量随机：需要档位 3-6 各有一批样本，
才能看出「强搭配是否提得更多」；随机取样会被档位 2 淹没（占 58%）。
"""
import argparse
import os
import random
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIGRAM = os.path.join(ROOT, "output", "bigram.db")
DICT = os.path.join(ROOT, "output", "system_dict.db")

# 前词是这些单字虚词时搭配没有预测价值
STOP = {"的", "了", "是", "在", "和", "与", "就", "都", "而", "及", "或",
        "也", "还", "又", "很", "更", "最", "被", "把", "为", "以", "对",
        "从", "到", "上", "下", "中", "个", "这", "那", "有", "没有"}
# 后词是虚词时搭配弱，测出来也不代表真实收益
WEAK_NEXT = {"的", "了", "是", "在", "和", "地", "得", "着", "过", "等",
             "与", "也", "而", "就", "至", "为", "被", "把", "或", "及"}


def load_dict():
    conn = sqlite3.connect(DICT)
    word_py = {}
    py_words = {}
    for pinyin, word, _freq in conn.execute(
        "SELECT pinyin, word, frequency FROM system_dict ORDER BY pinyin, frequency DESC"
    ):
        if word not in word_py:
            word_py[word] = pinyin
        bucket = py_words.setdefault(pinyin, [])
        if len(bucket) < 8:
            bucket.append(word)
    conn.close()
    return word_py, py_words


def main():
    ap = argparse.ArgumentParser(description="挖上下文验证案例")
    ap.add_argument("--out", default=os.path.join(ROOT, "tmp", "context_cases.tsv"))
    ap.add_argument("--per-tier", type=int, default=30, help="每档取多少例")
    ap.add_argument("--min-tier", type=int, default=3)
    ap.add_argument("--min-len", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20261117)
    args = ap.parse_args()

    random.seed(args.seed)
    print("加载词库...", file=sys.stderr)
    word_py, py_words = load_dict()
    print(f"词表 {len(word_py)} 词 / {len(py_words)} 拼音", file=sys.stderr)

    conn = sqlite3.connect(BIGRAM)
    rows = conn.execute(
        "SELECT prev, next, count FROM context_prior WHERE count >= ?", (args.min_tier,)
    ).fetchall()
    conn.close()
    random.shuffle(rows)
    print(f"候选对 {len(rows)}", file=sys.stderr)

    used_py = set()
    picked = {}
    for prev, nxt, tier in rows:
        if not (args.min_len <= len(prev) <= args.max_len):
            continue
        if not (args.min_len <= len(nxt) <= args.max_len):
            continue
        if prev in STOP or nxt in WEAK_NEXT:
            continue
        prev_pinyin = word_py.get(prev)
        next_pinyin = word_py.get(nxt)
        if not prev_pinyin or not next_pinyin or next_pinyin in used_py:
            continue
        # prev 必须是其拼音的第一候选
        if not py_words.get(prev_pinyin) or py_words[prev_pinyin][0] != prev:
            continue
        # next 必须在同拼音 top6 内且非首位
        cands = py_words.get(next_pinyin, [])
        if len(cands) < 2 or nxt not in cands:
            continue
        rank = cands.index(nxt) + 1
        if rank < 2 or rank > 6:
            continue
        bucket = picked.setdefault(tier, [])
        if len(bucket) >= args.per_tier:
            continue
        used_py.add(next_pinyin)
        bucket.append((prev, prev_pinyin, next_pinyin, nxt, rank, tier))
        if all(len(v) >= args.per_tier for v in picked.values()):
            break

    cases = []
    for tier in sorted(picked):
        cases.extend(picked[tier])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for case in cases:
            fh.write("\t".join(str(x) for x in case) + "\n")

    print(f"写出 {len(cases)} 例 → {args.out}", file=sys.stderr)
    for tier in sorted(picked):
        print(f"  档位{tier}: {len(picked[tier])} 例", file=sys.stderr)


if __name__ == "__main__":
    main()
