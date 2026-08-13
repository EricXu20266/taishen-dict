#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「输入串维度」词库挖掘 —— 挖词库未覆盖的高频相邻共现字串。

背景 / 动机
═══════════════
现有 OOV 挖掘（tmp/mining.py + filter_*.js）用「词汇学维度」选词：
  分词 → 找未登录片段（OOV）→ 频次 <3 过滤 → 人工挑「实词」。
结果「真机 / 还会 / 留着 / 还在」这类「字都登录、组合未登录」的高频
输入串，在「挑实词」这一步被当碎片丢掉（它们不是"词"，是"词的组合"）。

本模块改用「输入串维度」，用新词发现的标准三指标，不做「是不是实词」的语义判断：
  1. 凝固度 PMI —— 内部两字是否显著强于随机共现（滤掉「的是/了一」功能字黏着）
  2. 左右熵    —— 外部前后搭配是否多样（滤掉「览器/个文」这类长词碎片）
  3. 频次      —— 真实语料里出现够多（保证值得收）

语料（真实输入，分三路统计来源）：
  - user  — 泰深对话库 messages.content，role=user（Eric 真实打字）
  - ai    — 泰深对话库 messages.content，role=assistant（AI 正文）
  - nga   — NGA 论坛语料 tmp/nga_corpus.txt

输出（写到 tmp/）：
  tmp/input_string_bigrams.txt   2 字候选
  tmp/input_string_trigrams.txt  3 字候选
  格式：word<TAB>频次<TAB>PMI<TAB>左熵<TAB>右熵<TAB>拼音<TAB>user<TAB>ai<TAB>nga

用法：
  python tools/mine_input_strings.py [--min-freq 2] [--pmi-min 1.0] [--ent-min 1.0] [--top 200]
"""

import math
import os
import re
import sqlite3
import sys
from collections import defaultdict

from pypinyin import pinyin, Style

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "tmp")
TAISHEN_DB = os.path.expanduser("~/.taishen/taishen.db")
NGA_CORPUS = os.path.join(TMP, "nga_corpus.txt")

# 纯黏着/语气助词：两字都在此集合 → 语法黏着碎片，直接过滤。
# 注意「还 / 会 / 在」等副词/助动词不在内，故「还会 / 还在」保留。
CLITIC = set("的了着过呢吗吧啊哦嗯么哈啦呀哇嘛哟哉乎焉矣之其者也")

# 连续中文字符段（过滤英文/数字/标点/代码/路径/URL）
HANZI_SEG = re.compile(r"[\u4e00-\u9fff]{2,}")


def iter_tai_corpus():
    """泰深对话库语料：user（真实输入）+ assistant（AI 正文）。"""
    if not os.path.exists(TAISHEN_DB):
        print(f"  [warn] 泰深对话库不存在: {TAISHEN_DB}")
        return
    conn = sqlite3.connect(TAISHEN_DB)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE role IN ('user','assistant') AND content IS NOT NULL AND content != ''"
        )
        for role, content in rows:
            yield role, content
    finally:
        conn.close()


def iter_nga_corpus():
    if not os.path.exists(NGA_CORPUS):
        print(f"  [warn] NGA 语料不存在: {NGA_CORPUS}")
        return
    with open(NGA_CORPUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("==="):
                continue
            yield "nga", line


def count_ngrams(corpus, n):
    """n-gram 滑窗统计 + 左右邻字分布（供 PMI 凝固度 + 边界熵用）。

    返回 (gram_freq, gram_left, gram_right, char_freq, total_grams)
      gram_freq[gram][src]     = gram 在 src 语料的频次
      gram_left[gram][char]    = gram 的左邻字分布
      gram_right[gram][char]   = gram 的右邻字分布
      char_freq[char]          = 单字全局频次
      total_grams[src]         = src 语料的 n-gram 总数
    """
    gram_freq = defaultdict(lambda: defaultdict(int))
    gram_left = defaultdict(lambda: defaultdict(int))
    gram_right = defaultdict(lambda: defaultdict(int))
    char_freq = defaultdict(int)
    total_grams = defaultdict(int)

    for src, text in corpus:
        for seg in HANZI_SEG.findall(text):
            L = len(seg)
            for i in range(L - n + 1):
                g = seg[i : i + n]
                gram_freq[g][src] += 1
                total_grams[src] += 1
                if i > 0:
                    gram_left[g][seg[i - 1]] += 1
                if i + n < L:
                    gram_right[g][seg[i + n]] += 1
            for ch in seg:
                char_freq[ch] += 1

    return gram_freq, gram_left, gram_right, char_freq, total_grams


def entropy(dist):
    """边界熵：值越高 = 前后搭配越多样 = 越像独立使用的单位。"""
    total = sum(dist.values())
    if total <= 1:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in dist.values())


def load_existing_words():
    """加载所有已入库词集，用于「未覆盖」判断。"""
    words = set()

    jieba_txt = os.path.join(TMP, "jieba_dict.txt")
    if os.path.exists(jieba_txt):
        with open(jieba_txt, encoding="utf-8") as f:
            for line in f:
                p = line.strip().split()
                if p:
                    words.add(p[0])

    sys_db = os.path.join(ROOT, "output", "system_dict.db")
    if os.path.exists(sys_db):
        conn = sqlite3.connect(sys_db)
        try:
            for (w,) in conn.execute("SELECT word FROM system_dict"):
                words.add(w)
            for (w,) in conn.execute("SELECT word FROM system_dict_trad"):
                words.add(w)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    dom_db = os.path.join(ROOT, "output", "domains", "domains.db")
    if os.path.exists(dom_db):
        conn = sqlite3.connect(dom_db)
        try:
            for (w,) in conn.execute("SELECT word FROM domain_words"):
                words.add(w)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    common_txt = os.path.join(ROOT, "curate", "common_dict.txt")
    if os.path.exists(common_txt):
        with open(common_txt, encoding="utf-8") as f:
            for line in f:
                line = line.lstrip("\ufeff").strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    words.add(parts[1].strip())

    return words


def annotate(words):
    """pypinyin 注音 → {word: pinyin}。"""
    result = {}
    words = sorted(words)
    for i in range(0, len(words), 2000):
        chunk = words[i : i + 2000]
        flat = pinyin(chunk, style=Style.NORMAL, heteronym=False)
        idx = 0
        for w in chunk:
            n = len(w)
            syls = flat[idx : idx + n]
            idx += n
            py = "".join(s[0] for s in syls if s)
            if py and py.isascii() and py.islower():
                result[w] = py
    return result


def main():
    min_freq = 2
    pmi_min = 1.0
    ent_min = 1.0
    top = 200
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--min-freq" and i + 1 < len(args):
            min_freq = int(args[i + 1])
            i += 2
        elif args[i] == "--pmi-min" and i + 1 < len(args):
            pmi_min = float(args[i + 1])
            i += 2
        elif args[i] == "--ent-min" and i + 1 < len(args):
            ent_min = float(args[i + 1])
            i += 2
        elif args[i] == "--top" and i + 1 < len(args):
            top = int(args[i + 1])
            i += 2
        else:
            i += 1

    words = load_existing_words()
    print(f"已入库词集: {len(words):,}")

    corpus = []
    n_user = n_ai = n_nga = 0
    for role, content in iter_tai_corpus():
        src = "user" if role == "user" else "ai"
        corpus.append((src, content))
        if src == "user":
            n_user += 1
        else:
            n_ai += 1
    for role, content in iter_nga_corpus():
        corpus.append(("nga", content))
        n_nga += 1

    print(f"语料条数: user={n_user:,} ai={n_ai:,} nga={n_nga:,}")

    for n, out_name in ((2, "input_string_bigrams.txt"), (3, "input_string_trigrams.txt")):
        gram_freq, gram_left, gram_right, char_freq, total_grams = count_ngrams(corpus, n)
        total_ngrams = sum(total_grams.values()) or 1

        results = []
        for g, src_map in gram_freq.items():
            cnt = sum(src_map.values())
            if cnt < min_freq:
                continue
            if g in words:
                continue
            if n == 2 and all(c in CLITIC for c in g):
                continue

            # 凝固度 PMI：共现相对随机期望的对数比；>0 即强于随机
            if n == 2:
                pmi = math.log2(
                    cnt * total_ngrams / max(1, char_freq[g[0]] * char_freq[g[1]])
                )
            else:
                pmi = math.log2(
                    cnt * total_ngrams ** 2
                    / max(1, char_freq[g[0]] * char_freq[g[1]] * char_freq[g[2]])
                )
            le = entropy(gram_left[g])
            re_ = entropy(gram_right[g])

            if pmi < pmi_min:
                continue
            if min(le, re_) < ent_min:
                continue

            results.append(
                (g, cnt, pmi, le, re_,
                 src_map.get("user", 0), src_map.get("ai", 0), src_map.get("nga", 0))
            )

        # 排序：频次降序（真实输入频率优先；碎片已被 PMI+熵过滤）
        results.sort(key=lambda x: -x[1])

        py_map = annotate([r[0] for r in results[: top * 2]])

        out_path = os.path.join(TMP, out_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# 输入串维度挖掘（n={n}）—— 词库未覆盖的高频共现字串\n")
            f.write("# 格式: word<TAB>频次<TAB>PMI<TAB>左熵<TAB>右熵<TAB>拼音<TAB>user<TAB>ai<TAB>nga\n")
            f.write("# PMI=凝固度(>0强搭配) 左熵/右熵=边界独立性(越高越像独立词)\n")
            for g, cnt, pmi, le, re_, u, a, ng in results[:top]:
                py = py_map.get(g, "?")
                f.write(f"{g}\t{cnt}\t{pmi:.2f}\t{le:.2f}\t{re_:.2f}\t{py}\t{u}\t{a}\t{ng}\n")

        print(f"\n[n={n}] 通过三指标过滤的候选 {len(results):,} 个，写入 top {min(top, len(results))} → {out_path}")
        print("  --- 预览 top 30 ---")
        for g, cnt, pmi, le, re_, u, a, ng in results[:30]:
            py = py_map.get(g, "?")
            print(f"    {g}\t{cnt}\t{pmi:.2f}\t{le:.2f}/{re_:.2f}\t{py}\tU{u}/A{a}/N{ng}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
