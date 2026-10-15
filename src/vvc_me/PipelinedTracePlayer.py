from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *

class PipelinedTracePlayer(ClockedObject):
    type = "PipelinedTracePlayer"
    cxx_header = "vvc_me/pipelined_trace_player.hh"
    cxx_class = "gem5::PipelinedTracePlayer"

    port = RequestPort("Port to the reference frame cache")
    system = Param.System(Parent.any, "System this player belongs to")
    trace_file = Param.String("Binary trace file")
    max_outstanding = VectorParam.Unsigned([16, 16], "Max outstanding requests per stage (IME, FME)")
    issue_width = VectorParam.Unsigned([1, 1], "Requests issued per cycle per state (IME, FME)")
