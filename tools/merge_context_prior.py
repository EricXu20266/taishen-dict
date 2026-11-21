#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 context_prior 多来源：同一 (prev,next) 取档位较大者。

用法：
    python tools/merge_context_prior.py --base output/bigram.db \
        --extra output/context_prior_ext.db --out output/bigram.db
"""
import argparse
import os
import sqlite3


def read(path):
    con = sqlite3.connect(path)
    rows = con.execute("select prev, next, count from context_prior").fetchall()
    con.close()
    return {(a, b): c for a, b, c in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--extra", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    base = read(args.base)
    extra = read(args.extra)
    merged = dict(base)
    added = upgraded = 0
    for pair, tier in extra.items():
        cur = merged.get(pair)
        if cur is None:
            merged[pair] = tier
            added += 1
        elif tier > cur:
            merged[pair] = tier
            upgraded += 1

    tmp = args.out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE context_prior (
            prev TEXT NOT NULL, next TEXT NOT NULL,
            count INTEGER NOT NULL, PRIMARY KEY (prev, next));
    """)
    con.executemany(
        "INSERT INTO context_prior VALUES(?,?,?)",
        [(a, b, c) for (a, b), c in merged.items()],
    )
    con.commit()
    dist = con.execute("select count, count(*) from context_prior group by count").fetchall()
    con.close()
    os.replace(tmp, args.out)

    print(f"base {len(base)} + extra {len(extra)} -> {len(merged)}")
    print(f"新增 {added}，升档 {upgraded}")
    print(f"档位分布 {sorted(dist)}")
    print(f"输出 {args.out}（{os.path.getsize(args.out) / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
