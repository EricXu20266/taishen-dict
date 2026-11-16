#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""碎片词条识别（第一阶段：只读语料跟随度分析，不改任何数据）。

碎片特征：一个词条几乎总是作为更长字串的一部分出现。用维基语料量化：

    total_occ    A 在语料中的总出现次数
    alone_occ    A 前后都不是汉字的次数（真正独立成词）
    follow_char  A 后紧跟同一汉字 X 的最高次数，ratio = count / total_occ
    precede_char A 前紧跟同一汉字 Y 的最高次数，ratio = count / total_occ

判定（total_occ >= --min-occ 才判）：

    standalone   独立出现占比 > --alone-ratio（如「中国」，直接保留）
    prefix_frag  follow_ratio > --ratio 且 follow_occ >= --min-evidence
    suffix_frag  precede_ratio > --ratio 且 precede_occ >= --min-evidence
    insufficient 出现次数不足 --min-occ（证据弱，保留）

用法：

    python tools/analyze_fragments.py --dict output/system_dict.db \
        --corpus tmp/corpus_50k_full --out tmp/fragments_candidates.tsv

扫描口径：对语料每个汉字位置抠 2~--max-len 字子串，只查候选集（O(1) dict）；
未在候选集的长串不影响判定——碎片本身在词表里，正向最大匹配也正是这样切出来的。
"""

import argparse
import os
import sqlite3
import sys
import time
from collections import Counter

HAN_MIN, HAN_MAX = 0x4E00, 0x9FFF


def is_han(ch: str) -> bool:
    return HAN_MIN <= ord(ch) <= HAN_MAX


def load_candidates(db_path: str, min_len: int, max_len: int, max_freq: int):
    """读候选词：长度 min_len~max_len、存在低于 max_freq 的读法。

    返回 (rows, words)：
      rows  = [(word, pinyin, frequency)]，仅 frequency < max_freq 的行（要降权的对象）
      words = 去重候选词（判断粒度是词，不是读法）
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT word, pinyin, frequency FROM system_dict "
            "WHERE length(word) BETWEEN ? AND ? AND frequency < ?",
            (min_len, max_len, max_freq),
        ).fetchall()
    except sqlite3.Error as exc:
        sys.exit(f"读取词库失败: {exc}")
    finally:
        conn.close()
    words = sorted({w for w, _p, _f in rows})
    return rows, words


def load_full_vocab(db_path: str) -> set:
    """全词表（任意长度），用于判断碎片补全后的长词是否已在库。"""
    conn = sqlite3.connect(db_path)
    try:
        return {w for (w,) in conn.execute("SELECT DISTINCT word FROM system_dict")}
    finally:
        conn.close()


def iter_corpus(corpus_dir: str):
    for root, _dirs, files in os.walk(corpus_dir):
        for name in files:
            if name.lower().endswith((".txt", ".md")):
                yield os.path.join(root, name)


class Follow:
    """记录某词前后紧邻汉字的最强跟随。"""

    __slots__ = ("cnt", "best_ch", "best_cnt", "other")

    def __init__(self):
        self.cnt = 0
        self.best_ch = ""
        self.best_cnt = 0
        self.other = Counter()

    def add(self, ch: str):
        self.cnt += 1
        if ch == self.best_ch:
            self.best_cnt += 1
            return
        if self.best_cnt == 0:
            self.best_ch, self.best_cnt = ch, 1
            return
        c = self.other[ch] + 1
        if c > self.best_cnt:
            # 原 best 降级进 other，新字转正
            self.other[self.best_ch] += self.best_cnt
            self.best_ch, self.best_cnt = ch, c
            self.other.pop(ch, None)
        else:
            self.other[ch] = c


def scan_corpus(corpus_dir: str, words: list, max_len: int):
    """单遍扫描：统计 total / alone / 前后最强跟随。"""
    index = {w: i for i, w in enumerate(words)}
    n = len(words)
    total = [0] * n
    alone = [0] * n
    follow = [None] * n
    precede = [None] * n
    get = index.get
    files = 0
    chars = 0

    for path in iter_corpus(corpus_dir):
        files += 1
        t0 = time.time()
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        length = len(text)
        chars += length
        for i, ch in enumerate(text):
            if not is_han(ch):
                continue
            for size in range(2, max_len + 1):
                if i + size > length:
                    break
                k = get(text[i:i + size])
                if k is None:
                    continue
                total[k] += 1
                prev_ch = text[i - 1] if i > 0 else ""
                next_ch = text[i + size] if i + size < length else ""
                prev_han = bool(prev_ch) and is_han(prev_ch)
                next_han = bool(next_ch) and is_han(next_ch)
                if not prev_han and not next_han:
                    alone[k] += 1
                if next_han:
                    f = follow[k]
                    if f is None:
                        f = follow[k] = Follow()
                    f.add(next_ch)
                if prev_han:
                    p = precede[k]
                    if p is None:
                        p = precede[k] = Follow()
                    p.add(prev_ch)
        print(f"  扫描 {os.path.basename(path)}：{length / 1e6:.1f}M 字，"
              f"{time.time() - t0:.1f}s", flush=True)
    print(f"语料: {files} 文件 {chars / 1e6:.1f}M 字")
    return total, alone, follow, precede


def verdict_of(total: int, alone: int, f: Follow, p: Follow,
               ratio: float, min_evidence: int, min_occ: int, alone_ratio: float):
    """返回 (verdict, follow_ratio, precede_ratio)。"""
    if total < min_occ:
        return "insufficient", 0.0, 0.0
    if alone / total > alone_ratio:
        return "standalone", 0.0, 0.0
    fr = (f.best_cnt / total) if (f and total) else 0.0
    pr = (p.best_cnt / total) if (p and total) else 0.0
    prefix_hit = f is not None and f.best_cnt >= min_evidence and fr > ratio
    suffix_hit = p is not None and p.best_cnt >= min_evidence and pr > ratio
    if prefix_hit and (not suffix_hit or fr >= pr):
        return "prefix_frag", fr, pr
    if suffix_hit:
        return "suffix_frag", fr, pr
    return "standalone", fr, pr


def main():
    parser = argparse.ArgumentParser(description="碎片词条识别（语料跟随度，只读）")
    parser.add_argument("--dict", default="output/system_dict.db")
    parser.add_argument("--corpus", default="tmp/corpus_50k_full")
    parser.add_argument("--out", default="tmp/fragments_candidates.tsv")
    parser.add_argument("--min-len", type=int, default=2)
    parser.add_argument("--max-len", type=int, default=4)
    parser.add_argument("--max-freq", type=int, default=3600,
                        help="只看词频低于此值的候选（高词频是常用词，不动）")
    parser.add_argument("--min-occ", type=int, default=3, help="语料出现次数下限")
    parser.add_argument("--min-evidence", type=int, default=3, help="跟随绝对次数下限")
    parser.add_argument("--ratio", type=float, default=0.9, help="跟随占比阈值")
    parser.add_argument("--alone-ratio", type=float, default=0.2,
                        help="独立出现占比高于此值直接判 standalone")
    args = parser.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit(f"语料目录不存在: {args.corpus}")

    t0 = time.time()
    rows, words = load_candidates(args.dict, args.min_len, args.max_len, args.max_freq)
    print(f"候选: {len(words)} 词 / {len(rows)} 条读法（freq < {args.max_freq}，"
          f"{args.min_len}~{args.max_len} 字）")
    vocab = load_full_vocab(args.dict)
    print(f"全词表: {len(vocab)} 词")

    total, alone, follow, precede = scan_corpus(args.corpus, words, args.max_len)

    stat = {}
    for i, w in enumerate(words):
        stat[w] = (total[i], alone[i], follow[i], precede[i])

    out_rows = []
    dist = Counter()
    for word, pinyin, freq in rows:
        t, a, f, p = stat[word]
        verdict, fr, pr = verdict_of(t, a, f, p, args.ratio, args.min_evidence,
                                     args.min_occ, args.alone_ratio)
        dist[verdict] += 1
        if verdict == "prefix_frag":
            long_word = word + (f.best_ch if f else "")
        elif verdict == "suffix_frag":
            long_word = (p.best_ch if p else "") + word
        else:
            long_word = ""
        in_dict = 1 if long_word and long_word in vocab else 0
        out_rows.append((
            word, pinyin, freq, t,
            f.best_ch if f else "", f"{fr:.3f}",
            p.best_ch if p else "", f"{pr:.3f}",
            verdict, in_dict, long_word, a,
        ))

    out_rows.sort(key=lambda r: (-r[3], r[0]))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("word\tpinyin\tfrequency\ttotal_occ\tfollow_char\tfollow_ratio"
                 "\tprecede_char\tprecede_ratio\tverdict\tlong_word_in_dict"
                 "\tlong_word\talone_occ\n")
        for r in out_rows:
            fh.write("\t".join(str(x) for x in r) + "\n")

    print(f"\n已写出 {args.out}（{len(out_rows)} 行）")
    print("判定分布:", dict(dist.most_common()))
    frag = dist["prefix_frag"] + dist["suffix_frag"]
    print(f"碎片合计: {frag}")
    print(f"耗时: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
