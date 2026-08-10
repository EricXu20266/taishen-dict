#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
泰深词库构建管线 — 主编排
═══════════════════════════

一条命令构建完整词库：

    python pipeline.py

流程：
  采集（jieba + wiki）→ 清洗 → 注音 → 频次调校（boost/demote）
  → 对数压缩 → 输出（system_dict.db + domains/*.txt）

配置：
  domains.yaml   — 领域 → 维基百科分类映射
  curate/boost.yaml  — 打字高频词加成
  curate/demote.yaml — 新闻虚高词降权

依赖：
  pip install pyyaml pypinyin zhconv
"""

import math
import os
import sys

import yaml
from zhconv import convert

from sources import jieba, wiki
from output import sqlite, domains as out_domains, domains_db

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "output")
CACHE_DIR = os.path.join(ROOT, "tmp")
MAX_FREQ = 5000


def load_curation(path: str) -> dict[str, float]:
    """加载 YAML 调校表 → {word: multiplier_or_divisor}。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    result: dict[str, float] = {}
    for _category, items in data.items():
        if not isinstance(items, dict):
            continue
        for word, val in items.items():
            result[str(word)] = float(val)
    return result


def load_domains_config() -> dict[str, dict]:
    """加载 domains.yaml → {name: {wiki_categories, max_depth}}。"""
    path = os.path.join(ROOT, "domains.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("domains", {})


def compress_freq(raw_freq: int, max_raw: int) -> int:
    """对数压缩 → 1~MAX_FREQ。"""
    if raw_freq <= 0:
        return 1
    v = int(MAX_FREQ * math.log10(raw_freq) / math.log10(max_raw))
    return max(1, min(MAX_FREQ, v))


# ─── V0.5.5 简繁归一化（源头治理：构建时转简体，不靠引擎运行时转换）───

def is_garbage_char(c: str) -> bool:
    """乱码字符判定：CJK 区、zhconv 简繁都不认、且不在 GB2312 简体字集。

    覆盖 UTF-8→GBK→Unicode mojibake（紝/鐨/剉 类）中 zhconv 无法还原的字。
    zhconv 能映射的乱码字（紝→纴、鏈→链）会先转成合法简体，不在此判定。
    代价：zhconv 不认识的合法生僻字（姞 等）会被过滤——价值低（打不出/垫底），可接受。
    """
    if ord(c) < 0x4E00:
        return False
    if convert(c, "zh-cn") != c:
        return False  # 繁体/异体，可转换
    if convert(c, "zh-tw") != c:
        return False  # zhconv 认识（简体/简繁同形）
    try:
        c.encode("gb2312")
        return False  # 在 GB2312 简体字集
    except UnicodeEncodeError:
        return True


def split_simp_trad(
    entries: list[tuple[str, str, int]],
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
    """简繁分集：按"含繁体独有字"拆分（V0.5.6）。

    entries: [(word, pinyin, freq)] → (简体列表, 繁体原文列表)。
    繁体词条（我們/側視）保留原文进 trad 表，不转简丢弃（简繁双体需要）；
    乱码词条（zhconv 不认 + 不在 GB2312）过滤丢弃。
    """
    simp: list[tuple[str, str, int]] = []
    trad: list[tuple[str, str, int]] = []
    for word, pinyin, freq in entries:
        if any(is_garbage_char(c) for c in word):
            continue  # 乱码词条丢弃
        if _has_trad_char(word):
            trad.append((word, pinyin, freq))
        else:
            simp.append((word, pinyin, freq))
    return simp, trad


def _has_trad_char(word: str) -> bool:
    """词含繁体独有字（zhconv 转简变化 + 有可识别繁体）"""
    if convert(word, "zh-cn") == word:
        return False
    return any(ord(c) >= 0x4E00 and convert(c, "zh-cn") != c for c in word)


def simplify_entries(
    entries: list[tuple[str, str, int]],
) -> list[tuple[str, str, int]]:
    """繁→简归一化 + 乱码过滤 + 同词去重（保留最高频）。

    entries: [(word, pinyin, freq)] → 同上（word 已转简体）。
    繁体词条（我們→我们）与已有简体词条合并，保留频次高的；
    转简后仍含乱码字符的词条丢弃。
    """
    out: dict[str, tuple[str, int]] = {}
    for word, pinyin, freq in entries:
        w = convert(word, "zh-cn")
        if any(is_garbage_char(c) for c in w):
            continue  # 乱码词条丢弃
        if w in out:
            if freq > out[w][1]:
                out[w] = (pinyin, freq)
        else:
            out[w] = (pinyin, freq)
    return [(w, py, f) for w, (py, f) in out.items()]


def main():
    print("=" * 60)
    print("  泰深词库构建管线")
    print("=" * 60)

    # ── 1. 采集 jieba ──
    print("\n── 1/5 采集 jieba（MIT）──")
    os.makedirs(CACHE_DIR, exist_ok=True)
    jieba_entries = jieba.collect(CACHE_DIR)
    print(f"  jieba 总计: {len(jieba_entries):,} 条")

    # ── 2. 采集 Wikipedia ──
    print("\n── 2/5 采集 Wikipedia 领域词条（CC BY-SA）──")
    domains_cfg = load_domains_config()
    domain_entries: dict[str, list[tuple[str, str]]] = {}
    all_wiki: dict[str, str] = {}  # word → pinyin（Wiki 拼音）

    for name, cfg in domains_cfg.items():
        cats = cfg.get("wiki_categories", [])
        depth = cfg.get("max_depth", 2)
        entries = wiki.collect_domain(name, cats, max_depth=depth)
        entry_list: list[tuple[str, str]] = []
        for w, py, d in entries:
            entry_list.append((w, py))
            all_wiki[w] = py
        domain_entries[name] = entry_list

    total_wiki = sum(len(v) for v in domain_entries.values())
    print(f"\n  Wikipedia 总计: {total_wiki:,} 条（{len(domain_entries)} 领域）")

    # ── 3. 加载频次调校表 ──
    print("\n── 3/5 加载频次调校表 ──")
    boost = load_curation(os.path.join(ROOT, "curate", "boost.yaml"))
    demote = load_curation(os.path.join(ROOT, "curate", "demote.yaml"))
    print(f"  boost: {len(boost)} 词, demote: {len(demote)} 词")

    # ── 4. 融合 ──
    print("\n── 4/5 融合去重 ──")
    # jieba 为主骨架：word → (pinyin, freq)
    merged: dict[str, tuple[str, int]] = {}
    for word, pinyin, freq in jieba_entries:
        merged[word] = (pinyin, freq)

    # Wikipedia 补充（只在 jieba 无此词时补充，拼音用 pypinyin 注的）
    wiki_added = 0
    for word, pinyin in all_wiki.items():
        if word not in merged:
            merged[word] = (pinyin, 1)
            wiki_added += 1
    print(f"  jieba 基底: {len(jieba_entries):,}, Wiki 补充: {wiki_added:,}, 合并: {len(merged):,}")

    # ── 4.1 简繁分集（V0.5.6，Eric 决策：简繁隔离不转简丢弃）──
    # 繁体词条（我們/側視，源自 jieba 词典 + 维基）保留原文进 system_dict_trad 表；
    # 简体词条进 system_dict 表；乱码词条过滤。引擎繁体模式优先查 trad 表。
    print("\n── 4.1/5 简繁分集 ──")
    merged_list = [(w, py, f) for w, (py, f) in merged.items()]
    simp_list, trad_list = split_simp_trad(merged_list)
    merged = {w: (py, f) for w, py, f in simp_list}
    print(f"  简体: {len(simp_list):,}, 繁体(原文保留): {len(trad_list):,}, 乱码过滤: {len(merged_list) - len(simp_list) - len(trad_list):,}")

    # 应用 boost/demote
    max_raw = max(f for _, f in merged.values()) if merged else 1
    for word, multiplier in boost.items():
        if word in merged:
            py, freq = merged[word]
            merged[word] = (py, int(freq * multiplier))
    for word, divisor in demote.items():
        if word in merged:
            py, freq = merged[word]
            merged[word] = (py, int(freq / divisor))

    # 对数压缩 → 1~MAX_FREQ
    entries_out: list[tuple[str, str, int]] = []
    max_adj = max(f for _, f in merged.values()) if merged else 1
    for word, (pinyin, freq) in merged.items():
        compressed = compress_freq(freq, max_adj)
        entries_out.append((word, pinyin, compressed))

    # 按频次降序
    entries_out.sort(key=lambda x: x[2], reverse=True)
    print(f"  最终词条: {len(entries_out):,}（频次范围 1~{MAX_FREQ}）")

    # ── 5. 输出 ──
    print("\n── 5/5 输出 ──")
    os.makedirs(OUT_DIR, exist_ok=True)

    db_path = os.path.join(OUT_DIR, "system_dict.db")
    # V0.5.6 简繁分集：简体表 + 繁体原文表
    trad_out = [(w, py, f) for w, py, f in trad_list]
    sqlite.write(entries_out, db_path, trad_out)

    # ── 4.2 领域词乱码过滤（V0.5.6 简繁分集）──
    # 繁体词条保留原文（简繁双体需要），domains_db.py 输出时自动分集
    # （domain_words 简体 + domain_words_trad 繁体原文）。
    print("\n── 4.2/5 领域词乱码过滤 ──")
    dropped_domains = 0
    for name, entry_list in domain_entries.items():
        cleaned: list[tuple[str, str]] = []
        seen = set()
        for w, py in entry_list:
            if any(is_garbage_char(c) for c in w):
                dropped_domains += 1
                continue
            key = (w, py)
            if key not in seen:
                seen.add(key)
                cleaned.append((w, py))
        domain_entries[name] = cleaned
    print(f"  乱码丢弃: {dropped_domains:,}（繁体原文保留，输出时分集）")

    out_domains.write(domain_entries, os.path.join(OUT_DIR, "domains"))

    # ── 5.5 domains.db（V0.5+）──
    print("\n── 5.5/5 domains.db ──")
    domains_db.write(os.path.join(OUT_DIR, "domains"))

    # ── 5.7 common.db（V0.5.7+，P2 层常用词库）──
    # common 是人工优先级表（curate/common_dict.txt，行序即优先级），
    # 不参与 jieba/wiki 融合，仅做 txt → db 转换。
    print("\n── 5.7/6 common.db ──")
    from tools.build_common_db import build as build_common

    build_common(os.path.join(ROOT, "curate", "common_dict.txt"),
                 os.path.join(OUT_DIR, "common.db"))

    # ── 6. 校验阀门（V0.5.6 防简繁分集回归）──
    # 断言双表存在 + 简体表无繁体独有字；失败则中止构建，
    # 避免产出"混在一起"的单表词库被复制进 IME。
    print("\n── 6/6 校验 ──")
    from tools.verify_build import main as verify_build

    if not verify_build():
        sys.exit("校验失败：词库简繁分集不完整，构建中止（详见上方报告）")

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("  构建完成")
    print(f"  系统词库: {db_path}  ({len(entries_out):,} 条)")
    print(f"  领域词库: {OUT_DIR}/domains/  ({len(domain_entries)} 个领域)")
    print(f"  领域 DB:  {OUT_DIR}/domains/domains.db")
    print(f"  常用 DB:  {OUT_DIR}/common.db")
    print("=" * 60)


if __name__ == "__main__":
    main()
