#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新 VERSION.json（局部重建场景：只重建了某个词库产物）。

全量构建走 `pipeline.py`（它会调 `write_version()`）；
单独重建某个词库（`tools/build_common_db.py` / `tools/build_bigram.py`）后用本脚本
重算清单，避免手写 sha256 / 条数 / 版本号。

用法：
    python tools/update_version.py                      # 默认只刷 common.db（历史行为）
    python tools/update_version.py --dicts bigram.db    # 刷 bigram.db
    python tools/update_version.py --dicts common.db,bigram.db

内容没变不 bump 版本号（幂等）；任一产物变化则按共用版本号空间 +1。
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from versioning import OUT_DIR, next_version  # noqa: E402


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(db_path: str) -> dict:
    """各表行数（与 pipeline._table_counts 同口径）。"""
    conn = sqlite3.connect(db_path)
    try:
        return {
            r[0]: conn.execute(f"SELECT COUNT(*) FROM {r[0]}").fetchone()[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not r[0].startswith("sqlite")
        }
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新局部重建产物的清单条目")
    ap.add_argument("--dicts", default="common.db",
                    help="逗号分隔的词库相对路径（默认 common.db）")
    args = ap.parse_args()

    ver_path = os.path.join(OUT_DIR, "VERSION.json")
    if not os.path.exists(ver_path):
        print(f"错误：{ver_path} 不存在，先跑一次全量 pipeline.py")
        return 1

    targets = [t.strip() for t in args.dicts.split(",") if t.strip()]
    for rel in targets:
        if not os.path.exists(os.path.join(OUT_DIR, rel)):
            print(f"错误：output/{rel} 不存在，先跑对应构建脚本")
            return 1

    with open(ver_path, encoding="utf-8") as f:
        manifest = json.load(f)

    changed = False
    for rel in targets:
        path = os.path.join(OUT_DIR, rel)
        entry = {
            "sha256": sha256(path),
            "size": os.path.getsize(path),
            "rows": table_counts(path),
        }
        if manifest["dicts"].get(rel) != entry:
            changed = True
        manifest["dicts"][rel] = entry
        print(f"{rel}: rows={entry['rows']} sha256={entry['sha256'][:16]}")

    old = manifest["version"]
    if changed:
        manifest["version"] = next_version()
    manifest["built_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with open(ver_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"VERSION: {old} -> {manifest['version']} (changed={changed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
