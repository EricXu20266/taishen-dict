#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立采集维基百科网络流行语 / 现代口语词条 → output/domains/network_slang.txt"""
import os, sys, re, json, ssl, time, urllib.request

os.environ['http_proxy'] = 'http://127.0.0.1:7897'
os.environ['https_proxy'] = 'http://127.0.0.1:7897'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pypinyin import pinyin, Style

API = "https://zh.wikipedia.org/w/api.php"
UA = {"User-Agent": "taishen-dict/1.0"}
HANZI_RE = re.compile(r"^[\u4e00-\u9fff]{1,8}$")

def api_call(params, retries=3):
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    url = f"{API}?{qs}&format=json"
    ctx = ssl._create_unverified_context()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            resp = urllib.request.urlopen(req, timeout=30, context=ctx)
            return json.loads(resp.read())
        except Exception as e:
            print(f"    [retry {attempt+1}] {e}")
            if attempt == retries - 1:
                raise
            time.sleep(1 * (attempt + 1))
    return {}

def collect_cat(cat, depth, max_depth, visited):
    if depth > max_depth or cat in visited or len(visited) > 5000:
        return []
    visited.add(cat)
    pages = []
    cmcontinue = None
    while True:
        params = {"action": "query", "list": "categorymembers", "cmtitle": cat,
                  "cmlimit": "500", "cmtype": "page|subcat"}
        if cmcontinue:
            params.update(cmcontinue)
        try:
            data = api_call(params)
        except Exception as e:
            print(f"    [ERROR] {cat}: {e}")
            break
        members = data.get("query", {}).get("categorymembers", [])
        subcats = []
        for m in members:
            title = m["title"]
            if m["ns"] == 14:
                subcats.append(title)
            elif m["ns"] == 0:
                pages.append(title)
        for sc in subcats:
            if sc not in visited:
                pages.extend(collect_cat(sc, depth + 1, max_depth, visited))
        if "continue" in data:
            cmcontinue = data["continue"]
            time.sleep(0.15)
        else:
            break
    return pages

# 目标分类
CATS = [
    "Category:网络流行语",
    "Category:中国大陆网络用语",
    "Category:中国互联网文化",
    "Category:网络文化",
    "Category:流行语",
    "Category:网络用语",
    "Category:互联网用语",
    "Category:中国网络文化",
]

# 采集
raw = set()
for cat in CATS:
    print(f"\n=== {cat} ===")
    try:
        titles = collect_cat(cat, depth=0, max_depth=1, visited=set())
        cat_raw = set()
        for t in titles:
            clean = t.split("（")[0].split("(")[0].strip()
            if HANZI_RE.match(clean):
                cat_raw.add(clean)
                raw.add(clean)
        print(f"  有效: {len(titles)} -> 纯汉字 {len(cat_raw)}")
    except Exception as e:
        print(f"  [SKIP] {e}")
    # 分类间冷却 3 秒，防 429
    time.sleep(3)

print(f"\n=== 总计纯汉字: {len(raw)} ===")

# 注音
words = sorted(raw)
result = []
for i in range(0, len(words), 2000):
    chunk = words[i:i+2000]
    py_flat = pinyin(chunk, style=Style.NORMAL, heteronym=False)
    idx = 0
    for word in chunk:
        n = len(word)
        syls = py_flat[idx:idx+n]
        idx += n
        py_joined = "".join(s[0] for s in syls if s)
        if py_joined and py_joined.isascii() and py_joined.islower():
            result.append((word, py_joined))
    print(f"  注音 {i+len(chunk)}/{len(words)}")

print(f"\n=== 最终: {len(result)} 词 ===")

# 写入
out_dir = os.path.join(os.path.dirname(__file__), "output", "domains")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "network_slang.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("# 泰深输入法专业词库 — network_slang\n")
    f.write("# 格式：词 拼音（每行一个，制表符分隔）\n")
    f.write("# 来源：中文维基百科（CC BY-SA 4.0，https://zh.wikipedia.org）\n")
    for w, py in result:
        f.write(f"{w}\t{py}\n")
print(f"写入 {out_path}: {len(result)} 词")

# 与 system_dict 对比
import sqlite3
sys_db = os.path.join(os.path.dirname(__file__), "output", "system_dict.db")
if os.path.exists(sys_db):
    conn = sqlite3.connect(sys_db)
    cur = conn.cursor()
    new = [(w, py) for w, py in result if not cur.execute(
        "SELECT 1 FROM system_dict WHERE word=? LIMIT 1", (w,)).fetchone()]
    conn.close()
    print(f"system_dict 新增: {len(new)}/{len(result)}")
    if new:
        print("新增样例（前 20）:")
        for w, py in new[:20]:
            print(f"  {w}\t{py}")
