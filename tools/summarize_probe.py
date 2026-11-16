#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 probe_context 的输出，给出三项验收统计。

probe_context（taishenIME/engine/examples/probe_context.rs）每例打印一行：

    prev + next_pinyin 「词」(档位N 词库第R位): off=Some(a) on=Some(b) IMPROVED a->b

本脚本汇总：总提升率、平均提升位次、按档位分组的提升率，
并列出未提升的案例（用于判断「本来就第一」还是「权重不足」）。

用法：

    cd taishenIME/engine
    cargo run --release --example probe_context -- ..\\tmp\\context_cases.tsv > ..\\tmp\\probe.log
    python <taishen-dict>/tools/summarize_probe.py --log ..\\tmp\\probe.log
"""
import argparse
import re

LINE = re.compile(
    r"^(?P<prev>\S+) \+ (?P<py>\S+) 「(?P<word>[^」]+)」"
    r"\(档位(?P<tier>\d) 词库第(?P<rank>\d+)位\):"
    r" off=(?P<off>\S+) on=(?P<on>\S+) (?P<tag>.+)$"
)
# 「无->N」表示原本不在候选列表，按第 6 位计（保守：只算 6 位提升）
MISS_RANK = 6


def parse_rank(text):
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def main():
    ap = argparse.ArgumentParser(description="汇总 probe_context 结果")
    ap.add_argument("--log", required=True, help="probe_context 输出日志")
    ap.add_argument("--show-same", action="store_true", help="列出未提升案例")
    args = ap.parse_args()

    stats = {}
    rows = []
    with open(args.log, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = LINE.match(line.strip())
            if not m:
                continue
            tier = int(m["tier"])
            off, on = parse_rank(m["off"]), parse_rank(m["on"])
            slot = stats.setdefault(tier, {"n": 0, "imp": 0, "same": 0, "worse": 0, "gain": 0})
            slot["n"] += 1
            tag = m["tag"]
            if tag.startswith("IMPROVED"):
                slot["imp"] += 1
                if off is not None and on is not None:
                    slot["gain"] += off - on
                elif off is None:
                    slot["gain"] += max(0, MISS_RANK - (on or 0))
            elif tag.startswith("WORSE"):
                slot["worse"] += 1
            else:
                slot["same"] += 1
            rows.append((m["prev"], m["word"], tier, off, on, tag))

    total = sum(s["n"] for s in stats.values())
    if not total:
        raise SystemExit("日志里没有可解析的案例行")
    imp = sum(s["imp"] for s in stats.values())
    same = sum(s["same"] for s in stats.values())
    worse = sum(s["worse"] for s in stats.values())
    gain = sum(s["gain"] for s in stats.values())

    print(f"总案例 {total} | 提升 {imp} ({imp/total:.1%}) | 不变 {same} | 变差 {worse}")
    print(f"平均提升位次（按提升例均摊）: {gain/imp:.2f} 位\n")
    print("档位\t案例\t提升\t提升率\t平均提升位次")
    for tier in sorted(stats):
        s = stats[tier]
        avg = s["gain"] / s["imp"] if s["imp"] else 0
        print(f"{tier}\t{s['n']}\t{s['imp']}\t{s['imp']/s['n']:.0%}\t{avg:.2f}")

    if args.show_same:
        print("\n未提升案例：")
        for prev, word, tier, off, on, tag in rows:
            if not tag.startswith("IMPROVED"):
                print(f"  {prev} → {word}（档位{tier} off={off} on={on}）")


if __name__ == "__main__":
    main()
