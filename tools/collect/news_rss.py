#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RSS 新闻 → 新词候选（PMI 凝固度 + 左右熵 + 跨文档频次）。

用法：
    python tools/collect/news_rss.py --out tmp/news_candidates.tsv
    python tools/collect/news_rss.py --sources-file path/to/sources.json --proxy http://127.0.0.1:7897

原理：
    1. 抓 RSS，按 pubDate 判定源新鲜度（僵尸源整体丢弃），按 recent_days 过滤条目
    2. 标题+摘要去重、繁简归一
    3. 2/3/4-gram 滑窗 → PMI（内部结合强度）+ 左右熵（边界独立性）
    4. 剔除词库已有的词，输出候选

为什么需要 PMI 和左右熵：
    只用「最大匹配 + 频次」时，Top 候选全是「也是 / 中的 / 点此」这类碎片——
    它们高频但内部结合弱（PMI 低）、前后搭配单一（熵低）。
    PMI 滤掉随机相邻，左右熵滤掉固定前后缀。
"""
import argparse
import json
import math
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

from zhconv import convert

HANZI_SEG = re.compile(r"[\u4e00-\u9fff]+")
# 虚词字：出现在词首/词尾时大概率是碎片
CLITIC = set("的了和是在有就不很也都那这我你他她它们个上下中说到要会能可以而但所因为"
             "如果还又再最更非常没别吧呢啊嗯哦呀嘛么着过将被把让使从对向于与及或等之"
             "其此该每所有些怎样谁哪几什")

DEF_SOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_sources.json")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def build_opener(proxy):
    handlers = [urllib.request.HTTPSHandler(context=ssl._create_unverified_context())]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def load_vocab():
    """载入已有词表（system_dict + domains.db），用于差分。"""
    import sqlite3
    words = set()
    for db, tables in ((os.path.join(ROOT, "output", "system_dict.db"),
                        ("system_dict", "system_dict_trad")),
                       (os.path.join(ROOT, "output", "domains", "domains.db"),
                        ("domain_words",))):
        try:
            conn = sqlite3.connect(db)
            for t in tables:
                words |= {w for (w,) in conn.execute(f"SELECT DISTINCT word FROM {t}")}
            conn.close()
        except Exception:
            pass
    return words


def fetch(conf, opener, now):
    docs, seen_titles = [], set()
    live, dead = [], []
    per = conf["fetch"].get("per_source", 30)
    timeout = conf["fetch"].get("timeout", 15)
    recent = conf["fetch"].get("recent_days", 7)
    max_stale = conf["fetch"].get("source_max_stale_days", 30)

    for src in conf["sources"]:
        try:
            req = urllib.request.Request(src["url"], headers={
                "User-Agent": "Mozilla/5.0 (compatible; taishen-dict/1.0)"})
            root = ET.fromstring(opener.open(req, timeout=timeout).read())
        except Exception as e:
            dead.append((src["name"], f"抓取失败 {type(e).__name__}"))
            continue
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry")
        dates, kept = [], 0
        for it in items:
            if kept >= per:
                break
            d = parse_dt(it.findtext("pubDate") or it.findtext("date") or
                         it.findtext("{http://www.w3.org/2005/Atom}updated"))
            title = (it.findtext("title") or "").strip()
            desc = (it.findtext("description") or it.findtext("summary") or "").strip()
            if d:
                dates.append(d)
            if d and (now - d).days > recent:
                continue
            key = re.sub(r"\W", "", title)
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            docs.append(convert(f"{title} {desc}", "zh-cn"))
            kept += 1
        newest = max(dates) if dates else None
        if newest is None:
            dead.append((src["name"], "无 pubDate"))
        elif (now - newest).days > max_stale:
            dead.append((src["name"], f"僵尸源（最新 {newest:%Y-%m-%d}）"))
        else:
            live.append((src["name"], kept))
    return docs, live, dead


def entropy(dist):
    total = sum(dist.values())
    if total <= 1:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in dist.values())


def count_ngrams(docs, n):
    gf = defaultdict(int)
    gl = defaultdict(lambda: defaultdict(int))
    gr = defaultdict(lambda: defaultdict(int))
    cf = defaultdict(int)
    total = 0
    for text in docs:
        for seg in HANZI_SEG.findall(text):
            L = len(seg)
            for i in range(L - n + 1):
                g = seg[i:i + n]
                gf[g] += 1
                total += 1
                if i > 0:
                    gl[g][seg[i - 1]] += 1
                if i + n < L:
                    gr[g][seg[i + n]] += 1
            for ch in seg:
                cf[ch] += 1
    return gf, gl, gr, cf, max(1, total)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources-file", default=DEF_SOURCES)
    ap.add_argument("--out", default=os.path.join(ROOT, "tmp", "news_candidates.tsv"))
    ap.add_argument("--proxy", default=None, help="如 http://127.0.0.1:7897")
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--pmi-min", type=float, default=1.0)
    ap.add_argument("--ent-min", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=150)
    args = ap.parse_args()

    with open(args.sources_file, encoding="utf-8") as f:
        conf = json.load(f)
    opener = build_opener(args.proxy)
    now = datetime.now(timezone.utc)

    vocab = load_vocab()
    print(f"词表 {len(vocab):,}")

    docs, live, dead = fetch(conf, opener, now)
    print(f"\n存活源 {len(live)}：")
    for n, k in live:
        print(f"  {n}: {k} 条")
    if dead:
        print(f"丢弃源 {len(dead)}：{dead}")
    print(f"\n语料 {len(docs)} 篇，{sum(len(d) for d in docs):,} 字符")

    rows = []
    for n in (2, 3, 4):
        gf, gl, gr, cf, total = count_ngrams(docs, n)
        kept = []
        for g, cnt in gf.items():
            if cnt < args.min_freq or g in vocab:
                continue
            if g[0] in CLITIC or g[-1] in CLITIC:
                continue
            if n == 2:
                pmi = math.log2(cnt * total / max(1, cf[g[0]] * cf[g[1]]))
            else:
                denom = 1
                for ch in g:
                    denom *= max(1, cf[ch])
                pmi = math.log2(cnt * total ** (n - 1) / denom)
            le, re_ = entropy(gl[g]), entropy(gr[g])
            if pmi < args.pmi_min or min(le, re_) < args.ent_min:
                continue
            kept.append((g, cnt, round(pmi, 2), round(le, 2), round(re_, 2), n))
        kept.sort(key=lambda x: (-x[1], -x[2]))
        print(f"n={n}: {len(gf)} → {len(kept)} 通过")
        rows.extend(kept[:args.top])

    rows.sort(key=lambda x: (-x[1], -x[2]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# word\tcount\tpmi\tleft_ent\tright_ent\tngram\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"\n写出 {args.out}（{len(rows)} 条）")
    print("下一步：交给 LLM 做三档分级（A 术语 / B 专名 / C 丢弃），见 docs/COLLECTION.md")


if __name__ == "__main__":
    main()
