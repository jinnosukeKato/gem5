#ifndef __VVC_ME_ME_REPLACEMENT_POLICIES_HH__
#define __VVC_ME_ME_REPLACEMENT_POLICIES_HH__

#include <cstdint>
#include <memory>

#include "base/types.hh"
#include "mem/cache/replacement_policies/base.hh"
#include "vvc_me/me_access_ext.hh"

namespace gem5
{

struct MeOptRPParams;
struct MeMotionRPParams;
class PipelinedTracePlayer;

namespace replacement_policy
{

/**
 * OPT (論理順を基準にしたセット内 Belady, バイパスなし)。
 * トレースの opt_next_acc (同じラインの次のアクセスの seq) が最も遠いラインを追い出す。
 * 次のアクセスがないライン同士は、最後に触ったのが古い方を追い出す。
 */
class MeOpt : public Base
{
  protected:
    struct MeOptReplData : ReplacementData
    {
        bool valid = false;
        uint64_t nextAcc = ME_NO_NEXT;
        Tick lastTouchTick = 0;
    };

  public:
    typedef MeOptRPParams Params;
    MeOpt(const Params &p);

    void invalidate(const std::shared_ptr<ReplacementData>& replacement_data)
        override;
    void touch(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) override;
    void touch(const std::shared_ptr<ReplacementData>& replacement_data) const
        override;
    void reset(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) override;
    void reset(const std::shared_ptr<ReplacementData>& replacement_data) const
        override;
    ReplaceableEntry* getVictim(const ReplacementCandidates& candidates) const
        override;
    std::shared_ptr<ReplacementData> instantiateEntry() override;

  private:
    void update(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) const;
};

/**
 * 提案手法: 動きから予測した次の使用スロット (トレースの mv_pred_next_slot) で置換する。
 *
 * 追い出すときのスコア (大きいほど先に追い出す):
 *   - 直前のスロットまでに触ったライン (protect_recent): 0 (最も近い扱い。同じ CTU の FME や
 *     隣の CTU の再利用は予測の対象外のため)
 *   - 予測なし / 予測したスロットを過ぎた: 最大
 *   - それ以外: d = 予測スロット - 現在のスロット。quant_bits > 0 なら d を
 *     浮動小数点風 (指数 + 仮数 mantissa_bits ビット) の quant_bits ビットの符号に量子化する
 * 同じスコアなら、最後に触ったのが古い方を追い出す。
 * 現在のスロットは player (PipelinedTracePlayer) から取る。
 */
class MeMotion : public Base
{
  protected:
    struct MeMotionReplData : ReplacementData
    {
        bool valid = false;
        uint32_t predSlot = ME_NO_PRED;
        uint32_t lastSlot = 0;
        Tick lastTouchTick = 0;
    };

  public:
    typedef MeMotionRPParams Params;
    MeMotion(const Params &p);

    void invalidate(const std::shared_ptr<ReplacementData>& replacement_data)
        override;
    void touch(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) override;
    void touch(const std::shared_ptr<ReplacementData>& replacement_data) const
        override;
    void reset(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) override;
    void reset(const std::shared_ptr<ReplacementData>& replacement_data) const
        override;
    ReplaceableEntry* getVictim(const ReplacementCandidates& candidates) const
        override;
    std::shared_ptr<ReplacementData> instantiateEntry() override;

    /** 距離 d [スロット] の量子化 (quant_bits ビットの符号。最大値は「予測なし / 過ぎた」用に空けておく) */
    static uint64_t quantize(uint64_t d, unsigned bits, unsigned mantissa);

  private:
    void update(const std::shared_ptr<ReplacementData>& replacement_data,
        const PacketPtr pkt) const;
    uint32_t now() const;
    uint64_t score(const MeMotionReplData &d, uint32_t now) const;

    PipelinedTracePlayer *player;
    const bool protectRecent;
    const unsigned quantBits;
    const unsigned mantissaBits;
};

} // namespace replacement_policy
} // namespace gem5

#endif // __VVC_ME_ME_REPLACEMENT_POLICIES_HH__
