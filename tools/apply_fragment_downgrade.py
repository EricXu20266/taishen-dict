#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""碎片词条降权（只降权不删除）。

把确认的碎片词条 frequency 降到 1：
  - 词条保留，用户翻页仍能取到，不会出现「打不出」
  - 候选排序里让出位置给正常词

输入：
  --candidates  analyze_fragments.py 产出的 TSV（取 verdict=prefix/suffix_frag）
  --keep        「语义上是完整词、不降权」的清单（每行一个词），可选
  --max-freq    只处理词频 <= 该值的词条（高词频词误判风险大，默认 1500）
  --min-occ     只处理语料出现次数 >= 该值的词条（证据量门槛，默认 10）
  --min-ratio   跟随占比下限（默认 0.97）

输出：
  tmp/fragments_downgraded.tsv —— word / pinyin / 原 frequency，回滚依据

用法：
    python tools/apply_fragment_downgrade.py --db output/system_dict.db \\
        --candidates tmp/fragments_candidates.tsv --keep tmp/fragments_keep.txt \\
        --report tmp/fragments_downgraded.tsv [--dry-run]
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time


def load_fragments(path: str, min_occ: int, min_ratio: float, max_freq: int,
                   min_len: int = 1) -> dict:
    """读候选清单 → {word: (pinyin, frequency, occ)}，按阈值过滤。"""
    out = {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < len(header):
                continue
            verdict = cols[idx["verdict"]]
            if verdict not in ("prefix_frag", "suffix_frag"):
                continue
            occ = int(cols[idx["total_occ"]])
            if occ < min_occ:
                continue
            ratio_key = "follow_ratio" if verdict == "prefix_frag" else "precede_ratio"
            if float(cols[idx[ratio_key]]) < min_ratio:
                continue
            freq = int(cols[idx["frequency"]])
            if freq > max_freq:
                continue
            word = cols[idx["word"]]
            if len(word) < min_len:
                continue
            out[word] = (cols[idx["pinyin"]], freq, occ)
    return out


def load_keep(path: str) -> set:
    if not path:
        return set()
    out = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            word = line.strip()
            if word and not word.startswith("#"):
                out.add(word)
    return out


def main():
    parser = argparse.ArgumentParser(description="碎片词条降权（freq → 1，保留词条）")
    parser.add_argument("--db", default="output/system_dict.db")
    parser.add_argument("--candidates", default="tmp/fragments_candidates.tsv")
    parser.add_argument("--keep", default=None)
    parser.add_argument("--report", default="tmp/fragments_downgraded.tsv")
    parser.add_argument("--min-occ", type=int, default=10)
    parser.add_argument("--min-ratio", type=float, default=0.97)
    parser.add_argument("--max-freq", type=int, default=1500)
    parser.add_argument("--min-len", type=int, default=3,
                        help="只处理该字数及以上的词（2 字碎片误判率最高，默认不动）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    frags = load_fragments(args.candidates, args.min_occ, args.min_ratio, args.max_freq,
                           args.min_len)
    keep = load_keep(args.keep)
    target = {w: v for w, v in frags.items() if w not in keep}
    print(f"候选（阈值过滤后）: {len(frags)}；保留（语义完整词）: {len(frags) - len(target)}；"
          f"待降权: {len(target)}")

    conn = sqlite3.connect(args.db)
    rows = []
    skipped = 0
    for word, (pinyin, _freq, _occ) in sorted(target.items()):
        hit = conn.execute(
            "SELECT id, frequency FROM system_dict WHERE word = ? AND pinyin = ?",
            (word, pinyin),
        ).fetchall()
        if not hit:
            # 拼音对不上时按 word 全部读法处理（同一词条的其它读法在候选表里另有行）
            hit = conn.execute(
                "SELECT id, frequency FROM system_dict WHERE word = ?", (word,)
            ).fetchall()
        for rid, old in hit:
            if old <= 1:
                skipped += 1
                continue
            rows.append((rid, word, pinyin, old))

    print(f"命中读法: {len(rows)} 条待更新，跳过（已是 1）: {skipped} 条")
    if not rows:
        print("无需变更")
        conn.close()
        return

    if args.dry_run:
        print("dry-run：不写库。样例：")
        for r in rows[:20]:
            print("  ", r[1], r[2], r[3], "-> 1")
        conn.close()
        return

    backup = f"{args.db}.bak-frag-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.db, backup)
    print(f"已备份: {backup}")

    os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="") as fh:
        fh.write("word\tpinyin\told_frequency\n")
        for _rid, word, pinyin, old in rows:
            fh.write(f"{word}\t{pinyin}\t{old}\n")

    conn.executemany("UPDATE system_dict SET frequency = 1 WHERE id = ?",
                     [(r[0],) for r in rows])
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM system_dict WHERE frequency = 1").fetchone()[0]
    conn.close()
    print(f"已降权 {len(rows)} 条读法；库内 freq=1 共 {left} 条")
    print(f"回滚依据: {args.report}")


if __name__ == "__main__":
    main()
