"""
test_vvc_me.py
トレースの読み書き (me_trace.py) と、gem5 での再生 (run.py + PipelinedTracePlayer) のテスト。

実行 (gem5 のディレクトリで):
  python3 -m unittest discover -s configs/vvc_me/tests -v

  * 結合テストは build/NULL/gem5.opt を使う (環境変数 GEM5 で別のバイナリを指定できる)。
    バイナリがなければ結合テストはスキップする
"""

import os
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE.parent
GEM5_ROOT = CONFIG_DIR.parent.parent
sys.path.insert(0, str(CONFIG_DIR))

from me_trace import (  # noqa: E402
    FME,
    IME,
    LINE_BYTES,
    NO_NEXT,
    RECORD,
    TraceRecord,
    build_records,
    read_trace,
    write_trace,
)

GEM5 = Path(os.environ.get("GEM5", GEM5_ROOT / "build" / "NULL" / "gem5.opt"))
RUN_PY = CONFIG_DIR / "run.py"

# 768KiB / 64B / 12-way = 1024 セット。セット番号 = (addr >> 6) & 1023 (gem5 の既定の索引)
SMALL_CACHE = ("768KiB", 12)
N_SETS = 1024
SAME_SET_STRIDE = N_SETS * LINE_BYTES  # この間隔のアドレスは同じセットに入る


# ---------------------------------------------------------------------------
# トレースの読み書き
# ---------------------------------------------------------------------------
class TraceFormatTest(unittest.TestCase):
    def test_record_size_matches_cpp_fields(self):
        # uint32 + uint8 + uint64 + uint64 + uint32 + uint32 (詰め物なし)
        self.assertEqual(RECORD.size, 4 + 1 + 8 + 8 + 4 + 4)

    def test_roundtrip(self):
        recs = [
            TraceRecord(0, IME, 0x0, 2, 7, 0),
            TraceRecord(0, FME, 0x40, NO_NEXT, 9, 1),
            TraceRecord(3, IME, 0xFFFFFFC0, NO_NEXT, (1 << 32) - 1, 2),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.bin")
            self.assertEqual(write_trace(path, recs), 3)
            self.assertEqual(os.path.getsize(path), 3 * RECORD.size)
            self.assertEqual(list(read_trace(path)), recs)

    def test_build_records_next_access(self):
        recs = build_records([(0, IME, 0x0), (0, IME, 0x40), (1, FME, 0x0), (2, IME, 0x40)])
        self.assertEqual([r.seq for r in recs], [0, 1, 2, 3])
        # ライン 0x0 の次は seq 2、ライン 0x40 の次は seq 3、それ以降はなし
        self.assertEqual([r.opt_next_acc for r in recs], [2, 3, NO_NEXT, NO_NEXT])

    def test_build_records_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            build_records([(0, IME, 0x10)])  # 64B 境界でない
        with self.assertRaises(ValueError):
            build_records([(1, IME, 0x0), (0, IME, 0x40)])  # スロットが逆順
        with self.assertRaises(ValueError):
            build_records([(0, 0, 0x0)])  # 不明なステージ


# ---------------------------------------------------------------------------
# gem5 での再生
# ---------------------------------------------------------------------------
def parse_stats(path: Path) -> "OrderedDict[str, float]":
    """stats.txt を {統計名: 値} にする (最初の統計ブロックだけ)"""
    out: "OrderedDict[str, float]" = OrderedDict()
    for line in path.read_text().splitlines():
        if line.startswith("---------- End"):
            break
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("-"):
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return out


def reference_lru_misses(addrs, n_sets: int, ways: int) -> int:
    """セットアソシアティブ LRU のミス数 (gem5 と同じ索引: (addr >> 6) mod セット数)"""
    sets = [[] for _ in range(n_sets)]  # 先頭が最新
    misses = 0
    for a in addrs:
        line = a // LINE_BYTES
        s = sets[line % n_sets]
        if line in s:
            s.remove(line)
        else:
            misses += 1
            if len(s) >= ways:
                s.pop()
        s.insert(0, line)
    return misses


@unittest.skipUnless(GEM5.exists(), f"gem5 binary not found: {GEM5}")
class Gem5PlayerTest(unittest.TestCase):
    def run_gem5(self, accesses=None, records=None, extra=()):
        """トレースを書いて gem5 を走らせ、(終了コード, 出力, 統計) を返す"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        trace = os.path.join(tmp.name, "trace.bin")
        write_trace(trace, records if records is not None else build_records(accesses))
        outdir = os.path.join(tmp.name, "m5out")
        cmd = [str(GEM5), "-d", outdir, str(RUN_PY), "--trace", trace, *extra]
        proc = subprocess.run(cmd, cwd=GEM5_ROOT, capture_output=True, text=True, timeout=300)
        stats_path = Path(outdir) / "stats.txt"
        stats = parse_stats(stats_path) if stats_path.exists() else {}
        return proc.returncode, proc.stdout + proc.stderr, stats

    def assert_ok(self, code, log):
        self.assertEqual(code, 0, log)
        self.assertIn("trace finished", log)

    def test_counts_requests_and_slots(self):
        acc = [
            (0, IME, 0x0), (0, IME, 0x40), (0, FME, 0x80),
            (1, IME, 0x0), (1, FME, 0x40),
            (2, IME, 0x1000),
        ]
        code, log, st = self.run_gem5(acc)
        self.assert_ok(code, log)
        self.assertEqual(st["system.player.requests::IME"], 4)
        self.assertEqual(st["system.player.requests::FME"], 2)
        self.assertEqual(st["system.player.slots"], 3)
        self.assertEqual(st["system.player.slotCycles::samples"], 3)
        self.assertEqual(st["system.refcache.demandAccesses::total"], 6)

    def test_reuse_across_slots_hits(self):
        # 同じスロット内では ステージ間で同じラインを読まない (MSHR での合流を避ける)。
        # 容量に余裕があるので、ミス = 初回参照、ヒット = 2 回目以降
        lines = [i * LINE_BYTES for i in range(20)]
        acc = []
        for slot in range(5):
            acc += [(slot, IME, a) for a in lines[:10]]
            acc += [(slot, FME, a) for a in lines[10:]]
        code, log, st = self.run_gem5(acc, extra=["--cache-size", SMALL_CACHE[0], "--assoc", str(SMALL_CACHE[1])])
        self.assert_ok(code, log)
        self.assertEqual(st["system.refcache.demandMisses::total"], 20)
        self.assertEqual(st["system.refcache.demandHits::total"], len(acc) - 20)

    def test_lru_matches_reference_model(self):
        # 1 セットに 12 ウェイより多いラインを入れて、追い出しの順番まで確かめる。
        # 未完了数を 1 にして、トレースの順番どおりにキャッシュへ届くようにする
        ring = [i * SAME_SET_STRIDE for i in range(14)]  # 全部セット 0
        pattern = ring[:12] * 2 + ring + ring[:6] + ring[::-1]
        acc = [(i, IME, a) for i, a in enumerate(pattern)]
        expected = reference_lru_misses(pattern, N_SETS, SMALL_CACHE[1])
        code, log, st = self.run_gem5(
            acc,
            extra=["--cache-size", SMALL_CACHE[0], "--assoc", str(SMALL_CACHE[1]), "--max-outstanding", "1", "1"],
        )
        self.assert_ok(code, log)
        self.assertGreater(st["system.refcache.replacements"], 0)
        self.assertEqual(st["system.refcache.demandMisses::total"], expected)
        self.assertEqual(st["system.refcache.demandHits::total"], len(pattern) - expected)

    def test_same_dram_row_gives_row_hits(self):
        # RoRaBaCoCh では 先頭 8KB (128 ライン) が同じ ランク/バンク/行。逐次に読めば 2 本目から行ヒット
        acc = [(0, IME, i * LINE_BYTES) for i in range(16)]
        code, log, st = self.run_gem5(acc, extra=["--max-outstanding", "1", "1"])
        self.assert_ok(code, log)
        self.assertEqual(st["system.mem_ctrl.dram.readBursts"], 16)
        self.assertEqual(st["system.mem_ctrl.dram.readRowHits"], 15)

    def test_unsorted_trace_is_rejected(self):
        recs = [TraceRecord(1, IME, 0x0, NO_NEXT, 0, 0), TraceRecord(0, IME, 0x40, NO_NEXT, 0, 1)]
        code, log, _ = self.run_gem5(records=recs)
        self.assertNotEqual(code, 0)
        self.assertIn("not sorted by slot", log)

    def test_unknown_stage_is_rejected(self):
        code, log, _ = self.run_gem5(records=[TraceRecord(0, 0, 0x0, NO_NEXT, 0, 0)])
        self.assertNotEqual(code, 0)
        self.assertIn("unknown stage", log)

    def test_non_power_of_two_sets_is_rejected(self):
        # 3MiB / 64B / 8-way = 6144 セット (2 のべき乗でない)
        code, log, _ = self.run_gem5([(0, IME, 0x0)], extra=["--cache-size", "3MiB", "--assoc", "8"])
        self.assertNotEqual(code, 0)
        self.assertIn("power of two", log)


if __name__ == "__main__":
    unittest.main()
