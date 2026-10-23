# vvc_me: 動き探索の参照キャッシュの gem5 評価

パイプライン化された VVC エンコーダの動き探索 (IME / FME) の参照読み出しを gem5 で再生し、
参照キャッシュの置換ポリシ (LRU / OPT / 提案手法) を DDR4 込みで比較するための一式。

```
VTM のログ ──(address_trace.py)──▶ <seq>_gem5.bin ──▶ PipelinedTracePlayer ─▶ Cache ─▶ SystemXBar ─▶ MemCtrl (DDR4_2400_16x4)
 <seq>.bin, <seq>_mv.bin                                  (src/vvc_me)        置換ポリシ: LRURP / MeOptRP / MeMotionRP
```

## ファイル

| 場所 | 内容 |
|---|---|
| `src/vvc_me/pipelined_trace_player.{hh,cc}` | トレースプレイヤー (SimObject `PipelinedTracePlayer`) |
| `src/vvc_me/me_replacement_policies.{hh,cc}` | 置換ポリシ `MeOptRP` (OPT) / `MeMotionRP` (提案手法) |
| `src/vvc_me/me_access_ext.hh` | トレースのレコード `TraceFormat` と、要求に付ける拡張 `MeAccessExtension` |
| `configs/vvc_me/run.py` | 組み立てスクリプト |
| `configs/vvc_me/me_trace.py` | トレースの読み書き (標準ライブラリだけ) |
| `configs/vvc_me/summarize.py` | 結果を置換ポリシごとの比較表にする |
| `configs/vvc_me/tests/test_vvc_me.py` | テスト |
| (親リポジトリ) `MemoryAccessHeatmapGenerator/address_trace.py` | VTM のログ → gem5 用トレース |
| (親リポジトリ) `MemoryAccessHeatmapGenerator/gem5_reference.py` | gem5 のミス数を Python で再現する参照実装 (照合用) |

## 1. ビルド

```bash
scons build/NULL/gem5.opt -j 8      # CPU のモデルは使わないので NULL で十分
```

キャッシュのセット数 (容量 / 64B / ウェイ数) は 2 のべき乗である必要がある (gem5 の制約)。
768KiB / 3MiB / 12MiB は **12 ウェイ** なら 1024 / 4096 / 16384 セットになる。

## 2. トレースを作る (親リポジトリで)

```bash
# 6 本まとめて (エンコード済みなら)
./scripts/zoo-script/run_pipeline.sh --skip-encode --skip-heatmap   # CAL なら scripts/cal-script/run_pipeline.sh -j 6
# 1 本だけ
uv run --project MemoryAccessHeatmapGenerator python MemoryAccessHeatmapGenerator/address_trace.py \
    build/s201.bin --mv-log build/s201_mv.bin -o build/s201_gem5.bin
```

| `address_trace.py` の引数 | 既定 | 意味 |
|---|---|---|
| `log_file` | (必須) | VTM のメモリアクセスログ (`<seq>.bin`) |
| `--mv-log` | なし | 動きベクトルログ (`<seq>_mv.bin`)。省略すると予測は幾何だけ |
| `-o` | (必須) | 出力 (`<seq>_gem5.bin`) |
| `--margin` | 32 | 予測の探索窓のマージン [画素] |
| `--motion-threshold` | 0 | これより小さい動きは補正しない [画素] |
| `--mv-agg` | mean | CTU ごとの動きの代表値 (`mean` / `median`) |
| `--order` | interleave | スロット内の並び (= OPT の論理順)。`interleave`: IME と FME を 1 件ずつ交互 / `stage`: IME を全部 → FME を全部 |

### トレースの形式 (29B / レコード、詰め物なし、リトルエンディアン)

| フィールド | 型 | 内容 |
|---|---|---|
| `slot` | uint32 | パイプラインのスロット。スロット k で IME(CTU k) と FME(CTU k−1)。ファイルはこの昇順 |
| `stage` | uint8 | 1 = IME, 2 = FME (VTM のログと同じ番号) |
| `addr` | uint64 | 64B 境界のバイトアドレス (RoRaBaCoCh、8×6 画素のタイル、128×48 画素で 1 行) |
| `opt_next_acc` | uint64 | OPT 用: 同じラインの次のアクセスの `seq` (なし = 2^64−1) |
| `mv_pred_next_slot` | uint32 | 提案手法用: 動きから予測した次の使用スロット (なし = 2^32−1) |
| `seq` | uint32 | 通し番号 (OPT の基準になる論理順。既定ではスロット内で IME と FME が交互) |

## 3. 走らせる

```bash
build/NULL/gem5.opt -d m5out/s201_3MiB_lru configs/vvc_me/run.py --trace ../build/s201_gem5.bin --cache-size 3MiB
build/NULL/gem5.opt -d m5out/s201_3MiB_opt configs/vvc_me/run.py --trace ../build/s201_gem5.bin --cache-size 3MiB --repl opt
build/NULL/gem5.opt -d m5out/s201_3MiB_mv  configs/vvc_me/run.py --trace ../build/s201_gem5.bin --cache-size 3MiB --repl mv
build/NULL/gem5.opt -d m5out/s201_3MiB_mv5 configs/vvc_me/run.py --trace ../build/s201_gem5.bin --cache-size 3MiB --repl mv --quant-bits 5

# グラフは親リポジトリの scripts/plot_gem5_summary.py で summary.csv から描く
#   uv run ../scripts/plot_gem5_summary.py m5out/summary.csv -o m5out/plots
```

`summarize.py` はディレクトリ名 `<seq>_<容量>_<ポリシ>` を前提に、同じ `<seq>_<容量>` の `lru` / `opt` と比べる。

### run.py の引数

| 引数 | 既定 | 意味 |
|---|---|---|
| `--trace` | (必須) | トレース |
| `--clock` | 600MHz | プレイヤーとキャッシュのクロック |
| `--cache-size` | 3MiB | 参照キャッシュの容量 |
| `--assoc` | 12 | ウェイ数 |
| `--tag-latency` / `--data-latency` / `--response-latency` | 2 / 2 / 2 | キャッシュのレイテンシ [サイクル] |
| `--mshrs` | 32 | キャッシュが同時に待てるミスの数 |
| `--tgts-per-mshr` | 8 | 1 つの MSHR に合流できる要求の数 |
| `--max-outstanding IME FME` | 16 16 | ステージごとの未完了の上限 (出したがまだ返っていない要求の数) |
| `--issue-width IME FME` | 1 1 | ステージごとの 1 サイクルの発行数 (1 件 = 64B) |
| `--repl` | lru | 置換ポリシ: `lru` / `opt` / `mv` |
| `--quant-bits` | 0 | mv: 距離のタグのビット数 (0 = 量子化なし) |
| `--mantissa-bits` | 2 | mv: 浮動小数点風の量子化の仮数のビット数 |
| `--no-protect` | (保護あり) | mv: 直前のスロットまでに触ったラインを保護しない |

DRAM は `DDR4_2400_16x4` (2 ランク、16 バンク = 4 バンクグループ、行 8KB)、`addr_mapping = RoRaBaCoCh`、1 チャネル、データは保持しない (`null`)。

## 4. モデル

### PipelinedTracePlayer
- スロット k のレコード (IME(CTU k) と FME(CTU k−1)) をステージごとのキューに積み、各サイクル、ステージごとに `issue_width` 件まで、未完了が `max_outstanding` 未満の範囲で 64B の読み出しを出す
- スロットの要求が全部返ったら次のスロットへ進む (バリア)。これは
  「IME(c) は左隣 IME(c−1) の完了を待つ」「IME と FME の間にバッファなし (FME が受け取れなければ IME がストール)」
  「演算時間なし」の条件のパイプラインと、タイミングが同じになる
- 演算時間はモデルに入れていない。実行時間 (`simSeconds`、スロットのサイクル数) は比でだけ見る

| パラメータ (`PipelinedTracePlayer`) | 意味 |
|---|---|
| `trace_file` | トレース |
| `max_outstanding` | ステージごとの未完了の上限 `[IME, FME]` |
| `issue_width` | ステージごとの 1 サイクルの発行数 `[IME, FME]` |

| 統計 (`system.player.`) | 意味 |
|---|---|
| `requests::IME` / `requests::FME` | 要求数 |
| `slots` | 処理したスロット数 |
| `slotCycles` | 1 スロットにかかったサイクル数 (分布) |
| `readLatency` | 要求から応答までの tick (分布) |

### 置換ポリシ

| ポリシ | 使う値 | 追い出すライン |
|---|---|---|
| `LRURP` | 最後に触った時刻 | 最も古い |
| `MeOptRP` (OPT) | `opt_next_acc` | 次のアクセスが最も遠い (同点は古い方)。**論理順を基準にしたセット内 Belady、バイパスなし** |
| `MeMotionRP` (提案手法) | `mv_pred_next_slot` と、触ったスロット | 次の表のスコアが最も大きい (同点は古い方) |

`MeMotionRP` のスコア (現在のスロットは `player.currentSlot()`):

| ラインの状態 | スコア |
|---|---|
| 直前のスロットまでに触った (`protect_recent`) | 0 (最も近い扱い) |
| 予測なし / 予測したスロットを過ぎた | 最大 |
| それ以外 | d = 予測スロット − 現在のスロット。`quant_bits > 0` なら d を浮動小数点風 (指数 + 仮数 `mantissa_bits` ビット) の `quant_bits` ビットに量子化 |

| パラメータ (`MeMotionRP`) | 既定 | 意味 |
|---|---|---|
| `player` | (必須) | 現在のスロットを返すプレイヤー |
| `protect_recent` | True | 直前のスロットまでに触ったラインを保護する |
| `quant_bits` | 0 | 距離のタグのビット数 (0 = 量子化なし、2〜32) |
| `mantissa_bits` | 2 | 仮数のビット数 (`quant_bits` より小さく) |

### 注意: OPT の基準の順番
- プレイヤーは IME と FME から 1 サイクルに 1 件ずつ出すので、キャッシュには **スロット内で IME と FME が交互に** 届く
- そのため `address_trace.py` は既定 (`--order interleave`) で、スロット内の IME と FME を 1 件ずつ交互に並べ、
  この並びを論理順として `seq` と `opt_next_acc` を計算する
- 「IME を全部 → FME を全部」の並び (`--order stage`) で作ったトレースで OPT を並列に走らせると、
  すぐ後に FME が読むラインを「遠い」と見なして追い出し、**LRU より悪くなる** (s210・768KiB で DRAM 読み出しが LRU の約 2 倍)
- 交互の並びでも、実際の到着順とは完全には一致しない (レイテンシで前後する) ので、OPT は「論理順を基準にしたセット内 Belady」と定義する
- `MeMotionRP` は要求の拡張 (`MeAccessExtension`) から予測を読む。拡張のない要求は「予測なし」として扱う

## 5. テストと照合

```bash
python3 -m unittest discover -s configs/vvc_me/tests -v
```

- トレースの読み書き、プレイヤーの要求数・スロット数・異常入力、DRAM の行ヒット
- LRU / OPT / 提案手法のミス数が、テスト内の参照モデルと一致すること (ランダムなトレース、逐次)

実データでの照合は、stage を全部 IME にしたトレースを逐次 (`--max-outstanding 1 1`) に流し、親リポジトリの参照実装と比べる:

```bash
uv run --project MemoryAccessHeatmapGenerator python MemoryAccessHeatmapGenerator/gem5_reference.py \
    <trace> --capacity 3MiB --assoc 12 [--policy mv --quant-bits 5]
```

(s201・3MiB で LRU / OPT / 提案手法 (量子化なし・5 ビット) のミス数が gem5 と完全に一致することを確認済み)
