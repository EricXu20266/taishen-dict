#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
输出：domains.db（SQLite 领域词库，引擎 V0.5+ 优先加载）
══════════════════════════════════════════════════════
从 output/domains/*.txt 扫描生成单一 domains.db。
"""

import os
import sqlite3


def write(out_dir: str, db_path: str | None = None) -> str:
    """
    扫描 out_dir 下所有 *.txt → 写入 domains.db。
    返回生成的文件路径。
    """
    if db_path is None:
        db_path = os.path.join(out_dir, "domains.db")

    txt_files = sorted(
        f for f in os.listdir(out_dir)
        if f.endswith(".txt")
    )
    if not txt_files:
        print("  [output] domains.db: 无 txt 文件，跳过")
        return db_path

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        DROP TABLE IF EXISTS domain_words;
        CREATE TABLE domain_words (
            word        TEXT NOT NULL,
            pinyin      TEXT NOT NULL,
            domain_id   INTEGER NOT NULL,
            domain_name TEXT NOT NULL,
            PRIMARY KEY (word, domain_id)
        );
        CREATE INDEX IF NOT EXISTS idx_dw_pinyin ON domain_words(pinyin);
        CREATE INDEX IF NOT EXISTS idx_dw_domain ON domain_words(domain_id);
    """)

    total = 0
    for domain_id, fn in enumerate(txt_files):
        path = os.path.join(out_dir, fn)
        domain_name = fn.rsplit(".", 1)[0]
        count = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                word, pinyin = parts[0], parts[1]
                if not pinyin or not pinyin.isascii() or not pinyin.islower():
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO domain_words VALUES (?,?,?,?)",
                    (word, pinyin, domain_id, domain_name),
                )
                count += 1
        conn.commit()
        total += count
        print(f"  [output] domains.db: {fn} → {count} 词")

    conn.commit()
    conn.close()

    size = os.path.getsize(db_path)
    print(
        f"  [output] {db_path}: {len(txt_files)} 领域, {total} 词, "
        f"{size / 1024 / 1024:.1f} MB"
    )
    return db_path
