#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输出：domains/*.txt（领域词库，引擎运行时自动加载）。"""

import os


def write(
    domain_entries: dict[str, list[tuple[str, str]]],
    out_dir: str,
) -> None:
    """
    按领域输出 txt 文件。

    格式每行：词\t拼音
    文件头：来源标注（CC BY-SA 署名要求）
    """
    os.makedirs(out_dir, exist_ok=True)

    for domain, entries in domain_entries.items():
        path = os.path.join(out_dir, f"{domain}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 泰深输入法专业词库 — {domain}\n")
            f.write("# 格式：词 拼音（每行一个，空格或制表符分隔）\n")
            f.write("# 来源：中文维基百科（CC BY-SA 4.0，https://zh.wikipedia.org）\n")
            for word, pinyin in entries:
                f.write(f"{word}\t{pinyin}\n")
        print(f"  [output] {path}: {len(entries):,} 条")
