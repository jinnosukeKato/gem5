#ifndef __VVC_ME_PIPELINED_TRACE_PLAYER_HH__
#define __VVC_ME_PIPELINED_TRACE_PLAYER_HH__

#include <cstdint>
#include <deque>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "base/extensible.hh"
#include "base/statistics.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "mem/request.hh"
#include "params/PipelinedTracePlayer.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

/*
    読み込むトレースのフォーマット
*/
struct TraceFormat {
    uint32_t slot; // パイプライン内での時間スロット
    uint8_t stage; // 発行されるステージ(IME or FME)
    uint64_t addr; // 64Byte単位のアドレス
    uint64_t opt_next_acc; // OPTが用いる次のアクセス番号
    uint32_t mv_pred_next_slot; // 動きベクトルから予測した次の使用スロット
    uint32_t seq; // トレース内での通し番号
};

namespace gem5
{

/**
 * 要求 (Request) に付ける拡張。トレースの 1 レコードをそのまま持たせ、
 * キャッシュの置換ポリシ (reset / touch で受け取るパケット) から読めるようにする。
 * 置換ポリシからも使うので、いずれ別ヘッダ (me_access_ext.hh など) に分けるとよい。
 */
class MeAccessExtension : public Extension<Request, MeAccessExtension>
{
  public:
    explicit MeAccessExtension(const TraceFormat &rec) : record(rec) {}

    std::unique_ptr<ExtensionBase>
    clone() const override
    {
        return std::make_unique<MeAccessExtension>(*this);
    }

    const TraceFormat record;
};

/**
 * パイプライン化された動き探索 (IME / FME) の参照読み出しを再生する。
 *
 * - トレースを スロット単位で読み込み、ステージごとのキューに積む
 * - 各サイクル、ステージごとに 発行数 (issue_width) と 未完了数 (max_outstanding) の
 *   上限の範囲で 64B の読み出し要求を出す
 * - そのスロットの要求が全部返ってきたら次のスロットへ進む (バリア)
 */
class PipelinedTracePlayer : public ClockedObject
{
  public:
    /** トレースの stage の値 */
    enum Stage : uint8_t { IME = 1, FME = 2, NUM_STAGES };

  private:
    /** キャッシュ側へつながるポート (要求を出す側) */
    class PlayerPort : public RequestPort
    {
      public:
        PlayerPort(const std::string &name, PipelinedTracePlayer &owner)
            : RequestPort(name), owner(owner)
        {}

      protected:
        bool recvTimingResp(PacketPtr pkt) override;
        void recvReqRetry() override;

      private:
        PipelinedTracePlayer &owner;
    };

    /**
     * パケットに積んで往復させる送り手側の情報。
     * レスポンスが返ってきたときに、どのステージの要求か・いつ出したかを知るため。
     */
    struct PlayerSenderState : public Packet::SenderState
    {
        PlayerSenderState(uint8_t stage, Tick issued)
            : stage(stage), issued(issued)
        {}
        uint8_t stage;
        Tick issued;
    };

    // --- 処理 ---
    void tick();
    bool readRecord(TraceFormat &rec);
    bool loadNextSlot();
    PacketPtr makePacket(const TraceFormat &rec);
    void handleResponse(PacketPtr pkt);
    void handleRetry();
    bool hasPendingWork() const;
    bool slotFinished() const;
    void scheduleTick();

    // --- 構成 ---
    PlayerPort port;
    const RequestorID requestorId;
    const std::string traceFile;
    const std::vector<unsigned> maxOutstanding; // ステージごと
    const std::vector<unsigned> issueWidth;     // ステージごと (1 サイクルあたり)

    // --- 状態 ---
    std::ifstream trace;
    std::vector<std::deque<TraceFormat>> queues; // 現在のスロットの未発行の要求
    std::vector<unsigned> outstanding;           // ステージごとの未完了数
    TraceFormat lookahead{};  // 読み込んだ次のスロットの先頭レコード
    bool haveLookahead = false;
    uint32_t curSlot = 0;
    Tick slotStart = 0;
    PacketPtr retryPkt = nullptr; // 送信を断られて再送待ちのパケット

    EventFunctionWrapper tickEvent;

    // --- 統計 ---
    struct PlayerStats : public statistics::Group
    {
        PlayerStats(statistics::Group *parent);
        statistics::Vector requests;       // ステージごとの要求数
        statistics::Scalar slots;          // 処理したスロット数
        statistics::Histogram slotCycles;  // 1 スロットにかかったサイクル数
        statistics::Histogram readLatency; // 要求を出してから返るまで [tick]
    } stats;

  public:
    PARAMS(PipelinedTracePlayer);
    PipelinedTracePlayer(const Params &p);

    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;
    void startup() override;
};

} // namespace gem5

#endif // __VVC_ME_PIPELINED_TRACE_PLAYER_HH__
