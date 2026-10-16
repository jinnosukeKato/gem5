#include "vvc_me/me_replacement_policies.hh"

#include <cassert>
#include <limits>

#include "base/logging.hh"
#include "mem/packet.hh"
#include "params/MeMotionRP.hh"
#include "params/MeOptRP.hh"
#include "sim/cur_tick.hh"
#include "vvc_me/pipelined_trace_player.hh"

namespace gem5
{

namespace replacement_policy
{

namespace
{
/** パケットに付いたトレースのレコード (なければ nullptr) */
const TraceFormat *
recordOf(const PacketPtr pkt)
{
    if (!pkt || !pkt->req) {
        return nullptr;
    }
    auto ext = pkt->req->getExtension<MeAccessExtension>();
    return ext ? &ext->record : nullptr;
}
} // anonymous namespace

// ---------------------------------------------------------------------------
// MeOpt
// ---------------------------------------------------------------------------
MeOpt::MeOpt(const Params &p) : Base(p) {}

void
MeOpt::invalidate(const std::shared_ptr<ReplacementData>& replacement_data)
{
    auto d = std::static_pointer_cast<MeOptReplData>(replacement_data);
    d->valid = false;
    d->nextAcc = ME_NO_NEXT;
    d->lastTouchTick = 0;
}

void
MeOpt::update(const std::shared_ptr<ReplacementData>& replacement_data,
              const PacketPtr pkt) const
{
    auto d = std::static_pointer_cast<MeOptReplData>(replacement_data);
    const TraceFormat *rec = recordOf(pkt);
    d->valid = true;
    d->nextAcc = rec ? rec->opt_next_acc : ME_NO_NEXT;
    d->lastTouchTick = curTick();
}

void
MeOpt::touch(const std::shared_ptr<ReplacementData>& replacement_data,
             const PacketPtr pkt)
{
    update(replacement_data, pkt);
}

void
MeOpt::touch(const std::shared_ptr<ReplacementData>& replacement_data) const
{
    update(replacement_data, nullptr);
}

void
MeOpt::reset(const std::shared_ptr<ReplacementData>& replacement_data,
             const PacketPtr pkt)
{
    update(replacement_data, pkt);
}

void
MeOpt::reset(const std::shared_ptr<ReplacementData>& replacement_data) const
{
    update(replacement_data, nullptr);
}

ReplaceableEntry*
MeOpt::getVictim(const ReplacementCandidates& candidates) const
{
    assert(candidates.size() > 0);
    ReplaceableEntry *victim = nullptr;
    const MeOptReplData *best = nullptr;
    for (const auto &c : candidates) {
        auto d = std::static_pointer_cast<MeOptReplData>(c->replacementData);
        if (!d->valid) {
            return c; // 空きのウェイを最優先
        }
        if (!best || d->nextAcc > best->nextAcc ||
            (d->nextAcc == best->nextAcc &&
             d->lastTouchTick < best->lastTouchTick)) {
            best = d.get();
            victim = c;
        }
    }
    return victim;
}

std::shared_ptr<ReplacementData>
MeOpt::instantiateEntry()
{
    return std::make_shared<MeOptReplData>();
}

// ---------------------------------------------------------------------------
// MeMotion
// ---------------------------------------------------------------------------
MeMotion::MeMotion(const Params &p)
    : Base(p),
      player(p.player),
      protectRecent(p.protect_recent),
      quantBits(p.quant_bits),
      mantissaBits(p.mantissa_bits)
{
    fatal_if(!player, "MeMotionRP needs player (PipelinedTracePlayer)");
    fatal_if(quantBits > 0 && (quantBits < 2 || quantBits > 32),
             "quant_bits must be 0 (no quantization) or 2..32");
    fatal_if(quantBits > 0 && mantissaBits >= quantBits,
             "mantissa_bits must be smaller than quant_bits");
}

uint64_t
MeMotion::quantize(uint64_t d, unsigned bits, unsigned mantissa)
{
    // replacement_sim.py の _class_of (mode 4) と同じ:
    //   v = d + 1 の指数 e と上位 mantissa ビットの仮数で符号化し、最大値 (top) は空けておく
    const uint64_t top = (uint64_t(1) << bits) - 1;
    const uint64_t v = d + 1;
    unsigned e = 0;
    while ((v >> (e + 1)) > 0) {
        e++;
    }
    uint64_t code;
    if (e < mantissa) {
        code = v - 1;
    } else {
        code = (uint64_t(e - mantissa + 1) << mantissa) |
               ((v >> (e - mantissa)) & ((uint64_t(1) << mantissa) - 1));
    }
    return code < top - 1 ? code : top - 1;
}

uint32_t
MeMotion::now() const
{
    return player->currentSlot();
}

uint64_t
MeMotion::score(const MeMotionReplData &d, uint32_t cur) const
{
    const uint64_t worst = quantBits ? (uint64_t(1) << quantBits) - 1
                                     : std::numeric_limits<uint64_t>::max();
    if (protectRecent && uint64_t(d.lastSlot) + 1 >= cur) {
        return 0; // 直前のスロットまでに触ったライン
    }
    if (d.predSlot == ME_NO_PRED || d.predSlot < cur) {
        return worst; // 予測なし / 予測したスロットを過ぎた
    }
    const uint64_t dist = d.predSlot - cur;
    return quantBits ? quantize(dist, quantBits, mantissaBits) : dist;
}

void
MeMotion::invalidate(const std::shared_ptr<ReplacementData>& replacement_data)
{
    auto d = std::static_pointer_cast<MeMotionReplData>(replacement_data);
    d->valid = false;
    d->predSlot = ME_NO_PRED;
    d->lastSlot = 0;
    d->lastTouchTick = 0;
}

void
MeMotion::update(const std::shared_ptr<ReplacementData>& replacement_data,
                 const PacketPtr pkt) const
{
    auto d = std::static_pointer_cast<MeMotionReplData>(replacement_data);
    const TraceFormat *rec = recordOf(pkt);
    d->valid = true;
    d->predSlot = rec ? rec->mv_pred_next_slot : ME_NO_PRED;
    d->lastSlot = now();
    d->lastTouchTick = curTick();
}

void
MeMotion::touch(const std::shared_ptr<ReplacementData>& replacement_data,
                const PacketPtr pkt)
{
    update(replacement_data, pkt);
}

void
MeMotion::touch(const std::shared_ptr<ReplacementData>& replacement_data) const
{
    update(replacement_data, nullptr);
}

void
MeMotion::reset(const std::shared_ptr<ReplacementData>& replacement_data,
                const PacketPtr pkt)
{
    update(replacement_data, pkt);
}

void
MeMotion::reset(const std::shared_ptr<ReplacementData>& replacement_data) const
{
    update(replacement_data, nullptr);
}

ReplaceableEntry*
MeMotion::getVictim(const ReplacementCandidates& candidates) const
{
    assert(candidates.size() > 0);
    const uint32_t cur = now();
    ReplaceableEntry *victim = nullptr;
    uint64_t bestScore = 0;
    Tick bestTick = 0;
    for (const auto &c : candidates) {
        auto d = std::static_pointer_cast<MeMotionReplData>(c->replacementData);
        if (!d->valid) {
            return c; // 空きのウェイを最優先
        }
        const uint64_t s = score(*d, cur);
        if (!victim || s > bestScore ||
            (s == bestScore && d->lastTouchTick < bestTick)) {
            victim = c;
            bestScore = s;
            bestTick = d->lastTouchTick;
        }
    }
    return victim;
}

std::shared_ptr<ReplacementData>
MeMotion::instantiateEntry()
{
    return std::make_shared<MeMotionReplData>();
}

} // namespace replacement_policy
} // namespace gem5
