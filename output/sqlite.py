#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出：system_dict.db（SQLite 系统词库）。"""

import os
import sqlite3


def write(
    entries: list[tuple[str, str, int]],
    out_path: str,
) -> None:
    """
    将 (word, pinyin, freq) 三元组写入 SQLite 词库。

    表结构：
        system_dict(id INTEGER PK, pinyin TEXT, word TEXT, frequency INTEGER)
    索引：pinyin
    """
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    conn = sqlite3.connect(out_path)
    conn.execute("DROP TABLE IF EXISTS system_dict")
    conn.execute("""
        CREATE TABLE system_dict (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pinyin TEXT NOT NULL,
            word TEXT NOT NULL,
            frequency INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX idx_pinyin ON system_dict(pinyin)")

    count = 0
    for word, pinyin, freq in entries:
        conn.execute(
            "INSERT INTO system_dict (pinyin, word, frequency) VALUES (?, ?, ?)",
            (pinyin, word, freq),
        )
        count += 1

    conn.commit()
    conn.close()

    size = os.path.getsize(out_path)
    print(f"  [output] {out_path}: {count:,} 条, {size:,} bytes")
