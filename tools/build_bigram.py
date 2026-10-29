#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bigram 搭配表一键构建：wiki dump → 语料 → 相邻词共现 → output/bigram.db。

为什么单独一条命令而不塞进 pipeline.py：
    语料提取要读 3.4 GB 的 dump（5 万页约 6 分钟），统计再 6 分钟。
    词库 pipeline 是月度高频迭代，bigram 是低频重建——绑在一起会拖慢日常构建。

用法：

    python tools/build_bigram.py                # 默认 5 万页，产出 output/bigram.db
    python tools/build_bigram.py --limit 20000  # 小规模试跑
    python tools/build_bigram.py --reuse-corpus # 语料已在 tmp/corpus，跳过提取

产物登记进 output/VERSION.json 的 dicts（pipeline.py write_version 已包含 bigram.db），
随后 tools/sync_to_ime.py 会把它同步到 taishenIME/resources/。

引擎侧读取位置：与 taishenIME 的 DLL 同目录的 bigram.db
（C++ 在 ActivateEx 里调 engine_set_bigram_path，后台线程加载）。
"""
import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP_GLOB = os.path.join(ROOT, "tmp", "dumps", "zhwiki-latest-pages-articles.xml.bz2")
CORPUS_DIR = os.path.join(ROOT, "tmp", "corpus")
CORPUS_SCRIPT = os.path.join(ROOT, "tools", "collect", "wiki_corpus.py")
STAT_SCRIPT = os.path.join(ROOT, "tools", "build_context_prior.py")
DICT_DB = os.path.join(ROOT, "output", "system_dict.db")
OUT_DB = os.path.join(ROOT, "output", "bigram.db")


def run(cmd):
    print("»", " ".join(cmd), flush=True)
    ret = subprocess.call(cmd)
    if ret != 0:
        sys.exit(f"失败（exit={ret}）：{cmd[1]}")


def main():
    ap = argparse.ArgumentParser(description="构建 bigram 搭配表")
    ap.add_argument("--dump", default=DUMP_GLOB, help="wiki dump 路径")
    ap.add_argument("--limit", type=int, default=50000, help="提取页数（0=全量，很慢）")
    ap.add_argument("--reuse-corpus", action="store_true", help="复用 tmp/corpus，跳过提取")
    ap.add_argument("--top", type=int, default=400000, help="保留搭配对数上限")
    ap.add_argument("--min-count", type=int, default=2, help="最小共现次数")
    args = ap.parse_args()

    py = sys.executable
    if not args.reuse_corpus:
        if not os.path.exists(args.dump):
            sys.exit(f"dump 不存在: {args.dump}\n先按 README 下载 zhwiki dump 到 tmp/dumps/")
        os.makedirs(CORPUS_DIR, exist_ok=True)
        for f in glob.glob(os.path.join(CORPUS_DIR, "corpus_*.txt")):
            os.remove(f)
        cmd = [py, CORPUS_SCRIPT, "--xml", args.dump, "--out-dir", CORPUS_DIR]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        run(cmd)

    if not os.path.isdir(CORPUS_DIR) or not glob.glob(os.path.join(CORPUS_DIR, "*.txt")):
        sys.exit(f"语料目录为空: {CORPUS_DIR}")
    if not os.path.exists(DICT_DB):
        sys.exit(f"缺 system_dict.db，先跑 python pipeline.py")

    run([
        py, STAT_SCRIPT,
        "--corpus", CORPUS_DIR,
        "--dict", DICT_DB,
        "--out", OUT_DB,
        "--top", str(args.top),
        "--min-count", str(args.min_count),
    ])
    print(f"\n完成：{OUT_DB}")
    print("下一步：python pipeline.py（重新生成 VERSION.json 登记 bigram.db）")
    print("        python tools/sync_to_ime.py（同步到 taishenIME/resources/）")


if __name__ == "__main__":
    main()
