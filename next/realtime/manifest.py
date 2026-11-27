"""The startup wiring of realtime streams, their receivers and checks."""

from next.manifest import Manifest

from . import checks, signals
from .offer import RealtimeScripts


manifest = Manifest(
    "realtime", providers=(RealtimeScripts(),), checks=checks, signals=signals
)


__all__ = ["manifest"]
