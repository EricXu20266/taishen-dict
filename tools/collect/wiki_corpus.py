#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维基 dumps 正文提取：bz2 → 纯文本语料。

用途：给 tools/build_context_prior.py 提供语料目录（统计相邻词共现）。
与 wiki_dumps.py 的分工：那个脚本只取标题 + 分类（做领域词），本脚本取正文（做搭配）。

用法：

    python tools/collect/wiki_corpus.py --xml tmp/dumps/zhwiki-latest-pages-articles.xml.bz2 \
        --out-dir tmp/corpus --limit 20000 --shard-mb 64

产物：out-dir/corpus_000.txt, corpus_001.txt ...（纯汉字 + 换行）

清洗策略（保守，只删结构性标记，不改写文字）：
- 模板/表格嵌套剥离、ref/gallery 等标签删除、文件与分类链接删除
- [[A|B]] → B、[[A]] → A、外链取描述文字、粗体斜体标记去掉
- 最终只保留汉字与换行——build_context_prior.py 本身也只看汉字
"""
import argparse
import bz2
import os
import re
import sys
import time

PAGE_OPEN = b"<page>"
PAGE_CLOSE = b"</page>"
TEXT_OPEN = b"<text"
REDIRECT_RE = re.compile(rb"<redirect")

RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_REF = re.compile(r"<ref[^>]*>.*?</ref>", re.S)
RE_REF_SELF = re.compile(r"<ref[^>]*/>")
RE_TAG = re.compile(r"<[^>]{1,200}>")
RE_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
RE_TABLE = re.compile(r"\{\|.*?\|\}", re.S)
RE_FILE = re.compile(
    r"\[\[(?:File|Image|文件|檔案|图像|圖像|媒体|媒體):[^\]]*\]\]", re.I
)
RE_CAT = re.compile(r"\[\[(?:Category|分类|分類):[^\]]*\]\]", re.I)
RE_LINK_PIPE = re.compile(r"\[\[([^\]\|]+)\|([^\]]+)\]\]")
RE_LINK = re.compile(r"\[\[([^\]]+)\]\]")
RE_EXTLINK = re.compile(r"\[https?://[^\s\]]+\s*([^\]]*)\]")
RE_APOS = re.compile(r"'{2,5}")
RE_HEAD = re.compile(r"^=+\s*(.*?)\s*=+\s*$", re.M)
RE_HTML_ENT = re.compile(r"&[a-zA-Z]+;|&#\d+;")
RE_NON_HAN = re.compile(r"[^\u4e00-\u9fff]+")


def iter_pages(path, chunk=8 * 1024 * 1024):
    """流式解压 + 按 <page> 切分（内存恒定）。"""
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
                yield buf[s : e + len(PAGE_CLOSE)]
                buf = buf[e + len(PAGE_CLOSE) :]


def extract_text(raw):
    """从 <page> 里取 <text> 正文；重定向页返回 None。"""
    if REDIRECT_RE.search(raw):
        return None
    ti = raw.find(TEXT_OPEN)
    if ti < 0:
        return None
    gt = raw.find(b">", ti)
    et = raw.find(b"</text>", gt)
    if gt <= 0 or et <= gt:
        return None
    return raw[gt + 1 : et].decode("utf-8", errors="ignore")


def clean(text):
    """剥离 wiki 标记，只留汉字（用换行保留段落边界）。"""
    text = RE_COMMENT.sub("", text)
    text = RE_REF.sub("", text)
    text = RE_REF_SELF.sub("", text)
    text = RE_FILE.sub("", text)
    text = RE_CAT.sub("", text)
    # 模板/表格可能嵌套，剥三轮
    for _ in range(3):
        prev = text
        text = RE_TEMPLATE.sub("", text)
        text = RE_TABLE.sub("", text)
        if text == prev:
            break
    text = RE_TAG.sub("", text)
    text = RE_APOS.sub("", text)
    text = RE_HEAD.sub(r"\1", text)
    text = RE_EXTLINK.sub(r"\1", text)
    text = RE_LINK_PIPE.sub(r"\2", text)
    text = RE_LINK.sub(r"\1", text)
    text = RE_HTML_ENT.sub(" ", text)
    # 只保留汉字；非汉字串压成一个空行分隔
    text = RE_NON_HAN.sub("\n", text)
    # 去掉连续空行
    return "\n".join(line for line in text.split("\n") if line)


def main():
    ap = argparse.ArgumentParser(description="维基 dumps 正文 → 纯文本语料")
    ap.add_argument("--xml", required=True, help="zhwiki-*.xml.bz2")
    ap.add_argument("--out-dir", required=True, help="语料输出目录")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少页（0=全量）")
    ap.add_argument("--shard-mb", type=int, default=64, help="单个分片文件大小上限 MB")
    ap.add_argument("--min-han", type=int, default=200, help="正文汉字数下限（过滤小作品）")
    args = ap.parse_args()

    if not os.path.exists(args.xml):
        sys.exit(f"dump 不存在: {args.xml}")
    os.makedirs(args.out_dir, exist_ok=True)

    shard_bytes = max(1, args.shard_mb) * 1024 * 1024
    t0 = time.time()
    total = kept = 0
    written = 0
    shard_idx = 0
    fh = None

    def open_shard():
        nonlocal fh, shard_idx
        if fh:
            fh.close()
        path = os.path.join(args.out_dir, f"corpus_{shard_idx:03d}.txt")
        shard_idx += 1
        fh = open(path, "w", encoding="utf-8", newline="\n")
        return path

    open_shard()
    try:
        for raw in iter_pages(args.xml):
            total += 1
            body = extract_text(raw)
            if body:
                cleaned = clean(body)
                if len(cleaned) >= args.min_han:
                    fh.write(cleaned)
                    fh.write("\n")
                    written += len(cleaned.encode("utf-8"))
                    kept += 1
            if written >= shard_bytes:
                written = 0
                open_shard()
            if total % 5000 == 0:
                print(
                    f"[corpus] {total} 页 / 保留 {kept} / "
                    f"{shard_idx} 片 / {time.time() - t0:.0f}s",
                    flush=True,
                )
            if args.limit and total >= args.limit:
                break
    finally:
        if fh:
            fh.close()

    size = sum(
        os.path.getsize(os.path.join(args.out_dir, n))
        for n in os.listdir(args.out_dir)
        if n.startswith("corpus_")
    )
    print(
        f"完成：{total} 页，保留 {kept} 页，{shard_idx} 片，"
        f"{size / 1024 / 1024:.1f} MB，{time.time() - t0:.0f}s"
    )


if __name__ == "__main__":
    main()
