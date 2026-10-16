#ifndef __VVC_ME_PIPELINED_TRACE_PLAYER_HH__
#define __VVC_ME_PIPELINED_TRACE_PLAYER_HH__

#include <cstdint>
#include <deque>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "mem/packet.hh"
#include "mem/port.hh"
#include "mem/request.hh"
#include "params/PipelinedTracePlayer.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"
#include "vvc_me/me_access_ext.hh"

namespace gem5
{

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
    /** トレースの stage の値 (VTM のログのステージ番号と同じ) */
    enum Stage : uint8_t { IME = 1, FME = 2 };
    /** ステージ数。キューやパラメータの配列は IME から順に並べる */
    static constexpr unsigned NUM_STAGES = FME - IME + 1;
    /** ステージ番号 → 配列の添字 (IME → 0, FME → 1) */
    static constexpr unsigned
    stageIndex(uint8_t stage)
    {
        return stage - IME;
    }

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

    /**
     * いま処理しているパイプラインのスロット。
     * 置換ポリシ (MeMotionRP) が「現在の時刻」として使う (getVictim には時刻が渡されないため)
     */
    uint32_t currentSlot() const { return curSlot; }
};

} // namespace gem5

#endif // __VVC_ME_PIPELINED_TRACE_PLAYER_HH__
