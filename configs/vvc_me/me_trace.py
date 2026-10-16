"""
me_trace.py
PipelinedTracePlayer が読むトレース (src/vvc_me/pipelined_trace_player.hh の TraceFormat) の読み書き。

ファイル形式:
  * 1 レコード = 29 バイト、詰め物なし、リトルエンディアン。フィールドの順番は TraceFormat と同じ
      slot              uint32  パイプラインのスロット (ファイルはこの昇順に並べる)
      stage             uint8   IME = 1, FME = 2 (VTM のログと同じ番号)
      addr              uint64  64B 境界のバイトアドレス
      opt_next_acc      uint64  OPT 用: 同じラインの次のアクセスの seq (なければ NO_NEXT)
      mv_pred_next_slot uint32  提案手法用: 予測した次の使用スロット (なければ NO_PRED)
      seq               uint32  トレース内の通し番号
  * gem5 の外 (普通の Python) でも gem5 の中でも使えるよう、標準ライブラリだけで書いている
"""

import struct
from dataclasses import dataclass
from typing import Iterable, Iterator, List

RECORD = struct.Struct("<IBQQII")
assert RECORD.size == 29

IME = 1
FME = 2
LINE_BYTES = 64
NO_NEXT = (1 << 64) - 1
NO_PRED = (1 << 32) - 1


@dataclass(frozen=True)
class TraceRecord:
    slot: int
    stage: int
    addr: int
    opt_next_acc: int = NO_NEXT
    mv_pred_next_slot: int = NO_PRED
    seq: int = 0


def write_trace(path: str, records: Iterable[TraceRecord]) -> int:
    """レコードをファイルに書き出し、書いた数を返す"""
    n = 0
    with open(path, "wb") as f:
        for r in records:
            f.write(RECORD.pack(r.slot, r.stage, r.addr, r.opt_next_acc, r.mv_pred_next_slot, r.seq))
            n += 1
    return n


def read_trace(path: str) -> Iterator[TraceRecord]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) % RECORD.size:
        raise ValueError(f"{path}: size {len(data)} is not a multiple of {RECORD.size}")
    for fields in RECORD.iter_unpack(data):
        yield TraceRecord(*fields)


def build_records(accesses: Iterable[tuple]) -> List[TraceRecord]:
    """
    (slot, stage, addr) の列から、seq と OPT 用の次のアクセス (opt_next_acc) を付けたレコードを作る。
    並びは入力の順 (= OPT の基準の順番)。mv_pred_next_slot は NO_PRED のまま。
    """
    acc = list(accesses)
    for slot, stage, addr in acc:
        if addr % LINE_BYTES:
            raise ValueError(f"addr {addr:#x} is not {LINE_BYTES}B aligned")
        if stage not in (IME, FME):
            raise ValueError(f"unknown stage {stage}")
    if any(a[0] > b[0] for a, b in zip(acc, acc[1:])):
        raise ValueError("accesses must be sorted by slot")
    nxt = [NO_NEXT] * len(acc)
    last = {}
    for i in range(len(acc) - 1, -1, -1):
        line = acc[i][2] // LINE_BYTES
        nxt[i] = last.get(line, NO_NEXT)
        last[line] = i
    return [TraceRecord(s, st, a, nxt[i], NO_PRED, i) for i, (s, st, a) in enumerate(acc)]
