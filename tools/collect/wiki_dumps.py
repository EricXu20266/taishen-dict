#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维基 dumps 离线采集：流式解析 + 分类提取 + 领域分流。

为什么要用 dumps 而不是分类 API：
    分类 API 有 429 限流（实测 0.15s 间隔连续触发），21 个领域全量跑会被打断。
    dumps 离线全量、自带分类标签，解析一次可反复使用。

下载（约 3.2 GB，支持 Range，建议 4 连接）：
    aria2c -c -x4 -s4 -m10 --file-allocation=none \
        -o zhwiki-latest-pages-articles.xml.bz2 \
        https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles.xml.bz2

用法：
    # 1) 解析：bz2 → 「标题<TAB>分类1|分类2」
    python tools/collect/wiki_dumps.py parse --xml tmp/dumps/zhwiki-*.bz2 --limit 120000 \
        --out tmp/dumps_cat_pairs.tsv

    # 2) 分流：按 domains.yaml 的领域关键词归组，减去已有词库
    python tools/collect/wiki_dumps.py group --tsv tmp/dumps_cat_pairs.tsv \
        --out tmp/dumps_by_domain.json
"""
import argparse
import bz2
import json
import os
import re
import sys
import time
from collections import defaultdict

from zhconv import convert

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAGE_OPEN = b"<page>"
PAGE_CLOSE = b"</page>"
TITLE_RE = re.compile(rb"<title>(.*?)</title>", re.S)
TEXT_OPEN = b"<text"
REDIRECT_RE = re.compile(rb"<redirect")
CAT_RE = re.compile(rb"\[\[Category:([^\]\|\n#]+)")
HANZI_RE = re.compile(r"^[\u4e00-\u9fff]{2,8}$")

# 领域 → 分类名关键词（命中任一即归入，按顺序优先）
DOMAIN_KW = {
    "computer": ["计算机", "软件", "编程", "算法", "人工智能", "互联网", "操作系统",
                 "程序设计", "数据", "网络", "机器学习", "网页", "信息技术", "計算機", "資訊"],
    "math": ["数学", "幾何", "几何", "代数", "微积分", "数论", "拓扑", "统计学", "邏輯", "逻辑"],
    "physics": ["物理", "量子", "力学", "光学", "电磁", "热力学", "粒子", "相對論", "相对论"],
    "chemistry": ["化学", "化合物", "元素", "有机", "無機", "无机", "化学物质"],
    "biology": ["生物", "动物", "植物", "基因", "细胞", "生态", "微生物", "物种"],
    "medical": ["医学", "疾病", "药物", "解剖", "临床", "外科", "内科", "症状", "医疗"],
    "law": ["法律", "法规", "司法", "法院", "宪法", "法令", "法制"],
    "economics": ["经济", "金融", "财政", "贸易", "货币", "银行", "投资", "企业", "产业"],
    "geography": ["地理", "地质", "山脉", "河流", "湖泊", "地形"],
    "history": ["历史", "朝代", "战争", "古代", "考古", "历史事件"],
    "literature": ["文学", "作家", "小说", "诗歌", "散文", "作品", "文學"],
    "philosophy": ["哲学", "思想家", "逻辑", "伦理", "宗教", "形上学", "美學"],
    "psychology": ["心理学", "认知", "情绪", "心理", "行為"],
    "sport": ["体育", "足球", "篮球", "奥运", "运动员", "比赛", "賽事", "体育组织"],
    "military": ["军事", "武器", "军队", "战舰", "战斗机", "军事装备"],
    "agriculture": ["农业", "作物", "种植", "畜牧", "林业", "渔业", "農"],
    "art": ["艺术", "绘画", "音乐", "雕塑", "设计", "美術", "舞蹈", "摄影"],
    "food": ["饮食", "食品", "菜肴", "烹饪", "食物", "食譜"],
    "astronomy": ["天文", "行星", "恒星", "星系", "宇宙", "彗星", "小行星"],
    "meteorology": ["气象", "气候", "台风", "天气", "氣象"],
    "idiom": ["成语", "谚语", "俗語", "熟語"],
}


def iter_pages(path, chunk=8 * 1024 * 1024):
    """流式解压 + 按 <page> 切分（内存恒定，不整文件加载）。"""
    dec = bz2.BZ2Decompressor()
    buf = b""
    with open(path, "rb") as f:
        while True:
            raw = f.read(chunk)
            if not raw:
                break
            try:
                buf += dec.decompress(raw)
            except EOFError:  # bzip2 多流拼接
                dec = bz2.BZ2Decompressor()
                buf += dec.decompress(raw)
            while True:
                s = buf.find(PAGE_OPEN)
                if s < 0:
                    buf = buf[-64:]
                    break
                e = buf.find(PAGE_CLOSE, s)
                if e < 0:
                    buf = buf[s:]
                    break
                yield buf[s:e + len(PAGE_CLOSE)]
                buf = buf[e + len(PAGE_CLOSE):]


def parse_page(raw):
    m = TITLE_RE.search(raw)
    if not m or REDIRECT_RE.search(raw):
        return None
    title = m.group(1).decode("utf-8", errors="ignore")
    ti = raw.find(TEXT_OPEN)
    if ti < 0:
        return None
    gt = raw.find(b">", ti)
    et = raw.find(b"</text>", gt)
    if gt <= 0 or et <= gt:
        return None
    cats = [c.decode("utf-8", "ignore").strip() for c in CAT_RE.findall(raw[gt:et])[:8]]
    return title, cats


def mode_parse(args):
    t0 = time.time()
    total = kept = 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as out:
        for raw in iter_pages(args.xml):
            total += 1
            p = parse_page(raw)
            if p:
                title, cats = p
                if HANZI_RE.match(title) and cats:
                    out.write(f"{title}\t{'|'.join(cats)}\n")
                    kept += 1
            if total % 10000 == 0:
                print(f"[parse] {total} 页 {time.time()-t0:.0f}s 带分类 {kept}",
                      flush=True)
            if args.limit and total >= args.limit:
                break
    print(f"完成：{total} 页，带分类的纯汉字词条 {kept}，{time.time()-t0:.0f}s")
    print(f"输出: {args.out}")


def load_vocab():
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


def mode_group(args):
    vocab = load_vocab()
    print(f"词库 {len(vocab):,}")

    by_domain = defaultdict(set)
    unknown = 0
    total = 0
    with open(args.tsv, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            total += 1
            title = convert(parts[0], "zh-cn")
            cats = convert(parts[1], "zh-cn")
            hit = next((d for d, kws in DOMAIN_KW.items()
                        if any(k in cats for k in kws)), None)
            if hit:
                if title not in vocab:
                    by_domain[hit].add(title)
            else:
                unknown += 1

    print(f"总条目 {total}，命中领域（去词库重复）"
          f"{sum(len(v) for v in by_domain.values())}，未归类 {unknown}\n")
    for dom in sorted(by_domain, key=lambda d: -len(by_domain[d])):
        ws = sorted(by_domain[dom])
        print(f"  {dom:14s} {len(ws):5d}  {' '.join(ws[:6])}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({d: sorted(v) for d, v in by_domain.items()}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n输出: {args.out}")
    print("下一步：LLM 三档分级（A 术语进热域 / B 专名进 proper_noun / C 丢弃）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    p1 = sub.add_parser("parse", help="bz2 → 标题+分类 TSV")
    p1.add_argument("--xml", required=True)
    p1.add_argument("--out", default=os.path.join(ROOT, "tmp", "dumps_cat_pairs.tsv"))
    p1.add_argument("--limit", type=int, default=0, help="0=全量")
    p1.set_defaults(func=mode_parse)

    p2 = sub.add_parser("group", help="按领域分流 + 词库差分")
    p2.add_argument("--tsv", required=True)
    p2.add_argument("--out", default=os.path.join(ROOT, "tmp", "dumps_by_domain.json"))
    p2.set_defaults(func=mode_group)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
