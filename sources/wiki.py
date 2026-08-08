#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
源：中文维基百科分类词条（CC BY-SA 4.0）
────────────────────────────────────
通过 MediaWiki API 递归采集指定分类下的词条标题，过滤纯汉字词，
pypinyin 注音。按领域输出。

领域配置：../domains.yaml
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error

try:
    from pypinyin import pinyin, Style
except ImportError:
    sys.exit("需要 pypinyin: pip install pypinyin")

API = "https://zh.wikipedia.org/w/api.php"
UA = {"User-Agent": "taishen-dict/1.0 (https://github.com/EricXu20266/taishen-dict)"}
REQUEST_GAP = 0.15
MAX_PAGES = 25000
MAX_WORD_LEN = 6
BATCH_SIZE = 2000


def _ssl_context() -> ssl.SSLContext:
    """本机代理环境下的未验证 SSL 上下文。"""
    return ssl._create_unverified_context()


def api_call(params: dict, retries: int = 3) -> dict:
    """MediaWiki API 请求（带重试）。"""
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    url = f"{API}?{qs}&format=json"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            resp = urllib.request.urlopen(req, timeout=30, context=_ssl_context())
            return json.loads(resp.read())
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1 * (attempt + 1))
    return {}


def collect_category(
    cat: str, depth: int, max_depth: int, visited: set[str]
) -> list[str]:
    """递归采集分类成员（页面 + 子分类）。"""
    if depth > max_depth or cat in visited or len(visited) > 5000:
        return []
    visited.add(cat)

    pages: list[str] = []
    cmcontinue: dict | None = None

    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": cat,
            "cmlimit": "500",
            "cmtype": "page|subcat",
        }
        if cmcontinue:
            params.update(cmcontinue)

        try:
            data = api_call(params)
        except Exception as e:
            print(f"    [warn] {cat} API 失败: {e}")
            break

        members = data.get("query", {}).get("categorymembers", [])
        subcats: list[str] = []
        for m in members:
            title = m["title"]
            if m["ns"] == 14:  # Category namespace
                subcats.append(title)
            elif m["ns"] == 0:  # Article namespace
                pages.append(title)

        # 递归子分类
        for sc in subcats:
            if sc not in visited:
                pages.extend(collect_category(sc, depth + 1, max_depth, visited))

        if "continue" in data:
            cmcontinue = data["continue"]
            time.sleep(REQUEST_GAP)
        else:
            break

    return pages


def collect_domain(
    name: str, wiki_categories: list[str], max_depth: int
) -> list[tuple[str, str, str]]:
    """
    采集一个领域的所有词条 → [(word, pinyin, domain_name)]。
    """
    print(f"\n  [wiki] {name}: {wiki_categories}")
    hanzi_re = re.compile(r"^[\u4e00-\u9fff]{1,%d}$" % MAX_WORD_LEN)
    visited: set[str] = set()
    raw: set[str] = set()

    for cat in wiki_categories:
        titles = collect_category(cat, depth=0, max_depth=max_depth, visited=visited)
        for t in titles:
            # 去括号消歧义（"函数（数学）" → "函数"）
            clean = t.split("（")[0].split("(")[0].strip()
            if hanzi_re.match(clean):
                raw.add(clean)

    print(f"  [wiki] {name}: 原始 {len(raw)} 条，清洗后纯汉字 {len(raw)} 条")

    if not raw:
        return []

    # 批量注音
    words = list(raw)
    result: list[tuple[str, str, str]] = []
    for i in range(0, len(words), BATCH_SIZE):
        chunk = words[i : i + BATCH_SIZE]
        py_flat = pinyin(chunk, style=Style.NORMAL, heteronym=False)
        idx = 0
        for word in chunk:
            n = len(word)
            syls = py_flat[idx : idx + n]
            idx += n
            py_joined = "".join(s[0] for s in syls if s)
            if py_joined and py_joined.isascii() and py_joined.islower():
                result.append((word, py_joined, name))

    # 截断（防单领域过大）
    if len(result) > MAX_PAGES:
        result = result[:MAX_PAGES]

    print(f"  [wiki] {name}: 最终 {len(result)} 条")
    return result
