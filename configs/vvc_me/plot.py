# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "matplotlib",
# ]
# ///
"""
plot.py
gem5 の評価結果 (summary.csv) から、置換ポリシの比較グラフを描画する。

summarize.py とは疎結合になっており、CSV を入力にして何度でも単独で再描画可能。

使い方:
  uv run gem5/configs/vvc_me/plot.py build/gem5/summary.csv -o build/gem5/plots
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

# Seaborn-whitegrid スタイルと日本語フォント設定
STYLE_CONFIG = {
    "font.family": ["Noto Sans CJK JP", "sans-serif"],
    "font.size": 11,
    "axes.titlesize": 11,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.grid.axis": "y",
    "grid.alpha": 0.35,
    "grid.linestyle": "--",
}

# グラフ対象ポリシー
POLICY_ORDER = ["lru", "mv5", "opt"]
POLICY_LABELS = {
    "lru": "LRU",
    "mv5": "提案手法",
    "opt": "理論下限",
}

# 配色の一貫性を保証 (提案手法はオレンジ C1、LRU は青 C0、理論下限は緑 C2 / 基準線は gray)
POLICY_COLORS = {
    "lru": "C0",
    "mv5": "C1",
    "opt": "C2",
}

CAP_ORDER = ["768KiB", "3MiB", "12MiB"]


def load_csv(csv_path):
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (ValueError, TypeError):
                pass
    return rows


def group_data(rows):
    """(cap, seq, policy) -> row のマップと、ユニークな seq リストを返す"""
    data = defaultdict(dict)
    seqs = []
    for r in rows:
        seq = r["seq"]
        if seq not in seqs:
            seqs.append(seq)
        data[(r["cap"], seq)][r["policy"]] = r
    return data, sorted(seqs)


def plot_normalized_misses_per_capacity(rows, out_dir):
    """
    メイングラフ: 正規化ミス数 (OPT=1.0)
    理論下限 (OPT) を 1.0 としたときの LRU と提案手法のミス数倍率
    容量ごとに別画像ファイルで出力
    """
    data, seqs = group_data(rows)
    seq_labels = seqs + ["平均"]
    x = np.arange(len(seq_labels))
    width = 0.32
    outputs = []

    for cap in CAP_ORDER:
        fig, ax = plt.subplots(figsize=(8.5, 4.2))

        for p_idx, pol in enumerate(["lru", "mv5"]):
            vals = []
            for s in seqs:
                opt_entry = data.get((cap, s), {}).get("opt")
                target_entry = data.get((cap, s), {}).get(pol)
                if opt_entry and target_entry and opt_entry["misses"] > 0:
                    norm_val = target_entry["misses"] / opt_entry["misses"]
                else:
                    norm_val = 1.0
                vals.append(norm_val)

            avg_val = np.mean(vals) if vals else 1.0
            all_vals = vals + [avg_val]

            offset = (p_idx - 0.5) * width
            ax.bar(
                x + offset,
                all_vals,
                width,
                label=POLICY_LABELS[pol],
                color=POLICY_COLORS[pol],
                edgecolor="black",
                linewidth=0.5,
            )

        # 理論下限 (OPT = 1.0) の基準線
        ax.axhline(1.0, color="gray", linestyle="--", linewidth=1.2, label="理論下限 (1.0)")
        ax.axvline(len(seqs) - 0.5, color="gray", linestyle=":", alpha=0.6)

        ax.set_xticks(x)
        ax.set_xticklabels(seq_labels)
        ax.set_xlabel("映像シーケンス")
        ax.set_ylabel("正規化ミス数 (OPT=1.0)")
        ax.set_ylim(0, 2.0)
        ax.set_yticks(np.arange(0.0, 2.25, 0.25))
        ax.legend(
            loc="lower right",
            bbox_to_anchor=(1.0, 1.02),
            ncol=3,
            framealpha=0.9,
        )

        plt.tight_layout()
        out_path = os.path.join(out_dir, f"normalized_misses_{cap}.png")
        fig.savefig(out_path, dpi=300)
        plt.close(fig)
        outputs.append(out_path)

    return outputs


def plot_capacity_scaling_bars(rows, out_dir):
    """
    容量スケーリング特性を棒グラフで描画
    左: 平均 DRAM 読み出し量 [MiB] (LRU, 提案手法, 理論下限)
    右: 平均 正規化ミス数 (OPT=1.0) (LRU, 提案手法)
    """
    data, seqs = group_data(rows)

    avg_dram = defaultdict(dict)
    avg_norm_misses = defaultdict(dict)
    for cap in CAP_ORDER:
        for pol in ["lru", "mv5", "opt"]:
            d_list = []
            m_list = []
            for s in seqs:
                opt_entry = data.get((cap, s), {}).get("opt")
                entry = data.get((cap, s), {}).get(pol)
                if entry:
                    d_list.append(entry["dram_mib"])
                if opt_entry and entry and opt_entry["misses"] > 0:
                    m_list.append(entry["misses"] / opt_entry["misses"])
            avg_dram[pol][cap] = np.mean(d_list)
            avg_norm_misses[pol][cap] = np.mean(m_list)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    x = np.arange(len(CAP_ORDER))
    width = 0.25

    # 左: 平均 DRAM 読み出し量
    max_dram = 0
    for p_idx, pol in enumerate(POLICY_ORDER):
        vals = [avg_dram[pol][c] for c in CAP_ORDER]
        max_dram = max(max_dram, max(vals))
        offset = (p_idx - 1) * width
        ax1.bar(
            x + offset,
            vals,
            width,
            label=POLICY_LABELS[pol],
            color=POLICY_COLORS[pol],
            edgecolor="black",
            linewidth=0.5,
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(CAP_ORDER)
    ax1.set_xlabel("キャッシュ容量")
    ax1.set_ylabel("平均 DRAM読み出し量 [MiB]")
    ax1.set_ylim(0, max_dram * 1.25)
    ax1.legend(loc="upper right", framealpha=0.9)

    # 右: 平均 正規化ミス数 (OPT=1.0)
    width2 = 0.3
    for p_idx, pol in enumerate(["lru", "mv5"]):
        vals = [avg_norm_misses[pol][c] for c in CAP_ORDER]
        offset = (p_idx - 0.5) * width2
        ax2.bar(
            x + offset,
            vals,
            width2,
            label=POLICY_LABELS[pol],
            color=POLICY_COLORS[pol],
            edgecolor="black",
            linewidth=0.5,
        )

    ax2.axhline(1.0, color="gray", linestyle="--", linewidth=1.2, label="理論下限 (1.0)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(CAP_ORDER)
    ax2.set_xlabel("キャッシュ容量")
    ax2.set_ylabel("平均 正規化ミス数 (OPT=1.0)")
    ax2.set_ylim(0, 2.0)
    ax2.set_yticks(np.arange(0.0, 2.25, 0.25))
    ax2.legend(loc="upper left", framealpha=0.9)

    plt.tight_layout()
    out_path = os.path.join(out_dir, "capacity_scaling.png")
    fig.savefig(out_path, dpi=300)
    plt.close(fig)
    return [out_path]


def plot_dram_reads_per_capacity(rows, out_dir):
    """
    DRAM 読み出し量 [MiB] をキャッシュ容量ごとに別画像ファイルとして保存
    """
    data, seqs = group_data(rows)
    seq_labels = seqs + ["平均"]
    x = np.arange(len(seq_labels))
    width = 0.25
    outputs = []

    for cap in CAP_ORDER:
        fig, ax = plt.subplots(figsize=(8.5, 4.2))
        max_val = 0

        for p_idx, pol in enumerate(POLICY_ORDER):
            vals = []
            for s in seqs:
                entry = data.get((cap, s), {}).get(pol)
                v = entry["dram_mib"] if entry else 0
                vals.append(v)
            avg_val = np.mean(vals) if vals else 0
            all_vals = vals + [avg_val]
            max_val = max(max_val, max(all_vals) if all_vals else 0)

            offset = (p_idx - 1) * width
            ax.bar(
                x + offset,
                all_vals,
                width,
                label=POLICY_LABELS[pol],
                color=POLICY_COLORS[pol],
                edgecolor="black",
                linewidth=0.5,
            )

        ax.axvline(len(seqs) - 0.5, color="gray", linestyle=":", alpha=0.6)
        ax.set_xticks(x)
        ax.set_xticklabels(seq_labels)
        ax.set_xlabel("映像シーケンス")
        ax.set_ylabel("DRAM読み出し量 [MiB]")
        ax.set_ylim(0, max_val * 1.08)
        ax.legend(
            loc="lower right",
            bbox_to_anchor=(1.0, 1.02),
            ncol=3,
            framealpha=0.9,
        )

        plt.tight_layout()
        out_path = os.path.join(out_dir, f"dram_reads_{cap}.png")
        fig.savefig(out_path, dpi=300)
        plt.close(fig)
        outputs.append(out_path)

    return outputs


def plot_all(rows_or_csv, out_dir):
    """すべてのグラフを描画して出力先パスのリストを返す"""
    if isinstance(rows_or_csv, str):
        rows = load_csv(rows_or_csv)
    else:
        rows = rows_or_csv

    os.makedirs(out_dir, exist_ok=True)

    # Seaborn-whitegrid スタイルの適用
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(STYLE_CONFIG)

    outputs = []
    outputs.extend(plot_normalized_misses_per_capacity(rows, out_dir))
    outputs.extend(plot_capacity_scaling_bars(rows, out_dir))
    outputs.extend(plot_dram_reads_per_capacity(rows, out_dir))
    return outputs


def main():
    p = argparse.ArgumentParser(description="summary.csv から比較グラフを描画する")
    p.add_argument("csv_path", help="入力となる summary.csv のパス")
    p.add_argument("-o", "--output-dir", default="build/gem5/plots", help="グラフの出力先ディレクトリ")
    a = p.parse_args()

    if not os.path.exists(a.csv_path):
        sys.exit(f"CSV file not found: {a.csv_path}")

    print(f"Loading {a.csv_path}...")
    outputs = plot_all(a.csv_path, a.output_dir)
    print(f"Generated {len(outputs)} plots in {a.output_dir}:")
    for out in outputs:
        print(f"  - {out}")


if __name__ == "__main__":
    main()
