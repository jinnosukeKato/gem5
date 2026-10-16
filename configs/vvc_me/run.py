"""
run.py
PipelinedTracePlayer で動き探索の参照読み出しを再生し、参照キャッシュと DDR4 を通して評価する。

構成:
  PipelinedTracePlayer --> Cache (参照キャッシュ) --> SystemXBar --> MemCtrl (DDR4_2400_16x4)

  * キャッシュのセット数 (size / 64B / assoc) は 2 のべき乗である必要がある (gem5 の制約)。
    768KiB / 3MiB / 12MiB は 12 ウェイなら 1024 / 4096 / 16384 セットになる
  * DRAM は RoRaBaCoCh の 1 チャネル。トレースのアドレスはこの配置を前提に作る
  * DRAM のデータは保持しない (null = True)。タイミングと統計だけを見る

使い方 (gem5 のディレクトリで):
  build/NULL/gem5.opt configs/vvc_me/run.py --trace trace.bin
  build/NULL/gem5.opt -d m5out/s201_3MiB configs/vvc_me/run.py --trace s201.bin --cache-size 3MiB
"""

import argparse

import m5
from m5.objects import (
    DDR4_2400_16x4,
    AddrRange,
    Cache,
    LRURP,
    MemCtrl,
    PipelinedTracePlayer,
    Root,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
)

LINE_BYTES = 64
# DDR4_2400_16x4 の 1 チャネルの容量 (1GiB/チップ × 16 チップ × 2 ランク)
DRAM_CHANNEL_SIZE = "32GiB"


def parse_args():
    p = argparse.ArgumentParser(description="動き探索の参照キャッシュの gem5 シミュレーション")
    p.add_argument("--trace", required=True, help="トレースファイル (configs/vvc_me/me_trace.py の形式)")
    p.add_argument("--clock", default="600MHz", help="プレイヤーとキャッシュのクロック")
    p.add_argument("--cache-size", default="3MiB")
    p.add_argument("--assoc", type=int, default=12)
    p.add_argument("--tag-latency", type=int, default=2, help="[cycle]")
    p.add_argument("--data-latency", type=int, default=2, help="[cycle]")
    p.add_argument("--response-latency", type=int, default=2, help="[cycle]")
    p.add_argument("--mshrs", type=int, default=32)
    p.add_argument("--tgts-per-mshr", type=int, default=8)
    p.add_argument("--max-outstanding", type=int, nargs=2, default=[16, 16], metavar=("IME", "FME"))
    p.add_argument("--issue-width", type=int, nargs=2, default=[1, 1], metavar=("IME", "FME"))
    return p.parse_args()


def check_sets(size: str, assoc: int) -> None:
    """gem5 の起動時エラー (セット数が 2 のべき乗でない) を、分かりやすいメッセージで先に出す"""
    from m5.util import fatal
    from m5.util.convert import toMemorySize

    lines = toMemorySize(size) // LINE_BYTES
    sets, rem = divmod(lines, assoc)
    if rem or sets & (sets - 1):
        # gem5 の中では SystemExit に文字列を渡すと異常終了するので fatal で止める
        fatal(
            f"cache {size} / {LINE_BYTES}B / {assoc}-way gives {lines / assoc:g} sets; "
            "the number of sets must be a power of two"
        )


def build_system(args) -> System:
    check_sets(args.cache_size, args.assoc)

    system = System()
    system.clk_domain = SrcClockDomain(clock=args.clock, voltage_domain=VoltageDomain())
    system.mem_mode = "timing"
    system.cache_line_size = LINE_BYTES
    system.mem_ranges = [AddrRange(DRAM_CHANNEL_SIZE)]

    system.player = PipelinedTracePlayer(
        trace_file=args.trace,
        max_outstanding=args.max_outstanding,
        issue_width=args.issue_width,
    )

    system.refcache = Cache(
        size=args.cache_size,
        assoc=args.assoc,
        tag_latency=args.tag_latency,
        data_latency=args.data_latency,
        response_latency=args.response_latency,
        mshrs=args.mshrs,
        tgts_per_mshr=args.tgts_per_mshr,
        replacement_policy=LRURP(),  # TODO: OPT / 提案手法の置換ポリシに差し替える
    )
    system.player.port = system.refcache.cpu_side

    system.membus = SystemXBar()
    system.refcache.mem_side = system.membus.cpu_side_ports
    system.system_port = system.membus.cpu_side_ports

    system.mem_ctrl = MemCtrl()
    system.mem_ctrl.dram = DDR4_2400_16x4(range=system.mem_ranges[0])
    system.mem_ctrl.dram.addr_mapping = "RoRaBaCoCh"
    system.mem_ctrl.dram.null = True
    system.mem_ctrl.port = system.membus.mem_side_ports
    return system


def main() -> None:
    args = parse_args()
    system = build_system(args)
    root = Root(full_system=False, system=system)
    m5.instantiate()
    event = m5.simulate()
    print(f"Exiting @ tick {m5.curTick()} because {event.getCause()}")


if __name__ == "__m5_main__":
    main()
