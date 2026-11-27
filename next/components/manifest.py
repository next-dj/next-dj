"""The startup wiring of the component backends and their render pipeline."""

from next.manifest import Manifest

from . import checks, signals
from .manager import forget_render_pipeline


manifest = Manifest(
    "components",
    setting_changed=(forget_render_pipeline,),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
