from m5.objects.ReplacementPolicies import BaseReplacementPolicy
from m5.params import *
from m5.proxy import *


class MeOptRP(BaseReplacementPolicy):
    """OPT: トレースの opt_next_acc (同じラインの次のアクセスの seq) が最も遠いラインを追い出す"""

    type = "MeOptRP"
    cxx_class = "gem5::replacement_policy::MeOpt"
    cxx_header = "vvc_me/me_replacement_policies.hh"


class MeMotionRP(BaseReplacementPolicy):
    """提案手法: トレースの mv_pred_next_slot (動きから予測した次の使用スロット) が最も遠いラインを追い出す"""

    type = "MeMotionRP"
    cxx_class = "gem5::replacement_policy::MeMotion"
    cxx_header = "vvc_me/me_replacement_policies.hh"

    player = Param.PipelinedTracePlayer("Trace player that provides the current pipeline slot")
    protect_recent = Param.Bool(True, "Treat lines touched in the previous slot or later as the nearest")
    quant_bits = Param.Unsigned(0, "Bits of the quantized distance tag (0 = no quantization)")
    mantissa_bits = Param.Unsigned(2, "Mantissa bits of the float-like quantization")
