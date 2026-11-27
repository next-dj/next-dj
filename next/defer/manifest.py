"""The startup wiring of deferred boundaries, their ports and participant."""

from next.manifest import Bind, Manifest
from next.ports import deferrals_slot, server_load_slot

from . import checks, signals
from .assembly import inline_sink
from .lifecycle import DeferParticipant
from .manager import defer_manager
from .payload import StreamScripts


manifest = Manifest(
    "defer",
    ports=(Bind(deferrals_slot, inline_sink), Bind(server_load_slot, defer_manager)),
    providers=(StreamScripts(),),
    participants=(DeferParticipant(),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
