"""The startup wiring of the partial protocol, its layer pass and participant."""

from next.manifest import Bind, Manifest
from next.ports import layer_loading_slot

from . import checks, signals
from .layers import stash_layers
from .lifecycle import PartialParticipant
from .release import ReleaseScripts


manifest = Manifest(
    "partial",
    ports=(Bind(layer_loading_slot, stash_layers),),
    providers=(ReleaseScripts(),),
    participants=(PartialParticipant(),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
