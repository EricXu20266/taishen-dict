#!/usr/bin/env python3
"""Quick fix: rebuild sport + food domains only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import wiki
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
cfg = yaml.safe_load(open(os.path.join(ROOT, "domains.yaml"), "r", encoding="utf-8"))["domains"]

for name in ["food"]:
    c = cfg[name]
    print(f"Building {name}...")
    entries = wiki.collect_domain(name, c["wiki_categories"], max_depth=c.get("max_depth", 2))
    out = os.path.join(ROOT, "output", "domains", f"{name}.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# taishenIME domain — {name}\n")
        f.write("# format: word pinyin\n")
        f.write("# source: Wikipedia (CC BY-SA 4.0)\n")
        for w, py, d in entries:
            f.write(f"{w}\t{py}\n")
    print(f"  {name}: {len(entries)} entries -> {out}")

print("Done.")
