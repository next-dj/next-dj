"""The startup wiring of the dependency resolver, applied before any page loads."""

from next.manifest import Manifest

from . import checks, signals
from .resolver import apply_resolver_setting, reapply_resolver_setting


manifest = Manifest(
    "deps",
    settings_reloaded=(reapply_resolver_setting,),
    startup=(apply_resolver_setting,),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
