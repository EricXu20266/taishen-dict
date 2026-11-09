"""把现有词库拼音重注为「保留音节边界」格式（yu'an / xi'an）。

背景
----
V2026.11 之前，pypinyin 的音节列表被直接 join：`yu` + `an` → `yuan`，
`xi` + `an` → `xian`。跨音节词于是与同键单音节词挤在一个索引键里
（预案 vs 元、西安 vs 先），被词频更高的单字埋掉。

新格式用 `'` 保留边界。引擎 V0.6+ 加载时会去掉分隔符建索引（用户输入
`yuan` 仍能命中「预案」），因此本脚本**不改变任何候选行为**，只把
"读音边界"这一事实写回词库，供后续按边界做精确切分匹配。

安全策略
--------
- 默认 dry-run，不写库。
- 只有「重注结果去掉分隔符后与原拼音完全一致」的词才改（纯格式转换）。
- 读音不一致的词默认跳过并报告（`--force` 才覆盖，会改变读音，慎用）。
- `--apply` 写库前自动备份到同目录 `<db>.bak-YYYYmmdd-HHMMSS`。

用法
----
    python tools/reannotate_pinyin.py --db ../taishenIME/resources/system_dict.db
    python tools/reannotate_pinyin.py --db ../taishenIME/resources/system_dict.db --apply
    python tools/reannotate_pinyin.py --db ../taishenIME/resources/domains/domains.db --apply

注意：改完 SQLite 后必须删除同目录 `*.bin` 预编译索引，让引擎重建。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

try:
    from pypinyin import Style, pinyin
except ImportError:  # pragma: no cover
    sys.exit("需要 pypinyin：pip install pypinyin")

BATCH = 2000

# (表名, 取 pinyin 的列, 取 word 的列)[]
TABLES = {
    "system_dict": ("pinyin", "word"),
    "system_dict_trad": ("pinyin", "word"),
    "domain_words": ("pinyin", "word"),
    "domain_words_trad": ("pinyin", "word"),
}


def annotate(words: list[str]) -> dict[str, str]:
    """批量注音，返回 word → 带音节边界的拼音（如 yu'an）。"""
    out: dict[str, str] = {}
    for i in range(0, len(words), BATCH):
        chunk = words[i : i + BATCH]
        py = pinyin(chunk, style=Style.NORMAL, heteronym=False)
        idx = 0
        for word in chunk:
            n = len(word)
            syls = py[idx : idx + n]
            idx += n
            joined = "'".join(s[0] for s in syls if s)
            if joined and joined.replace("'", "").isascii():
                out[word] = joined.lower()
    return out


def tables_of(conn: sqlite3.Connection) -> list[str]:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [t for t in TABLES if t in names]


def plan(conn: sqlite3.Connection, table: str) -> tuple[list, list, int]:
    py_col, word_col = TABLES[table]
    rows = conn.execute(f"SELECT {word_col}, {py_col} FROM {table}").fetchall()
    words = sorted({w for w, _ in rows if w})
    fresh = annotate(words)

    convert, changed, already = [], [], 0
    for word, old_py in rows:
        new_py = fresh.get(word)
        if not new_py:
            continue
        if new_py == old_py:
            already += 1
            continue
        if new_py.replace("'", "") == old_py.replace("'", ""):
            convert.append((new_py, word, old_py))
        else:
            changed.append((word, old_py, new_py))
    return convert, changed, already


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite 词库路径")
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--force", action="store_true", help="连读音变化的词一起覆盖")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"词库不存在: {args.db}")

    conn = sqlite3.connect(args.db)
    tables = tables_of(conn)
    if not tables:
        sys.exit("没有找到可处理的词表")

    total_convert = 0
    total_changed = 0
    for table in tables:
        convert, changed, already = plan(conn, table)
        total_convert += len(convert)
        total_changed += len(changed)
        print(
            f"[{table}] 已是带边界 {already:,} | 仅格式转换 {len(convert):,} "
            f"| 读音变化(跳过) {len(changed):,}"
        )
        for word, old, new in changed[:5]:
            print(f"    读音变化: {word} {old} → {new}")

    print(f"\n合计：格式转换 {total_convert:,} 条，读音变化 {total_changed:,} 条")

    if not args.apply:
        print("dry-run：未写库。确认无误后加 --apply。")
        return

    backup = f"{args.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(args.db, backup)
    print(f"已备份: {backup}")

    written = 0
    for table in tables:
        convert, changed, _ = plan(conn, table)
        py_col, word_col = TABLES[table]
        upd = f"UPDATE {table} SET {py_col} = ? WHERE {word_col} = ?"
        conn.executemany(upd, [(new, word) for new, word, _ in convert])
        if args.force:
            conn.executemany(upd, [(new, word) for word, _, new in changed])
            written += len(convert) + len(changed)
        else:
            written += len(convert)
        conn.commit()
    print(f"已写回 {written:,} 条。")
    print("记得删除同目录 *.bin 预编译索引，让引擎按新库重建。")


if __name__ == "__main__":
    main()
