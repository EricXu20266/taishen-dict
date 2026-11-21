#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把外部语言模型的「搭配强度」导入为 context_prior 的增量来源。

设计原则（四道闸门，任何一条不通过都丢弃）：
  1. 词表闸门：prev / next 必须都在 system_dict 里 —— 不引入任何外部词条。
  2. 形态闸门：沿用 build_context_prior.py 的质量口径
     （STOP_WORDS 前词、双单字对、前词单字低频、WEAK_NEXT 后词）。
  3. 强度闸门：lift = log10 P(next|prev) − log10 P(next) ≥ --min-lift，
     且 next 的一元概率 ≥ --min-uni（剔除生僻词造出的虚高 lift）。
  4. 增量闸门：只补现有 context_prior 没有的对，已有证据不覆盖、不改档。

产出：`context_prior(prev, next, count, source)`，档位 2-4（保留 5-6 档给自有语料），
source 列供审计。合并到生产 bigram.db 由调用方另行执行。

用法：
    python tools/import_prior_ext.py \
        --lm <外部语言模型文件> --words <词→ID 映射 json> \
        --dict output/system_dict.db --existing output/bigram.db \
        --out output/context_prior_ext.db
"""
import argparse
import json
import os
import sqlite3
import struct
from collections import defaultdict

STOP_WORDS = {
    "的", "了", "是", "在", "和", "与", "就", "都", "而", "及", "或",
    "也", "还", "又", "很", "更", "最", "被", "把", "为", "以", "对",
    "从", "到", "上", "下", "中", "个", "这", "那", "有", "没有",
}
WEAK_NEXT = {
    "的", "了", "是", "在", "和", "地", "得", "着", "过", "等",
    "与", "也", "而", "就", "至", "为", "被", "把", "或", "及",
    "不", "有", "之", "皆", "其", "亦", "且", "则", "者", "所",
    "矣", "焉", "乎", "已", "于", "此", "各", "该", "并",
}
SINGLE_PREV_MIN_FREQ = 3600


def load_lm(lm_path, words_path):
    buf = open(lm_path, "rb").read()
    seg_a = buf[44:2641384]
    seg_b = buf[2641384:16393120]
    n = len(seg_a) // 5 - 1
    start, uidx = [], []
    for k in range(n + 1):
        v = int.from_bytes(seg_a[k * 5:k * 5 + 5], "little")
        start.append(v & 0x7FFFFF)
        if k < n:
            uidx.append((v >> 23) & 0xFF)
    toc = 0xFA23A0
    blocks = {}
    p = toc
    while p + 8 <= len(buf):
        t, ln = struct.unpack_from("<ii", buf, p)
        if ln < 0 or p + 8 + ln > len(buf):
            break
        blocks[t] = buf[p + 8:p + 8 + ln]
        p += 8 + ln
    uni_tab = struct.unpack_from("<256f", blocks[12294], 4)
    bg_tab = struct.unpack_from("<16f", blocks[12294], 4 + 1024 + 4)
    uni = [uni_tab[x] for x in uidx]
    words = {int(k): v for k, v in json.load(open(words_path, encoding="utf-8")).items()}
    return seg_b, n, start, uni, bg_tab, words


def load_vocab(dict_path):
    con = sqlite3.connect(dict_path)
    freq = {}
    for w, f in con.execute("select word, max(frequency) from system_dict group by word"):
        freq[w] = f
    return freq


def tier_of(lift, weak_next):
    """外部来源只补 2-3 档：4 档以上保留给自有语料，避免外来统计压过主证据。"""
    if weak_next:
        return 2
    if lift >= 2.5:
        return 3
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True, help="外部语言模型文件")
    ap.add_argument("--words", required=True, help="词→ID 映射 JSON")
    ap.add_argument("--dict", required=True, help="system_dict.db")
    ap.add_argument("--existing", help="现有 bigram.db（增量闸门用）")
    ap.add_argument("--out", required=True, help="输出增量库")
    ap.add_argument("--min-lift", type=float, default=1.5)
    ap.add_argument("--min-uni", type=float, default=-6.0)
    ap.add_argument("--per-prev", type=int, default=8)
    ap.add_argument("--top", type=int, default=500000)
    args = ap.parse_args()

    freq = load_vocab(args.dict)
    existing = set()
    if args.existing and os.path.exists(args.existing):
        con = sqlite3.connect(args.existing)
        existing = {(a, b) for a, b in con.execute("select prev, next from context_prior")}
    print(f"词表 {len(freq)} 词，现有搭配 {len(existing)} 对")

    seg_b, n, start, uni, bg_tab, words = load_lm(args.lm, args.words)
    print(f"语言模型 {n} 词")

    gates = defaultdict(int)
    per_prev = defaultdict(list)
    total = 0
    for pid in range(n):
        pw = words.get(pid)
        if pw is None:
            continue
        for k in range(start[pid], start[pid + 1]):
            v = seg_b[k * 3] | (seg_b[k * 3 + 1] << 8) | (seg_b[k * 3 + 2] << 16)
            cid = v & 0xFFFFF
            cw = words.get(cid)
            if cw is None:
                continue
            total += 1
            if pw not in freq or cw not in freq:
                gates["not_in_vocab"] += 1
                continue
            if pw in STOP_WORDS:
                gates["prev_stop"] += 1
                continue
            if len(pw) == 1 and len(cw) == 1:
                gates["double_single"] += 1
                continue
            if len(pw) == 1 and freq[pw] < SINGLE_PREV_MIN_FREQ:
                gates["single_prev_low_freq"] += 1
                continue
            if cw in WEAK_NEXT:
                # 虚词后词保留（拆组排序有用），但降档，不占配额
                weak = True
            else:
                weak = False
                if len(cw) == 1:
                    # 后词单字且非虚词：抽样显示碎片集中在这一形态，整对剔除
                    gates["single_next"] += 1
                    continue
            if (pw, cw) in existing:
                gates["already_existing"] += 1
                continue
            lift = bg_tab[(seg_b[k * 3 + 2] >> 4)] - uni[cid]
            if lift < args.min_lift or uni[cid] < args.min_uni:
                gates["low_strength"] += 1
                continue
            per_prev[pw].append((lift, cw, tier_of(lift, weak)))

    rows = []
    for pw, items in per_prev.items():
        # 同一个词可能对应多个模型 ID，按 (prev,next) 取最强证据，避免主键冲突
        best = {}
        for lift, cw, tier in items:
            if cw not in best or lift > best[cw][0]:
                best[cw] = (lift, tier)
        merged = sorted(((l, w, t) for w, (l, t) in best.items()), reverse=True)
        for lift, cw, tier in merged[: args.per_prev]:
            rows.append((pw, cw, tier))
    rows.sort(key=lambda r: (r[0], r[1]))
    if len(rows) > args.top:
        rows = rows[: args.top]

    if os.path.exists(args.out):
        os.remove(args.out)
    con = sqlite3.connect(args.out)
    con.executescript("""
        CREATE TABLE context_prior (
            prev TEXT NOT NULL, next TEXT NOT NULL,
            count INTEGER NOT NULL, source TEXT NOT NULL,
            PRIMARY KEY (prev, next));
    """)
    con.executemany("INSERT INTO context_prior VALUES(?,?,?, 'ext_lm')", rows)
    con.commit()
    dist = con.execute("select count, count(*) from context_prior group by count").fetchall()
    prevs = con.execute("select count(distinct prev) from context_prior").fetchone()[0]
    con.close()

    print("\n闸门淘汰:", dict(gates))
    print(f"导出 {len(rows)} 对，覆盖前词 {prevs} 个，档位分布 {sorted(dist)}")
    print(f"输出: {args.out}（{os.path.getsize(args.out) / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
