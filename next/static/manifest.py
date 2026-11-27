"""The startup wiring of the static pipeline and the assets port."""

from next.manifest import Bind, Manifest
from next.ports import static_assets_slot

from . import checks, signals
from .ports import StaticAssetsImpl


manifest = Manifest(
    "static",
    ports=(Bind(static_assets_slot, StaticAssetsImpl()),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
