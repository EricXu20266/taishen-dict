#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""碎片 → 长词还原（阶段 1：只读分析，不改任何数据）。

思路：碎片之所以是碎片，是因为它几乎总跟着同一个字出现。把「碎片 + 高频跟随字」
拼回去，用「该串独立出现的比例」判断它是不是一个完整的长词。

    prefix_frag（A 是长词前段，如 马尔马）→ 向右延伸 L=1..4
    suffix_frag（A 是长词后段，如 大维）  → 向左延伸 L=1..4

对每个延伸串统计：
    total_occ   语料总出现次数
    standalone  前后都不是汉字的次数（真正独立成词）

选择规则（任务书 1.1）：
    standalone >= 3 且 standalone / total_occ >= 0.5，取 total_occ 最大的串
    都不满足 → unresolved

用法：
    python tools/restore_longwords.py --candidates tmp/fragments_candidates.tsv \
        --corpus tmp/corpus_50k_full --out tmp/longword_restore.tsv [--min-occ 10]
"""

import argparse
import os
import sys
import time
from collections import defaultdict

HAN_MIN, HAN_MAX = 0x4E00, 0x9FFF


def is_han(ch: str) -> bool:
    return HAN_MIN <= ord(ch) <= HAN_MAX


def all_han(s: str) -> bool:
    return all(is_han(c) for c in s)


def load_fragments(path: str, min_occ: int) -> list:
    """从第 4 轮候选表取碎片：[(word, direction, total_occ, pinyin, follow_char)]。"""
    out = []
    seen = set()
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < len(header):
                continue
            verdict = cols[idx["verdict"]]
            if verdict == "prefix_frag":
                direction = "R"
            elif verdict == "suffix_frag":
                direction = "L"
            else:
                continue
            occ = int(cols[idx["total_occ"]])
            if occ < min_occ:
                continue
            word = cols[idx["word"]]
            key = (word, direction)
            if key in seen:
                continue
            seen.add(key)
            out.append((word, direction, occ, cols[idx["pinyin"]]))
    return out


def iter_corpus(corpus_dir: str):
    for root, _dirs, files in os.walk(corpus_dir):
        for name in files:
            if name.lower().endswith((".txt", ".md")):
                yield os.path.join(root, name)


def scan(corpus_dir: str, frags: list, max_ext: int):
    """单遍扫描：统计每个 (碎片, 方向, 延伸串) 的 total / standalone。"""
    index = {}
    for i, (word, direction, _occ, _py) in enumerate(frags):
        index.setdefault(word, []).append((i, direction))

    # counts[(frag_idx, dir, ext)] = [total, standalone]
    counts: dict = defaultdict(lambda: [0, 0])
    get = index.get
    t0 = time.time()
    chars = 0

    for path in iter_corpus(corpus_dir):
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        length = len(text)
        chars += length
        for i, ch in enumerate(text):
            if not is_han(ch):
                continue
            for size in range(2, 5):
                if i + size > length:
                    break
                hits = get(text[i:i + size])
                if not hits:
                    continue
                prev_han = i > 0 and is_han(text[i - 1])
                for frag_idx, direction in hits:
                    if direction == "R":
                        # 向右延伸
                        for ext_len in range(1, max_ext + 1):
                            end = i + size + ext_len
                            if end > length:
                                break
                            tail = text[i + size:end]
                            if not all_han(tail):
                                break
                            next_han = end < length and is_han(text[end])
                            key = (frag_idx, "R", text[i:end])
                            entry = counts[key]
                            entry[0] += 1
                            if not prev_han and not next_han:
                                entry[1] += 1
                    else:
                        # 向左延伸
                        for ext_len in range(1, max_ext + 1):
                            start = i - ext_len
                            if start < 0:
                                break
                            head = text[start:i]
                            if not all_han(head):
                                break
                            prev2_han = start > 0 and is_han(text[start - 1])
                            next_han = i + size < length and is_han(text[i + size])
                            key = (frag_idx, "L", text[start:i + size])
                            entry = counts[key]
                            entry[0] += 1
                            if not prev2_han and not next_han:
                                entry[1] += 1
        print(f"  扫描 {os.path.basename(path)}：{length / 1e6:.1f}M 字，"
              f"{time.time() - t0:.1f}s", flush=True)
    print(f"语料: {chars / 1e6:.1f}M 字，延伸串 {len(counts)} 个")
    return counts


def build_rows(frags: list, counts: dict, max_ext: int,
               min_standalone: int, min_ratio: float) -> list:
    """按碎片聚合，逐长度取最强串，再按选择规则定结果。"""
    by_frag = defaultdict(list)
    for (idx, direction, ext), (total, alone) in counts.items():
        by_frag[idx].append((direction, ext, total, alone))

    rows = []
    for idx, (word, direction, occ, pinyin) in enumerate(frags):
        items = [x for x in by_frag.get(idx, []) if x[0] == direction]
        best = None
        # 每个延伸长度只保留 total 最大的串（任务书：取最高频的那个）
        per_len = {}
        for _d, ext, total, alone in items:
            ext_len = len(ext) - len(word)
            if ext_len < 1 or ext_len > max_ext:
                continue
            cur = per_len.get(ext_len)
            if cur is None or total > cur[2]:
                per_len[ext_len] = (direction, ext, total, alone)
        for ext_len, (_d, ext, total, alone) in per_len.items():
            ratio = alone / total if total else 0.0
            if alone >= min_standalone and ratio >= min_ratio:
                if best is None or total > best[2]:
                    best = (direction, ext, total, alone, ratio)
        if best:
            _d, ext, total, alone, ratio = best
            rows.append(dict(fragment=word, direction=direction, restored_word=ext,
                             total_occ=total, standalone=alone, ratio=round(ratio, 4),
                             llm_verdict="", final="accept"))
        else:
            rows.append(dict(fragment=word, direction=direction, restored_word="",
                             total_occ=0, standalone=0, ratio=0.0,
                             llm_verdict="", final="unresolved"))
    return rows


def main():
    parser = argparse.ArgumentParser(description="碎片 → 长词还原（只读）")
    parser.add_argument("--candidates", default="tmp/fragments_candidates.tsv")
    parser.add_argument("--corpus", default="tmp/corpus_50k_full")
    parser.add_argument("--out", default="tmp/longword_restore.tsv")
    parser.add_argument("--min-occ", type=int, default=10)
    parser.add_argument("--max-ext", type=int, default=4)
    parser.add_argument("--min-standalone", type=int, default=3)
    parser.add_argument("--min-ratio", type=float, default=0.5)
    args = parser.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit(f"语料目录不存在: {args.corpus}")

    frags = load_fragments(args.candidates, args.min_occ)
    print(f"碎片（occ >= {args.min_occ}）: {len(frags)}")
    counts = scan(args.corpus, frags, args.max_ext)
    rows = build_rows(frags, counts, args.max_ext, args.min_standalone, args.min_ratio)

    resolved = [r for r in rows if r["final"] == "accept"]
    unresolved = [r for r in rows if r["final"] == "unresolved"]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("fragment\tdirection\trestored_word\ttotal_occ\tstandalone\tratio"
                 "\tllm_verdict\tfinal\n")
        for r in sorted(rows, key=lambda x: -x["total_occ"]):
            fh.write("\t".join(str(r[k]) for k in
                               ("fragment", "direction", "restored_word", "total_occ",
                                "standalone", "ratio", "llm_verdict", "final")) + "\n")

    from collections import Counter as C
    dist = C(len(r["restored_word"]) for r in resolved)
    print(f"\n已写出 {args.out}")
    print(f"碎片 {len(rows)} 条：可还原 {len(resolved)}，不可还原 {len(unresolved)}")
    print(f"还原词字数分布: {dict(sorted(dist.items()))}")


if __name__ == "__main__":
    main()
