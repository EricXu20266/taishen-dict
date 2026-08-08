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
"""

import math
import os
import sys

import yaml

from sources import jieba, wiki
from output import sqlite, domains as out_domains

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
    sqlite.write(entries_out, db_path)

    out_domains.write(domain_entries, os.path.join(OUT_DIR, "domains"))

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("  构建完成")
    print(f"  系统词库: {db_path}  ({len(entries_out):,} 条)")
    print(f"  领域词库: {OUT_DIR}/domains/  ({len(domain_entries)} 个领域)")
    print("=" * 60)


if __name__ == "__main__":
    main()
