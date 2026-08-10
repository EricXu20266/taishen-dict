#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
词库同步：dict 产物 → taishenIME resources/
═══════════════════════════════════════════════════
1. 读 output/VERSION.json 清单
2. 同步前对账：产物 sha256 必须与清单一致（防产物被改/清单过期）
3. 复制 system_dict.db / domains/domains.db / common.db / VERSION.json
   + common_dict.txt（源）+ output/domains/*.txt（35 个可读源）→ IME resources/
4. 同步后对账：IME 侧 sha256 与清单一致（确认复制完整）

用法：
    python tools/sync_to_ime.py [ime_root]
    # 默认 ime_root = ../../taishenIME
"""

import hashlib
import json
import os
import shutil
import sys

DICT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IME = os.path.normpath(os.path.join(DICT_ROOT, "..", "taishenIME"))


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ime_root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IME)
    out_dir = os.path.join(DICT_ROOT, "output")
    ver_path = os.path.join(out_dir, "VERSION.json")

    if not os.path.exists(ver_path):
        print(f"[FAIL] 缺失清单 {ver_path}——先跑 python pipeline.py")
        return 1
    with open(ver_path, encoding="utf-8") as f:
        manifest = json.load(f)
    print(f"版本: {manifest['version']}（git {manifest['git_commit']}，"
          f"构建于 {manifest['built_at']}）")

    # 同步前对账：产物 vs 清单
    for rel in manifest["dicts"]:
        src = os.path.join(out_dir, rel)
        if not os.path.exists(src):
            print(f"[FAIL] 产物缺失 {src}——与清单不符，中止")
            return 1
        if sha256(src) != manifest["dicts"][rel]["sha256"]:
            print(f"[FAIL] {rel} 哈希与清单不符（产物被修改？）——中止，请重跑 pipeline")
            return 1
    print(f"同步前对账通过：{len(manifest['dicts'])} 个词库与清单一致")

    # 复制（dict → ime）
    copied = []
    for rel in list(manifest["dicts"]) + ["VERSION.json"]:
        src = os.path.join(out_dir, rel)
        dst = os.path.join(ime_root, "resources", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)

    # 源文件：common_dict.txt（curate）→ resources/
    shutil.copy2(os.path.join(DICT_ROOT, "curate", "common_dict.txt"),
                 os.path.join(ime_root, "resources", "common_dict.txt"))
    copied.append("common_dict.txt")

    # 领域 txt（35 个产物，IME 侧可读源）→ resources/domains/
    dom_src = os.path.join(out_dir, "domains")
    dom_dst = os.path.join(ime_root, "resources", "domains")
    os.makedirs(dom_dst, exist_ok=True)
    for fn in sorted(os.listdir(dom_src)):
        if fn.endswith(".txt"):
            shutil.copy2(os.path.join(dom_src, fn), os.path.join(dom_dst, fn))
    copied.append(f"domains/*.txt ({len([f for f in os.listdir(dom_src) if f.endswith('.txt')])} 个)")

    # 同步后对账：IME 侧 vs 清单
    ok = True
    for rel in manifest["dicts"]:
        dst = os.path.join(ime_root, "resources", rel)
        if sha256(dst) != manifest["dicts"][rel]["sha256"]:
            print(f"[FAIL] 同步后 {rel} 哈希不一致")
            ok = False
    if not ok:
        return 1

    print(f"同步后对账通过（IME: {ime_root}）")
    print(f"已复制 {len(copied)} 项：{', '.join(copied)}")
    print(f"IME 词库版本 = {manifest['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
