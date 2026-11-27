"""The startup wiring of consent."""

from next.manifest import Manifest

from . import checks, signals


manifest = Manifest("consent", checks=checks, signals=signals)


__all__ = ["manifest"]
