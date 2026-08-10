#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建后校验阀门 — 简繁分集完整性断言（V0.5.6 防回归）
════════════════════════════════════════════════════════════

pipeline.py 第 6 步自动调用；也可独立运行：

    python tools/verify_build.py

校验内容：
  1. system_dict.db 必须存在 system_dict + system_dict_trad 双表
  2. domains.db 必须存在 domain_words + domain_words_trad 双表
  3. 简体表（system_dict / domain_words）逐字符扫描：
     不允许出现任何繁体独有字（zhconv 判定），否则列出样例词
  4. 繁体表（system_dict_trad / domain_words_trad）非空，
     且词条应含繁体独有字（防简体全量误入 trad 表）

退出码：0 = 通过，1 = 失败（pipeline 据此中止构建）。
判定标准与 pipeline.split_simp_trad / domains_db._has_trad_char 一致
（_has_trad_char 为唯一事实源，三处保持一致）。
"""

import os
import sqlite3
import sys

from zhconv import convert

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAD_SAMPLE_LIMIT = 2000  # trad 表逐词抽查上限（全量太慢，抽样足够防全量误入）
# 简体表"疑似繁体独有字"覆盖词条数的 WARN/FAIL 阈值。
# zhconv 是机械字面映射，会误判简繁同形多音字（乾 qián/徵 人名/於 古字），
# 这些字大多在 GB2312 简体字集（1980 规范底线），已过滤；
# 剩余零星边界字（瞭望/矇眬/生僻人名）为 GB2312 未收的规范保留字，
# 词条数低于阈值 → WARN 提示人工核，不阻断；高于阈值 → 大规模混入，阻断构建。
WARN_THRESHOLD = 100


def _in_gb2312(c: str) -> bool:
    """GB2312 简体字集（规范化简体的务实底线）。"""
    try:
        c.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def _has_trad_char(word: str) -> bool:
    """词含繁体独有字（与 pipeline.py / domains_db.py 同源判定）。"""
    if convert(word, "zh-cn") == word:
        return False
    return any(ord(c) >= 0x4E00 and convert(c, "zh-cn") != c for c in word)


def _suspect_trad_chars_in(chars: set[str]) -> set[str]:
    """从字符集筛出"疑似繁体独有字"：zhconv 判繁体 && 不在 GB2312。
    排除 乾/徵/於 等 GB2312 内简繁同形字——它们在简体中是合法字。"""
    return {c for c in chars
            if ord(c) >= 0x4E00 and _has_trad_char(c) and not _in_gb2312(c)}


def _collect_chars(conn: sqlite3.Connection, table: str) -> set[str]:
    chars: set[str] = set()
    for (word,) in conn.execute(f"SELECT word FROM {table}"):
        chars.update(word)
    return chars


def _find_words(conn: sqlite3.Connection, table: str, trad_chars: set[str], limit: int = 5) -> list[str]:
    out: list[str] = []
    for (word,) in conn.execute(f"SELECT word FROM {table}"):
        if any(c in trad_chars for c in word):
            out.append(word)
            if len(out) >= limit:
                break
    return out


def _count_words_with(conn: sqlite3.Connection, table: str, trad_chars: set[str]) -> int:
    n = 0
    for (word,) in conn.execute(f"SELECT word FROM {table}"):
        if any(c in trad_chars for c in word):
            n += 1
    return n


def _check_simp_table(conn: sqlite3.Connection, table: str, label: str) -> bool:
    chars = _collect_chars(conn, table)
    suspect = _suspect_trad_chars_in(chars)
    if not suspect:
        print(f"  [OK]   {label}: 无繁体独有字（{len(chars):,} 唯一字符）")
        return True
    n = _count_words_with(conn, table, suspect)
    if n >= WARN_THRESHOLD:
        print(f"  [FAIL] {label}: 疑似繁体独有字 {len(suspect)} 个覆盖 {n:,} 词条——简繁分集失效，构建中止")
        for c in sorted(suspect)[:20]:
            print(f"         {c} -> {convert(c, 'zh-cn')}")
        for w in _find_words(conn, table, suspect):
            print(f"         样例词: {w}")
        return False
    print(f"  [WARN] {label}: {n} 词条含疑似繁体独有字 {len(suspect)} 个"
          f"（{sorted(suspect)[:10]}）——GB2312 未收边界字/生僻人名，不阻断，建议人工核")
    for w in _find_words(conn, table, suspect):
        print(f"         样例词: {w}")
    return True


def _check_trad_table(conn: sqlite3.Connection, table: str, label: str) -> bool:
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if total == 0:
        print(f"  [FAIL] {label}: 空表——简繁分集未生效")
        return False
    bad = 0
    for (word,) in conn.execute(f"SELECT word FROM {table} LIMIT {TRAD_SAMPLE_LIMIT}"):
        if not _has_trad_char(word):
            bad += 1
    if bad:
        print(f"  [WARN] {label}: 前 {TRAD_SAMPLE_LIMIT} 条中 {bad} 条无繁体独有字（可能混入简体）")
    else:
        print(f"  [OK]   {label}: 抽查 {TRAD_SAMPLE_LIMIT} 条全含繁体独有字（共 {total:,} 条）")
    return True


def _check_db(path: str, need: list[str]) -> bool:
    if not os.path.exists(path):
        print(f"  [FAIL] 缺失产物: {path}")
        return False
    conn = sqlite3.connect(path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in need:
            if t not in have:
                print(f"  [FAIL] {path}: 缺表 {t}")
                return False
        ok = True
        ok &= _check_simp_table(conn, need[0], f"{os.path.basename(path)}::{need[0]}")
        ok &= _check_trad_table(conn, need[1], f"{os.path.basename(path)}::{need[1]}")
        return ok
    finally:
        conn.close()


def _check_common_db() -> bool:
    """common.db 校验：rank 连续 + 与源 txt 条数对账（防 txt 改了 db 没重建）。"""
    db_path = os.path.join(ROOT, "output", "common.db")
    txt_path = os.path.join(ROOT, "curate", "common_dict.txt")
    if not os.path.exists(db_path):
        print(f"  [FAIL] 缺失产物: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "common_words" not in have:
            print(f"  [FAIL] {db_path}: 缺表 common_words")
            return False
        n = conn.execute("SELECT COUNT(*) FROM common_words").fetchone()[0]
        if n == 0:
            print("  [FAIL] common.db::common_words: 空表")
            return False
        # rank 必须 0..n-1 连续（行序优先级完整性；rank 为 PK 天然无重复）
        mn, mx = conn.execute(
            "SELECT MIN(rank), MAX(rank) FROM common_words").fetchone()
        if mn != 0 or mx != n - 1:
            print(f"  [FAIL] common.db::common_words: rank 不连续"
                  f"（min={mn} max={mx} 期望 0..{n - 1}）——优先级行序被破坏")
            return False
        # 与源 txt 条数对账
        txt_n = 0
        with open(txt_path, encoding="utf-8") as f:
            for line in f:
                line = line.lstrip("\ufeff").strip()
                if line and not line.startswith("#") and "\t" in line:
                    txt_n += 1
        if txt_n != n:
            print(f"  [FAIL] common.db::common_words: {n} 条 vs 源 txt {txt_n} 条——不一致，需重建")
            return False
        print(f"  [OK]   common.db::common_words: {n} 条, rank 0..{n-1} 连续, 与源 txt 对账一致")
        return True
    finally:
        conn.close()


def main() -> bool:
    print("══ 词库校验 ══")
    ok = True
    ok &= _check_db(os.path.join(ROOT, "output", "system_dict.db"),
                    ["system_dict", "system_dict_trad"])
    ok &= _check_db(os.path.join(ROOT, "output", "domains", "domains.db"),
                    ["domain_words", "domain_words_trad"])
    ok &= _check_common_db()
    print("══ " + ("校验通过" if ok else "校验失败") + " ══")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
