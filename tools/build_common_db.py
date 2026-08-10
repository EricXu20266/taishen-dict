#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
输出：common.db（SQLite 常用词库，P2 层）
═══════════════════════════════════════════════
解析 curate/common_dict.txt → output/common.db。

common 是人工维护的优先级表：行序 = 候选优先级（rank 越小越先出），
与 system/domains（词频排序）语义不同——引擎 P2 层按 rank 行序取词。

txt 格式（与 IME resources/common_dict.txt 一致）：
    pinyin<TAB>word         # 行序即优先级，Eric 可直接增删行

用法：
    python tools/build_common_db.py [txt_path] [db_path]
    # 默认 curate/common_dict.txt → output/common.db
"""

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(txt_path: str, db_path: str) -> int:
    if not os.path.isfile(txt_path):
        print(f"错误：{txt_path} 不存在")
        sys.exit(1)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS common_words;
        CREATE TABLE common_words (
            rank    INTEGER NOT NULL PRIMARY KEY,
            pinyin  TEXT NOT NULL,
            word    TEXT NOT NULL
        );
    """)

    count = 0
    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.lstrip("\ufeff").strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            pinyin = parts[0].strip().lower()
            word = parts[1].strip()
            if not pinyin or not word:
                continue
            conn.execute(
                "INSERT INTO common_words (rank, pinyin, word) VALUES (?,?,?)",
                (count, pinyin, word),
            )
            count += 1

    conn.commit()
    conn.close()

    db_size = os.path.getsize(db_path)
    print(f"  [output] {db_path}: {count} 条, {db_size/1024:.1f} KB")
    return count


if __name__ == "__main__":
    txt = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "curate", "common_dict.txt")
    db = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "output", "common.db")
    build(os.path.abspath(txt), os.path.abspath(db))
