#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长词补入词库（第 5 轮：碎片还原出来的长词）。

读 curate/longwords.txt（词\t拼音\t频次），把库里没有的词补进 system_dict。
已存在（同词同拼音）则跳过，不覆盖原词频。

频次口径：按语料出现次数分档（100/800/600/500），刻意低于 5~7 字专名的常见档位
（1500-3000），避免长词在同音竞争里抢短词的首位。

用法：
    python tools/add_longwords.py --db output/system_dict.db \\
        --list curate/longwords.txt --report tmp/longwords_added.tsv [--dry-run]
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time


def load_list(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            word, pinyin = parts[0], parts[1]
            try:
                freq = int(parts[2])
            except ValueError:
                continue
            out.append((word, pinyin, freq))
    return out


def main():
    parser = argparse.ArgumentParser(description="长词补入 system_dict")
    parser.add_argument("--db", default="output/system_dict.db")
    parser.add_argument("--list", default="curate/longwords.txt")
    parser.add_argument("--report", default="tmp/longwords_added.tsv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = load_list(args.list)
    print(f"清单: {len(entries)} 词")

    conn = sqlite3.connect(args.db)
    added = []
    skipped = 0
    for word, pinyin, freq in entries:
        hit = conn.execute(
            "SELECT 1 FROM system_dict WHERE word = ? AND pinyin = ?", (word, pinyin)
        ).fetchone()
        if hit:
            skipped += 1
            continue
        added.append((word, pinyin, freq))

    print(f"待补: {len(added)}，已存在（跳过）: {skipped}")
    if not added:
        conn.close()
        print("无需变更")
        return

    if args.dry_run:
        print("dry-run：不写库。样例：")
        for row in added[:15]:
            print("  ", row)
        conn.close()
        return

    backup = f"{args.db}.bak-longword-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.db, backup)
    print(f"已备份: {backup}")

    conn.executemany(
        "INSERT INTO system_dict (word, pinyin, frequency) VALUES (?, ?, ?)", added
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM system_dict").fetchone()[0]
    conn.close()

    os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="") as fh:
        fh.write("word\tpinyin\tfrequency\n")
        for word, pinyin, freq in added:
            fh.write(f"{word}\t{pinyin}\t{freq}\n")

    print(f"已补入 {len(added)} 词；库内词条共 {total} 条")
    print(f"补词清单: {args.report}")


if __name__ == "__main__":
    main()
