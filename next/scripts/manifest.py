"""The startup wiring of third-party scripts, their receivers and checks."""

from next.manifest import Manifest

from . import checks, signals
from .ports import PageScriptsImpl


manifest = Manifest(
    "scripts", providers=(PageScriptsImpl(),), checks=checks, signals=signals
)


__all__ = ["manifest"]
