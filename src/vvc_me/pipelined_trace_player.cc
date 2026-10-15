#include "vvc_me/pipelined_trace_player.hh"

#include "base/cast.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/PipelinedTracePlayer.hh"
#include "sim/sim_exit.hh"
#include "sim/system.hh"

namespace gem5
{

namespace
{
/** 1 要求の大きさ = キャッシュライン = DDR4 の 1 バースト */
constexpr unsigned BLOCK_SIZE = 64;
} // anonymous namespace

// ---------------------------------------------------------------------------
// 構築・接続
// ---------------------------------------------------------------------------
PipelinedTracePlayer::PipelinedTracePlayer(const Params &p)
    : ClockedObject(p),
      port(name() + ".port", *this),
      // この SimObject が出す要求の送り手 ID (統計やキャッシュの区別に使われる)
      requestorId(p.system->getRequestorId(this)),
      traceFile(p.trace_file),
      maxOutstanding(p.max_outstanding),
      issueWidth(p.issue_width),
      queues(NUM_STAGES),
      outstanding(NUM_STAGES, 0),
      // イベントで呼ぶ関数をラムダで包む。name は統計やデバッグ表示用
      tickEvent([this] { tick(); }, name() + ".tickEvent"),
      stats(this)
{
    fatal_if(maxOutstanding.size() != NUM_STAGES,
             "max_outstanding needs %d entries (one per stage)", NUM_STAGES);
    fatal_if(issueWidth.size() != NUM_STAGES,
             "issue_width needs %d entries (one per stage)", NUM_STAGES);
}

Port &
PipelinedTracePlayer::getPort(const std::string &if_name, PortID idx)
{
    // Python 側の "port" という名前のポートを、C++ のポートに結びつける
    if (if_name == "port") {
        return port;
    }
    return ClockedObject::getPort(if_name, idx);
}

void
PipelinedTracePlayer::startup()
{
    // startup() はシミュレーション開始直前に 1 回呼ばれる (ポートの接続は済んでいる)
    trace.open(traceFile, std::ios::binary);
    fatal_if(!trace, "cannot open trace file %s", traceFile);

    haveLookahead = readRecord(lookahead);
    fatal_if(!haveLookahead, "trace file %s is empty", traceFile);

    loadNextSlot();
    scheduleTick();
}

// ---------------------------------------------------------------------------
// トレースの読み込み
// ---------------------------------------------------------------------------
bool
PipelinedTracePlayer::readRecord(TraceFormat &rec)
{
    // 構造体は詰め物 (padding) が入るので、まとめて read せずフィールドごとに読む。
    // → ファイル上は 詰め物なしで フィールドをこの順に並べた形式になる
    //   (Python 側も同じ順・同じ型で書き出すこと)
    auto rd = [this](auto &field) {
        trace.read(reinterpret_cast<char *>(&field), sizeof(field));
    };
    rd(rec.slot);
    rd(rec.stage);
    rd(rec.addr);
    rd(rec.opt_next_acc);
    rd(rec.mv_pred_next_slot);
    rd(rec.seq);
    return static_cast<bool>(trace);
}

bool
PipelinedTracePlayer::loadNextSlot()
{
    if (!haveLookahead) {
        return false; // トレースの終わり
    }

    // 先読みしたレコードのスロットを、次に処理するスロットとする
    curSlot = lookahead.slot;
    slotStart = curTick();
    stats.slots++;

    // 同じスロットのレコードを全部キューに積む
    TraceFormat rec = lookahead;
    while (true) {
        fatal_if(rec.stage >= NUM_STAGES, "unknown stage %d (seq %d)",
                 rec.stage, rec.seq);
        queues[rec.stage].push_back(rec);

        if (!readRecord(rec)) {
            haveLookahead = false;
            break;
        }
        if (rec.slot != curSlot) {
            fatal_if(rec.slot < curSlot,
                     "trace is not sorted by slot (seq %d)", rec.seq);
            lookahead = rec; // 次のスロットの先頭として取っておく
            break;
        }
    }

    DPRINTF(PipelinedTracePlayer, "slot %d: IME %d reqs, FME %d reqs\n",
            curSlot, queues[IME].size(), queues[FME].size());
    return true;
}

// ---------------------------------------------------------------------------
// 要求の発行
// ---------------------------------------------------------------------------
PacketPtr
PipelinedTracePlayer::makePacket(const TraceFormat &rec)
{
    // TODO: トレースの addr が「バイトアドレス」か「64B 単位の番号」かに合わせる。
    //       ここではバイトアドレス (64B 境界) として扱っている
    const Addr addr = rec.addr;

    RequestPtr req = std::make_shared<Request>(addr, BLOCK_SIZE, 0,
                                               requestorId);
    // 置換ポリシが読めるように、トレースのレコードを拡張として付ける
    req->setExtension(std::make_shared<MeAccessExtension>(rec));

    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->allocate(); // 読み出したデータを受け取る領域

    // レスポンスでステージと発行時刻が分かるように、送り手の情報を積む
    pkt->pushSenderState(new PlayerSenderState(rec.stage, curTick()));
    return pkt;
}

void
PipelinedTracePlayer::tick()
{
    // 再送待ちの間は何も出さない (recvReqRetry で再開する)
    if (retryPkt) {
        return;
    }

    for (unsigned s = 0; s < NUM_STAGES; s++) {
        unsigned issued = 0;
        while (issued < issueWidth[s] &&
               outstanding[s] < maxOutstanding[s] &&
               !queues[s].empty()) {
            PacketPtr pkt = makePacket(queues[s].front());
            queues[s].pop_front();
            outstanding[s]++;
            issued++;
            stats.requests[s]++;

            if (!port.sendTimingReq(pkt)) {
                // 相手 (キャッシュ) が受け付けられない。再送の合図を待つ
                retryPkt = pkt;
                return;
            }
        }
    }

    // まだ出せる要求が残っていれば、次のサイクルにもう一度
    scheduleTick();
}

void
PipelinedTracePlayer::handleRetry()
{
    assert(retryPkt);
    PacketPtr pkt = retryPkt;
    retryPkt = nullptr;
    if (!port.sendTimingReq(pkt)) {
        retryPkt = pkt; // まだ受け付けられない
        return;
    }
    scheduleTick();
}

// ---------------------------------------------------------------------------
// レスポンスの受け取り
// ---------------------------------------------------------------------------
void
PipelinedTracePlayer::handleResponse(PacketPtr pkt)
{
    auto *state = safe_cast<PlayerSenderState *>(pkt->popSenderState());
    stats.readLatency.sample(curTick() - state->issued);
    assert(outstanding[state->stage] > 0);
    outstanding[state->stage]--;
    delete state;
    delete pkt; // Request は shared_ptr なので自動で解放される

    if (slotFinished()) {
        stats.slotCycles.sample(ticksToCycles(curTick() - slotStart));
        if (loadNextSlot()) {
            scheduleTick();
        } else {
            exitSimLoop("PipelinedTracePlayer: trace finished");
        }
        return;
    }

    // 未完了数に空きができたので、出せる要求があれば出す
    scheduleTick();
}

// ---------------------------------------------------------------------------
// 補助
// ---------------------------------------------------------------------------
bool
PipelinedTracePlayer::hasPendingWork() const
{
    for (const auto &q : queues) {
        if (!q.empty()) {
            return true;
        }
    }
    return false;
}

bool
PipelinedTracePlayer::slotFinished() const
{
    if (retryPkt || hasPendingWork()) {
        return false;
    }
    for (unsigned n : outstanding) {
        if (n > 0) {
            return false;
        }
    }
    return true;
}

void
PipelinedTracePlayer::scheduleTick()
{
    // 出せる要求があり、まだ予約していなければ、次のクロックの立ち上がりに予約
    if (hasPendingWork() && !retryPkt && !tickEvent.scheduled()) {
        schedule(tickEvent, clockEdge(Cycles(1)));
    }
}

// ---------------------------------------------------------------------------
// ポート
// ---------------------------------------------------------------------------
bool
PipelinedTracePlayer::PlayerPort::recvTimingResp(PacketPtr pkt)
{
    owner.handleResponse(pkt);
    return true; // レスポンスは常に受け取る
}

void
PipelinedTracePlayer::PlayerPort::recvReqRetry()
{
    owner.handleRetry();
}

// ---------------------------------------------------------------------------
// 統計
// ---------------------------------------------------------------------------
PipelinedTracePlayer::PlayerStats::PlayerStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(requests, statistics::units::Count::get(),
               "Number of read requests per stage"),
      ADD_STAT(slots, statistics::units::Count::get(),
               "Number of pipeline slots"),
      ADD_STAT(slotCycles, statistics::units::Cycle::get(),
               "Cycles spent per pipeline slot"),
      ADD_STAT(readLatency, statistics::units::Tick::get(),
               "Ticks from request to response")
{
    requests.init(NUM_STAGES);
    requests.subname(IME, "IME");
    requests.subname(FME, "FME");
    slotCycles.init(32); // バケット数
    readLatency.init(32);
}

} // namespace gem5
