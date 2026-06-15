"""Django signals emitted by the partial-rendering subsystem.

Every signal name is past tense and the sender is the owning class. The
hot-path idiom is to send only when the signal has receivers, so a
quiet deployment pays nothing.

The `zone_registered` signal fires once per compiled composed template
when its named zones are first read. The sender is the compiled
template class. The keyword arguments are `template`, `zone_name`, and
`lazy`.

The `zone_rendered` signal fires after a zone body renders for a
partial request. The sender is the zone manager class. The keyword
arguments are `zone_name`, `page_path`, `request`, and `duration_ms`.

The `patches_shaped` signal fires after the form shaping layer turns an
action outcome into a patch envelope. The sender is the backend class.
The keyword arguments are `request`, `uid`, `action_name`,
`outcome_kind`, and `ops`.

The `patch_op_registered` signal fires after a custom patch verb is
registered through `register_patch_op`. The sender is
`PatchOpRegistry`. The keyword argument is `name`.

The `field_validated` signal fires after a validate-only pass runs,
always behind the action guard so unauthenticated validate traffic
never reaches telemetry. The sender is the backend class. The keyword
arguments are `action_name`, `uid`, `request`, `field_names`, and
`error_count`.

The `sse_stream_opened` signal fires when a patch event stream starts.
The sender is `PatchEventStream`. The keyword argument is `request`.

The `sse_stream_closed` signal fires when a patch event stream ends.
The sender is `PatchEventStream`. The keyword arguments are `request`,
`duration_ms`, and `envelopes_sent`.
"""

from django.dispatch import Signal


zone_registered: Signal = Signal()
zone_rendered: Signal = Signal()
patches_shaped: Signal = Signal()
patch_op_registered: Signal = Signal()
field_validated: Signal = Signal()
sse_stream_opened: Signal = Signal()
sse_stream_closed: Signal = Signal()


__all__ = [
    "field_validated",
    "patch_op_registered",
    "patches_shaped",
    "sse_stream_closed",
    "sse_stream_opened",
    "zone_registered",
    "zone_rendered",
]
