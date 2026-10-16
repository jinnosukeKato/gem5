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
import math
import os
import re
import sys
from collections import defaultdict

DIR_RE = re.compile(r"^(?P<seq>[^_]+)_(?P<cap>\d+(?:KiB|MiB|GiB))_(?P<policy>.+)$")
KEYS = {
    "requests_ime": "system.player.requests::IME",
    "requests_fme": "system.player.requests::FME",
    "demand_accesses": "system.refcache.demandAccesses::total",
    "hits": "system.refcache.demandHits::total",
    "misses": "system.refcache.demandMisses::total",
    "demand_miss_rate": "system.refcache.demandMissRate::total",
    "dram_bytes_read": "system.mem_ctrl.dram.bytesRead::total",
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
        r["dram_mib"] = r["dram_read_bursts"] * 64 / (1024 * 1024) if not math.isnan(r["dram_read_bursts"]) else float("nan")
        r["reduction_vs_lru"] = (lru["dram_read_bursts"] - x) / lru["dram_read_bursts"] if lru else float("nan")
        gap = (lru["dram_read_bursts"] - opt["dram_read_bursts"]) if (lru and opt) else 0
        r["gap_closed"] = (lru["dram_read_bursts"] - x) / gap if gap > 0 else float("nan")
        r["time_ratio_vs_lru"] = r["sim_seconds"] / lru["sim_seconds"] if lru else float("nan")
        r["row_hit_rate"] = r["dram_row_hits"] / r["dram_read_bursts"] if r["dram_read_bursts"] else float("nan")
    return rows


def cap_bytes(cap):
    n = int(re.match(r"\d+", cap).group())
    return n << {"KiB": 10, "MiB": 20, "GiB": 30}[cap[-3:]]


def fmt_int(v, width):
    return f"{'nan':>{width}}" if math.isnan(v) else f"{int(v):{width}d}"


def fmt_pct(v, width, prec=1):
    return f"{'nan':>{width}}" if math.isnan(v) else f"{v * 100:{width-1}.{prec}f}%"


def fmt_float(v, width, prec=3):
    return f"{'nan':>{width}}" if math.isnan(v) else f"{v:{width}.{prec}f}"


def main():
    p = argparse.ArgumentParser(description="gem5 の結果を置換ポリシごとに比較する")
    p.add_argument("root", help="<seq>_<容量>_<ポリシ> のディレクトリが並ぶ場所")
    p.add_argument("-o", "--output", help="CSV の出力先")
    p.add_argument("--raw-only", action="store_true", help="生の値の表のみ表示")
    p.add_argument("--cmp-only", action="store_true", help="比較表のみ表示")
    p.add_argument("--plot-dir", help="グラフの出力先ディレクトリ (指定時のみグラフを描画)")
    a = p.parse_args()
    rows = add_ratios(collect(a.root))
    if not rows:
        sys.exit(f"no results under {a.root}")
    order = {"lru": 0, "opt": 1}
    rows.sort(key=lambda r: (r["seq"], cap_bytes(r["cap"]), order.get(r["policy"], 2), r["policy"]))

    show_raw = not a.cmp_only
    show_cmp = not a.raw_only

    if show_raw:
        raw_hdr = f"{'seq':6}{'cap':>8}{'policy':>8}{'accesses':>11}{'misses':>11}{'miss_rate':>11}{'dram_bytes':>13}{'dram_bursts':>12}{'slot_cyc':>10}{'sim_sec':>10}"
        print("=== 生の値 (Raw Statistics) ===")
        print(raw_hdr)
        print("-" * len(raw_hdr))
        for r in rows:
            print(
                f"{r['seq']:6}{r['cap']:>8}{r['policy']:>8}"
                f"{fmt_int(r['demand_accesses'], 11)}"
                f"{fmt_int(r['misses'], 11)}"
                f"{fmt_pct(r['demand_miss_rate'], 11, prec=2)}"
                f"{fmt_int(r['dram_bytes_read'], 13)}"
                f"{fmt_int(r['dram_read_bursts'], 12)}"
                f"{fmt_float(r['slot_cycles_mean'], 10, prec=1)}"
                f"{fmt_float(r['sim_seconds'], 10, prec=4)}"
            )
        if show_cmp:
            print()

    if show_cmp:
        cmp_hdr = f"{'seq':6}{'cap':>8}{'policy':>8}{'DRAM MiB':>10}{'削減率':>8}{'差埋め':>8}{'行ヒット':>8}{'時間比':>8}"
        print("=== 比較値 (Comparison vs LRU / OPT) ===")
        print(cmp_hdr)
        print("-" * len(cmp_hdr))
        for r in rows:
            print(
                f"{r['seq']:6}{r['cap']:>8}{r['policy']:>8}"
                f"{fmt_float(r['dram_mib'], 10, prec=1)}"
                f"{fmt_pct(r['reduction_vs_lru'], 8, prec=1)}"
                f"{fmt_pct(r['gap_closed'], 8, prec=0)}"
                f"{fmt_pct(r['row_hit_rate'], 8, prec=1)}"
                f"{fmt_float(r['time_ratio_vs_lru'], 8, prec=3)}"
            )

    csv_path = a.output
    if not csv_path and a.plot_dir:
        csv_path = os.path.join(a.root, "summary.csv")

    if csv_path:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nsaved {csv_path}")

    if a.plot_dir:
        import subprocess

        plot_script = os.path.join(os.path.dirname(__file__), "plot.py")
        cmd = ["uv", "run", "--with", "matplotlib", "python3", plot_script, csv_path, "-o", a.plot_dir]
        print(f"\n[Plotting] running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
