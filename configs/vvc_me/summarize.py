"""
summarize.py
run.py の出力ディレクトリ (m5out) の stats.txt を集めて、置換ポリシごとの比較表を作る。

ディレクトリ名は <seq>_<容量>_<ポリシ> (例: s201_3MiB_lru) を前提にする。
同じ <seq>_<容量> の lru と opt があれば、
  削減率         = (LRU − X) / LRU                 (DRAM 読み出しバースト数で比較)
  差を埋めた割合 = (LRU − X) / (LRU − OPT)
を出す。

使い方 (普通の Python で):
  python3 configs/vvc_me/summarize.py ../build/gem5 -o ../build/gem5/summary.csv
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

DIR_RE = re.compile(r"^(?P<seq>[^_]+)_(?P<cap>\d+(?:KiB|MiB|GiB))_(?P<policy>.+)$")
KEYS = {
    "requests_ime": "system.player.requests::IME",
    "requests_fme": "system.player.requests::FME",
    "hits": "system.refcache.demandHits::total",
    "misses": "system.refcache.demandMisses::total",
    "dram_read_bursts": "system.mem_ctrl.dram.readBursts",
    "dram_row_hits": "system.mem_ctrl.dram.readRowHits",
    "slot_cycles_mean": "system.player.slotCycles::mean",
    "sim_seconds": "simSeconds",
}


def read_stats(path):
    out = {}
    with open(path) as f:
        for line in f:
            if line.startswith("---------- End"):
                break
            parts = line.split()
            if len(parts) >= 2:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def collect(root):
    rows = []
    for name in sorted(os.listdir(root)):
        m = DIR_RE.match(name)
        stats = os.path.join(root, name, "stats.txt")
        if not m or not os.path.exists(stats):
            continue
        st = read_stats(stats)
        row = dict(m.groupdict())
        for k, key in KEYS.items():
            row[k] = st.get(key, float("nan"))
        rows.append(row)
    return rows


def add_ratios(rows):
    base = defaultdict(dict)
    for r in rows:
        base[(r["seq"], r["cap"])][r["policy"]] = r
    for r in rows:
        g = base[(r["seq"], r["cap"])]
        lru, opt = g.get("lru"), g.get("opt")
        x = r["dram_read_bursts"]
        r["reduction_vs_lru"] = (lru["dram_read_bursts"] - x) / lru["dram_read_bursts"] if lru else float("nan")
        gap = (lru["dram_read_bursts"] - opt["dram_read_bursts"]) if (lru and opt) else 0
        r["gap_closed"] = (lru["dram_read_bursts"] - x) / gap if gap > 0 else float("nan")
        r["time_ratio_vs_lru"] = r["sim_seconds"] / lru["sim_seconds"] if lru else float("nan")
        r["row_hit_rate"] = r["dram_row_hits"] / r["dram_read_bursts"] if r["dram_read_bursts"] else float("nan")
    return rows


def cap_bytes(cap):
    n = int(re.match(r"\d+", cap).group())
    return n << {"KiB": 10, "MiB": 20, "GiB": 30}[cap[-3:]]


def main():
    p = argparse.ArgumentParser(description="gem5 の結果を置換ポリシごとに比較する")
    p.add_argument("root", help="<seq>_<容量>_<ポリシ> のディレクトリが並ぶ場所")
    p.add_argument("-o", "--output", help="CSV の出力先")
    a = p.parse_args()
    rows = add_ratios(collect(a.root))
    if not rows:
        sys.exit(f"no results under {a.root}")
    order = {"lru": 0, "opt": 1}
    rows.sort(key=lambda r: (r["seq"], cap_bytes(r["cap"]), order.get(r["policy"], 2), r["policy"]))
    hdr = f"{'seq':6}{'cap':>8}{'policy':>8}{'DRAM MiB':>10}{'削減率':>8}{'差埋め':>8}{'行ヒット':>8}{'時間比':>8}"
    print(hdr)
    for r in rows:
        print(
            f"{r['seq']:6}{r['cap']:>8}{r['policy']:>8}{r['dram_read_bursts'] * 64 / 2**20:10.1f}"
            f"{r['reduction_vs_lru'] * 100:7.1f}%{r['gap_closed'] * 100:7.0f}%{r['row_hit_rate'] * 100:7.1f}%{r['time_ratio_vs_lru']:8.3f}"
        )
    if a.output:
        with open(a.output, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"saved {a.output}")


if __name__ == "__main__":
    main()
