#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新 VERSION.json（局部重建场景：只重建了 common.db）。

全量构建走 `pipeline.py`（它会调 `write_version()`）；
单独重建 common.db（`tools/build_common_db.py`）后用本脚本重算清单，
避免手写 sha256 / 条数 / 版本号。

用法：
    python tools/update_version.py
"""

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


def main() -> int:
    ver_path = os.path.join(OUT_DIR, "VERSION.json")
    common_db = os.path.join(OUT_DIR, "common.db")
    if not os.path.exists(common_db):
        print(f"错误：{common_db} 不存在，先跑 tools/build_common_db.py")
        return 1
    if not os.path.exists(ver_path):
        print(f"错误：{ver_path} 不存在，先跑一次全量 pipeline.py")
        return 1

    with open(ver_path, encoding="utf-8") as f:
        manifest = json.load(f)

    conn = sqlite3.connect(common_db)
    rows = conn.execute("select count(*) from common_words").fetchone()[0]
    conn.close()

    new_entry = {
        "sha256": sha256(common_db),
        "size": os.path.getsize(common_db),
        "rows": {"common_words": rows},
    }
    # 幂等：内容没变就不 bump 版本号，避免反复跑脚本把版本号刷高
    changed = manifest["dicts"].get("common.db") != new_entry
    old = manifest["version"]
    manifest["dicts"]["common.db"] = new_entry
    if changed:
        manifest["version"] = next_version()
    manifest["built_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with open(ver_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"VERSION: {old} -> {manifest['version']} (changed={changed})")
    print(f"common.db rows: {rows} sha256: {new_entry['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
