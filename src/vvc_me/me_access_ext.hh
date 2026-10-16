#ifndef __VVC_ME_ME_ACCESS_EXT_HH__
#define __VVC_ME_ME_ACCESS_EXT_HH__

#include <cstdint>
#include <limits>
#include <memory>

#include "base/extensible.hh"
#include "mem/request.hh"

/*
    読み込むトレースのフォーマット
*/
struct TraceFormat {
    uint32_t slot; // パイプライン内での時間スロット
    uint8_t stage; // 発行されるステージ(IME or FME)
    uint64_t addr; // 64B 境界のバイトアドレス
    uint64_t opt_next_acc; // OPTが用いる次のアクセス番号
    uint32_t mv_pred_next_slot; // 動きベクトルから予測した次の使用スロット
    uint32_t seq; // トレース内での通し番号
};

/** opt_next_acc の「次のアクセスなし」 */
constexpr uint64_t ME_NO_NEXT = std::numeric_limits<uint64_t>::max();
/** mv_pred_next_slot の「予測なし」 */
constexpr uint32_t ME_NO_PRED = std::numeric_limits<uint32_t>::max();

namespace gem5
{

/**
 * 要求 (Request) に付ける拡張。トレースの 1 レコードをそのまま持たせ、
 * キャッシュの置換ポリシ (reset / touch で受け取るパケット) から読めるようにする。
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

} // namespace gem5

#endif // __VVC_ME_ME_ACCESS_EXT_HH__
