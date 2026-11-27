"""The startup wiring of response compression."""

from next.manifest import Manifest

from . import checks, signals


manifest = Manifest("compression", checks=checks, signals=signals)


__all__ = ["manifest"]
