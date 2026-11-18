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

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import yaml
from zhconv import convert

from sources import jieba, wiki
from output import sqlite, domains as out_domains, domains_db
from tools.versioning import next_version

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


# ─── 词库版本控制（V0.5.7+）───

def _sha256(path: str) -> str:
    """文件 SHA256（分块，防大文件占内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_counts(db_path: str) -> dict[str, int]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    out = {r[0]: conn.execute(f"SELECT COUNT(*) FROM {r[0]}").fetchone()[0]
           for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
           if not r[0].startswith("sqlite")}
    conn.close()
    return out


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=ROOT, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def write_version() -> str:
    """生成 VERSION.json（版本号 + git commit + 各词库 sha256/条数）。"""
    from datetime import datetime

    version = next_version()
    manifest = {
        "version": version,
        "built_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": _git_commit(),
        "dicts": {},
    }
    for rel in ("system_dict.db", "domains/domains.db", "common.db", "bigram.db"):
        path = os.path.join(OUT_DIR, rel)
        if not os.path.exists(path):
            continue
        entry = {"sha256": _sha256(path), "size": os.path.getsize(path)}
        counts = _table_counts(path)
        if counts:
            entry["rows"] = counts
        manifest["dicts"][rel] = entry

    path = os.path.join(OUT_DIR, "VERSION.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  [output] {path}: {version}（git {manifest['git_commit']}）")
    return version


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

    reuse_wiki = "--reuse-wiki" in sys.argv
    if reuse_wiki:
        # 复用上次构建的 output/domains/*.txt（格式：词 拼音），跳过网络采集。
        # 用途：只改了 curate/ 层、不需要重新采集 wiki 时的快速重建。
        print("  [复用] 读 output/domains/*.txt，跳过 wiki 网络采集")
        dom_dir = os.path.join(OUT_DIR, "domains")
        wiki_names = set(domains_cfg.keys())  # 只取 domains.yaml 定义的 21 个 wiki 域
        for fn in sorted(os.listdir(dom_dir)):
            if not fn.endswith(".txt"):
                continue
            name = fn[:-4]
            if name not in wiki_names:
                continue  # thuocl_* / curate 补充域不进 system_dict，避免专名污染常用候选
            entry_list = []
            with open(os.path.join(dom_dir, fn), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    w, py = parts[0], parts[1]
                    entry_list.append((w, py))
                    all_wiki.setdefault(w, py)
            domain_entries[fn[:-4]] = entry_list
    else:
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

    # ── 4.15 长词补充（curate/longwords.txt）──
    # 碎片还原出来的长词（马尔马拉海/公车上书这类），补进来让正向最大匹配优先切长词，
    # 碎片也就不再被当入口。频次在清单里按语料频次分档（500-1000），低于同类专名的
    # 常见档位（1500-3000），避免长词抢短词的首位。
    longword_path = os.path.join(ROOT, "curate", "longwords.txt")
    if os.path.exists(longword_path):
        lw_added = 0
        with open(longword_path, encoding="utf-8") as fh:
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
                if word not in merged:
                    merged[word] = (pinyin, freq)
                    lw_added += 1
        print(f"  长词补充: {lw_added} 词（curate/longwords.txt）")
    else:
        print("  长词补充: 无清单，跳过")

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

    # ── 4.3 碎片词条降权（curate/fragments.txt）──
    # 语料跟随度 + 语义复核确认的碎片（2~4 字、几乎总跟在特定字前后、语义不自足），
    # 频次压到 1：词条保留（翻页仍可取到），只让出候选前排位置。
    # 清单由 tools/analyze_fragments.py 产出、tools/apply_fragment_downgrade.py 落库，
    # 这里接入是为了下次全量重建不回滚降权。
    fragment_path = os.path.join(ROOT, "curate", "fragments.txt")
    if os.path.exists(fragment_path):
        frag_words = set()
        with open(fragment_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    frag_words.add(line)
        hit = 0
        for word in frag_words:
            if word in merged:
                merged[word] = (merged[word][0], 1)
                hit += 1
        print(f"  碎片降权: {hit}/{len(frag_words)} 词（curate/fragments.txt）")
    else:
        print("  碎片降权: 无清单，跳过")

    # ── 4.16 校正批次：补单字/补词（curate/extra_words.txt）──
    # 2026-11-18 全量校正产出。格式：词\t拼音\t频次。同 longwords 机制，已存在则跳过。
    extra_path = os.path.join(ROOT, "curate", "extra_words.txt")
    if os.path.exists(extra_path):
        ew_added = 0
        with open(extra_path, encoding="utf-8") as fh:
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
                if word not in merged:
                    merged[word] = (pinyin, freq)
                    ew_added += 1
        print(f"  校正补词: {ew_added} 词（curate/extra_words.txt）")

    # ── 4.17 校正批次：拼音修正（curate/pinyin_fix.txt）──
    # 格式：词\t正确拼音。覆盖 jieba/pypinyin 注错的拼音（如 长江大桥 zhangjiang→changjiang）。
    pfix_path = os.path.join(ROOT, "curate", "pinyin_fix.txt")
    if os.path.exists(pfix_path):
        pf_fixed = 0
        with open(pfix_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                word, pinyin = parts[0], parts[1]
                if word in merged and merged[word][0] != pinyin:
                    merged[word] = (pinyin, merged[word][1])
                    pf_fixed += 1
        print(f"  拼音修正: {pf_fixed} 词（curate/pinyin_fix.txt）")

    # 对数压缩 → 1~MAX_FREQ
    entries_out: list[tuple[str, str, int]] = []
    max_adj = max(f for _, f in merged.values()) if merged else 1
    for word, (pinyin, freq) in merged.items():
        compressed = compress_freq(freq, max_adj)
        entries_out.append((word, pinyin, compressed))

    # ── 4.18 校正批次：单字补读音（curate/extra_readings.txt）──
    # 格式：字\t拼音\t频次（频次已是最终值，不再压缩）。同一字可有多个拼音，
    # 追加为独立行——merged 是 word→单拼音的字典，这里绕开它，避免改动主结构。
    radd_path = os.path.join(ROOT, "curate", "extra_readings.txt")
    if os.path.exists(radd_path):
        existing_pairs = {(w, py) for w, py, _ in entries_out}
        ra_added = 0
        with open(radd_path, encoding="utf-8") as fh:
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
                if (word, pinyin) in existing_pairs:
                    continue
                entries_out.append((word, pinyin, max(1, min(MAX_FREQ, freq))))
                existing_pairs.add((word, pinyin))
                ra_added += 1
        print(f"  补读音: {ra_added} 条（curate/extra_readings.txt）")

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

    # ── 5.4 合并源 txt（V0.5.7 目录分离）──
    # curate/domains/ = 人工维护源（thuocl/conversation/modern/network_slang，
    # 不可重建，入库）；output/domains/ = wiki 重建产物（gitignore）。
    # 构建 domains.db 前把源合并进产物目录——domains.db 从 output/domains/ 读全部 txt。
    print("\n── 5.4/6 合并源 txt ──")
    src_dir = os.path.join(ROOT, "curate", "domains")
    if os.path.isdir(src_dir):
        merged = 0
        for fn in sorted(os.listdir(src_dir)):
            if fn.endswith(".txt"):
                shutil.copy(os.path.join(src_dir, fn),
                            os.path.join(OUT_DIR, "domains", fn))
                merged += 1
        print(f"  源 txt 合并: {merged} 个")

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

    # ── 6.5 版本清单（V0.5.7 词库版本控制）──
    # 校验通过后生成 VERSION.json：版本号 + git commit + 各词库 sha256/条数。
    # sync_to_ime.py 同步前按此清单对账，保证"同步的是本轮产物"。
    print("\n── 6.5/6 版本清单 ──")
    version = write_version()

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("  构建完成")
    print(f"  版本:    {version}")
    print(f"  系统词库: {db_path}  ({len(entries_out):,} 条)")
    print(f"  领域词库: {OUT_DIR}/domains/  ({len(domain_entries)} 个领域)")
    print(f"  领域 DB:  {OUT_DIR}/domains/domains.db")
    print(f"  常用 DB:  {OUT_DIR}/common.db")
    print("=" * 60)


if __name__ == "__main__":
    main()
