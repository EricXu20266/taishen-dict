#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上下文先验表构建：从纯文本语料统计相邻词共现，产出 bigram.db（表名 context_prior）。

产物结构（供引擎 engine_set_bigram_path 读取）：

    CREATE TABLE context_prior (
        prev  TEXT NOT NULL,
        next  TEXT NOT NULL,
        count INTEGER NOT NULL,      -- 档位 1-6，直接当前移权重
        PRIMARY KEY (prev, next)
    );

用法：

    python tools/build_context_prior.py --corpus corpus_dir --dict output/system_dict.db \
        --out output/bigram.db [--top 700000] [--min-count 2] [--per-prev 8]

语料与分词：
- 语料：--corpus 指向目录，递归读取 .txt / .md（UTF-8）。
- 分词：以系统词库词表做正向最大匹配；连续非汉字作切句边界。
- 简繁过滤：--trad-filter（默认开）按 zhconv 剔除含繁体独有字的词，
  避免繁简混排语料产生简体模式下无法命中的搭配对。

档位口径（与消费侧严格对齐，绝对频次映射，不再按分位切分）：
    档位 1 = 共现 1 次（无证据，默认 --min-count 2 时不入库）
    档位 2 = 2-3 次
    档位 3 = 4-7 次
    档位 4 = 8-15 次
    档位 5 = 16-39 次
    档位 6 = 40 次及以上
消费侧把档位 1 视为无证据（不计入主排序），2 档起才提升；
分位映射会让「共现 8 次」也被标成 1 档，导致强数据被丢弃。

质量约束：
- STOP_WORDS：前词是单字虚词时跳过（「的→X」无预测价值）。
- 后词是**虚词**（的/了/是/在…）的对保留但降档到 2-3 档，且不占 --per-prev 配额，
  避免「X 的」把实词搭配挤出每前词名额。这类对在拆组排序里仍有用（打 we+de 时「的」要排前）。
- 后词是**单字且非虚词**的对整对剔除：抽样显示碎片集中在「后词单字」形态
  （马尔马→拉、楚考烈→王、单元→音、不适→当）——专名被词表碎片切断后尾字落成单字。
  试过「降档到 2 而不删」：碎片仍会在同轮唯一命中时前移，且把档位 2 从 25 万撑到 47 万，
  档位 2 的切碎率从 10% 升到 50%，比不处理更差。
- 双单字对（两边都是单字）整对剔除：50 条分层抽样里这类占 11.4%，单独抽 20 条中
  18 条不可用（市→大、籀→庼、堂→区）；档位 6 里双单字占 28.8%，是最强权重位的碎片源。
- 前词单字且词频 < SINGLE_PREV_MIN_FREQ(3600) 的对整对剔除：专名首字碎片
  （芬→王朝、政→收入、票→反对）。实测碎片单字词频 2098-3457、常用单字（他/我/年/月/们）
  3880-4711，两档之间有空档，取 3600 作分界。
- NOISE_PAIRS：百科结构化标签对（模板/infobox 残留），整对剔除。
- --per-prev：每个前词最多保留多少条**实词**搭配（按频次降序），提升前词覆盖率。
  只有「后词为多字实词」的对占配额，虚词后词不占。
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter

HAN_MIN, HAN_MAX = 0x4E00, 0x9FFF

# 高频虚词：作为前词会产生大量无意义搭配，直接跳过
STOP_WORDS = {
    "的", "了", "是", "在", "和", "与", "就", "都", "而", "及", "或",
    "也", "还", "又", "很", "更", "最", "被", "把", "为", "以", "对",
    "从", "到", "上", "下", "中", "个", "这", "那", "有", "没有",
}

# 后词是虚词/助词：搭配弱，降档且不占每前词配额
WEAK_NEXT = {
    "的", "了", "是", "在", "和", "地", "得", "着", "过", "等",
    "与", "也", "而", "就", "至", "为", "被", "把", "或", "及",
    # 2026-11-17 抽检补入：档位 2 的虚词后词占 43%，原名单只有 19 字
    "不", "有", "之", "皆", "其", "亦", "且", "则", "者", "所",
    "矣", "焉", "乎", "已", "于", "此", "各", "该", "并",
}

# 百科结构化标签对（模板/infobox 残留），整对剔除
NOISE_PAIRS = {
    ("年", "出生"), ("年", "逝世"), ("年", "去世"), ("年", "生"), ("年", "卒"),
    ("人", "死亡"), ("已", "删除"), ("已", "上载"), ("已", "存档"), ("已", "归档"),
    ("外部", "链接"), ("内容", "为"), ("总人口", "为"), ("人口", "为"),
    ("面积", "为"), ("坐标", "为"), ("参考", "资料"), ("参见", "外部"),
    ("本", "条目"), ("本", "页面"), ("基", "百科"), ("分", "类"),
}

WORD_MIN_LEN, WORD_MAX_LEN = 1, 4

# 前词单字的最低词频：低于此值视为专名首字碎片（芬/帝/蓝/牙/政/票/采/湾）。
# 实测碎片单字词频 2098-3457，常用单字（他/我/你/年/月/们）3880-4711，中间有空档。
SINGLE_PREV_MIN_FREQ = 3600

# 档位上界：(次数上界, 档位)，超出最后一档即 6
TIER_BOUNDS = ((3, 2), (7, 3), (15, 4), (39, 5))

_trad_cache: dict[str, bool] = {}


def has_trad_char(word: str) -> bool:
    """词内是否含繁体独有字（zhconv 判定，按字缓存）。"""
    for ch in word:
        hit = _trad_cache.get(ch)
        if hit is None:
            if ord(ch) < HAN_MIN:
                hit = False
            else:
                try:
                    from zhconv import convert
                except ImportError:  # zhconv 缺失时不做简繁过滤，只提示
                    return False
                hit = convert(ch, "zh-cn") != ch
            _trad_cache[ch] = hit
        if hit:
            return True
    return False


def load_vocab(dict_db: str) -> tuple:
    """从系统词库读取词表，并单独取单字词频（判前词单字是否常用）。"""
    vocab = set()
    single_freq: dict = {}
    conn = sqlite3.connect(dict_db)
    try:
        cur = conn.execute("SELECT word, MAX(frequency) FROM system_dict GROUP BY word")
        for word, freq in cur:
            if WORD_MIN_LEN <= len(word) <= WORD_MAX_LEN:
                vocab.add(word)
                if len(word) == 1:
                    single_freq[word] = freq
    except sqlite3.Error as exc:
        sys.exit(f"读取词表失败: {exc}")
    finally:
        conn.close()
    if not vocab:
        sys.exit("词表为空，检查 --dict 是否指向 system_dict.db")
    print(f"词表: {len(vocab)} 词（单字 {len(single_freq)}）")
    return vocab, single_freq


def iter_corpus(corpus_dir: str):
    """遍历语料目录的文本文件。"""
    for root, _dirs, files in os.walk(corpus_dir):
        for name in files:
            if name.lower().endswith((".txt", ".md")):
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        yield path, fh.read()
                except OSError as exc:
                    print(f"跳过 {path}: {exc}")


def is_han(ch: str) -> bool:
    return HAN_MIN <= ord(ch) <= HAN_MAX


def split_sentences(text: str):
    """连续汉字段视为一个待分词片段（标点/空白/字母数字均为边界）。"""
    buf = []
    for ch in text:
        if is_han(ch):
            buf.append(ch)
        elif buf:
            yield "".join(buf)
            buf = []
    if buf:
        yield "".join(buf)


def segment(segment_text: str, vocab: set, max_len: int):
    """正向最大匹配分词：未命中词表的字串按单字输出（单字不参与共现统计）。"""
    out = []
    i = 0
    n = len(segment_text)
    while i < n:
        hit = None
        for size in range(min(max_len, n - i), 0, -1):
            candidate = segment_text[i:i + size]
            if size == 1 or candidate in vocab:
                hit = candidate
                break
        out.append(hit if hit else segment_text[i])
        i += len(hit) if hit else 1
    return out


def resplit_inner(word: str, vocab: set, max_len: int):
    """4 字以上整词按词表还原内部边界；切不出 ≥2 个多字词时返回 None。

    词表把「北京大学」「北京师范大学」这类常用组合收成了整词，正向最大匹配会把
    它们整体吃掉，导致 (北京, 大学) 这类搭配统计不到。整词化是词库的正常形态，
    所以在此把长词按同一词表还原内部边界。

    保守口径：只处理 ≥4 字整词，且每段必须 ≥2 字——3 字的「共产党」「共和国」
    这类词不拆，避免把已有的强搭配（中国→共产党）打碎成（中国→共产）。
    """
    n = len(word)
    if n < 4:
        return None
    segs = []
    i = 0
    while i < n:
        hit = None
        for size in range(min(max_len, n - i), 1, -1):
            # 首段不能等于整词本身，否则又会把整词匹配回来
            if i == 0 and size == n:
                continue
            if word[i:i + size] in vocab:
                hit = word[i:i + size]
                break
        if not hit:
            return None
        segs.append(hit)
        i += len(hit)
    return segs if len(segs) >= 2 else None


def inner_pairs(words: list, vocab: set, max_len: int):
    """长词内部相邻词对（额外证据，不替换原整词词对）。"""
    out = []
    for word in words:
        segs = resplit_inner(word, vocab, max_len)
        if segs:
            out.extend(zip(segs, segs[1:]))
    return out


def count_pairs(corpus_dir: str, vocab: set, max_len: int, trad_filter: bool, noise_filter: bool,
                single_filter: bool = True, single_freq: dict = None) -> Counter:
    """统计相邻词共现（同一句内相邻 + 长词内部边界）。"""
    pairs: Counter = Counter()
    files = 0
    dropped_trad = 0
    dropped_noise = 0
    dropped_single = 0

    def keep(left: str, right: str) -> bool:
        nonlocal dropped_trad, dropped_noise, dropped_single
        # 前词是纯虚词时跳过（「的→X」没有预测价值）；
        # 后词不过滤——「我们→的」这类对拆组排序有用。
        if left in STOP_WORDS:
            return False
        if not (WORD_MIN_LEN <= len(left) <= WORD_MAX_LEN):
            return False
        if not (WORD_MIN_LEN <= len(right) <= WORD_MAX_LEN):
            return False
        # 双单字对整对剔除：抽样 20 条中 18 条是长词被切成两个单字（市→大、籀→庼），
        # 且档位 6 里双单字占 28.8%——留着等于把碎片送进最强权重位。
        if single_filter and len(left) == 1 and len(right) == 1:
            dropped_single += 1
            return False
        # 后词单字且非虚词：专名尾字碎片（马尔马→拉、单元→音、不适→当）整对剔除。
        # 虚词单字保留（走 WEAK_NEXT 降档），拆组排序需要它们。
        if single_filter and len(right) == 1 and right not in WEAK_NEXT:
            dropped_single += 1
            return False
        # 前词单字且词频偏低：专名首字碎片（芬→王朝、帝→大理、政→收入、票→反对）整对剔除。
        # 高频单字（他/我/你/年/月/们）词频 3880+，不受影响，它们的搭配有预测价值。
        if single_filter and single_freq is not None and len(left) == 1:
            if single_freq.get(left, 0) < SINGLE_PREV_MIN_FREQ:
                dropped_single += 1
                return False
        if trad_filter and (has_trad_char(left) or has_trad_char(right)):
            dropped_trad += 1
            return False
        if noise_filter and (left, right) in NOISE_PAIRS:
            dropped_noise += 1
            return False
        return True

    for path, text in iter_corpus(corpus_dir):
        files += 1
        for seg_text in split_sentences(text):
            words = segment(seg_text, vocab, max_len)
            for left, right in zip(words, words[1:]):
                if keep(left, right):
                    pairs[(left, right)] += 1
            # 整词内部的词边界也是真实搭配，额外补一层（不替换原整词词对）
            for left, right in inner_pairs(words, vocab, max_len):
                if keep(left, right):
                    pairs[(left, right)] += 1
    print(f"语料文件: {files}，候选搭配: {len(pairs)}"
          f"（简繁剔除 {dropped_trad} 次、结构化对剔除 {dropped_noise} 次、"
          f"双单字对剔除 {dropped_single} 次）")
    return pairs


def tier_of(count: int, weak_next: bool = False) -> int:
    """绝对频次 → 档位。虚词后词封顶 3 档（弱证据）。

    后词是单字且非虚词的对已在 count_pairs 阶段剔除，所以这里只需处理虚词。
    """
    if count <= 1:
        return 1
    if weak_next:
        return 2 if count <= 7 else 3
    for upper, tier in TIER_BOUNDS:
        if count <= upper:
            return tier
    return 6


def select_pairs(pairs: Counter, top: int, per_prev: int) -> list:
    """按频次降序挑选；每前词最多 per_prev 条实词搭配。

    「实词」= 后词是多字且不在 WEAK_NEXT。虚词后词与单字后词都不占配额——
    否则「X 的」「X → 某单字」会把真正的多字搭配挤出每前词名额。
    """
    items = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    used: Counter = Counter()
    kept = []
    for (prev, nxt), count in items:
        if per_prev and nxt not in WEAK_NEXT:
            if used[prev] >= per_prev:
                continue
            used[prev] += 1
        kept.append(((prev, nxt), count))
        if top and len(kept) >= top:
            break
    return kept


def write_db(out_path: str, rows: list):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_path = out_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute(
            """
            CREATE TABLE context_prior (
                prev  TEXT NOT NULL,
                next  TEXT NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (prev, next)
            )
            """
        )
        conn.executemany(
            "INSERT OR REPLACE INTO context_prior (prev, next, count) VALUES (?, ?, ?)",
            rows,
        )
        conn.execute("CREATE INDEX idx_prior_prev ON context_prior(prev, count DESC)")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    os.replace(tmp_path, out_path)
    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"已写出 {out_path}（{len(rows)} 对，{size_mb:.1f} MB）")


def report_distribution(kept: list):
    """打印档位分布与覆盖指标（构建审计口径）。"""
    dist = Counter()
    prevs = set()
    double_single = 0
    for (prev, nxt), count in kept:
        dist[tier_of(count, nxt in WEAK_NEXT)] += 1
        prevs.add(prev)
        if len(prev) == 1 and len(nxt) == 1:
            double_single += 1
    total = len(kept) or 1
    print(f"档位分布: {dict(sorted(dist.items()))}")
    print(f"前词数: {len(prevs)}，双单字对占比: {double_single / total:.1%}")
    high = sum(v for k, v in dist.items() if k >= 5)
    print(f"高档位(5-6)占比: {high / total:.1%}")


def main():
    parser = argparse.ArgumentParser(description="从语料构建上下文先验表")
    parser.add_argument("--corpus", required=True, help="语料目录")
    parser.add_argument("--dict", required=True, help="system_dict.db 路径")
    parser.add_argument("--out", required=True, help="输出 bigram.db")
    parser.add_argument("--top", type=int, default=700000, help="最多保留的搭配对数")
    parser.add_argument("--min-count", type=int, default=2, help="最小共现次数")
    parser.add_argument("--per-prev", type=int, default=8,
                        help="每个前词最多保留的实词搭配数（0=不限）")
    parser.add_argument("--max-word-len", type=int, default=WORD_MAX_LEN)
    parser.add_argument("--no-trad-filter", action="store_true",
                        help="关闭简繁过滤（默认开启：剔除含繁体独有字的词对）")
    parser.add_argument("--no-noise-filter", action="store_true",
                        help="关闭结构化标签对过滤（默认开启）")
    parser.add_argument("--no-single-filter", action="store_true",
                        help="关闭双单字对剔除（默认开启：两边都是单字的对整对丢弃）")
    args = parser.parse_args()

    if not os.path.isdir(args.corpus):
        sys.exit(f"语料目录不存在: {args.corpus}")

    vocab, single_freq = load_vocab(args.dict)
    pairs = count_pairs(args.corpus, vocab, args.max_word_len,
                        not args.no_trad_filter, not args.no_noise_filter,
                        not args.no_single_filter, single_freq)
    if not pairs:
        sys.exit("未统计到任何搭配，检查语料内容与词表是否匹配")

    kept = [(pair, c) for pair, c in pairs.items() if c >= args.min_count]
    if not kept:
        sys.exit(f"没有搭配达到最小共现次数 {args.min_count}")
    kept = select_pairs(Counter(dict(kept)), args.top, args.per_prev)

    rows = [(prev, nxt, tier_of(c, nxt in WEAK_NEXT))
            for (prev, nxt), c in kept]
    write_db(args.out, rows)
    print(f"频次区间: {kept[-1][1]} ~ {kept[0][1]}")
    report_distribution(kept)


if __name__ == "__main__":
    main()
