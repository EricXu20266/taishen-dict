#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""词库版本号生成（两个仓库共用同一版本号空间）。

版本号 = {YYYY}.{MM}.{DD}.{n}，n = 当日最大序号 + 1。

序号同时参考本仓库 `output/VERSION.json` 与 IME 侧
`taishenIME/resources/VERSION.json`：局部重建（只重建 common.db）也会调
这里，若只看本仓库，很容易生成与 IME 已发布版本重复、甚至更小的号，
`sync_to_ime.py` 会把 IME 的版本指针拉回去。

IME 侧路径可用环境变量 `TAISHEN_IME_VERSION` 覆盖（换机器/换目录布局时）。
"""

import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output")
IME_VERSION_PATH = os.environ.get(
    "TAISHEN_IME_VERSION",
    os.path.join(os.path.dirname(ROOT), "taishenIME", "resources", "VERSION.json"),
)


def today_seq(path: str, today: str) -> int:
    """读 manifest 的 version；只有同日版本才返回序号，其余（含文件缺失）返回 0。"""
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f).get("version", "")
    except Exception:
        return 0
    if not prev.startswith(today + "."):
        return 0
    try:
        return int(prev.rsplit(".", 1)[-1])
    except Exception:
        return 0


def next_version() -> str:
    """当日下一个版本号（本仓库与 IME 侧取最大值 + 1）。"""
    today = datetime.now().strftime("%Y.%m.%d")
    n = max(
        today_seq(os.path.join(OUT_DIR, "VERSION.json"), today),
        today_seq(IME_VERSION_PATH, today),
    )
    return f"{today}.{n + 1}"
