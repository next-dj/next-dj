"""The startup wiring of error boundaries, their signals alone."""

from next.manifest import Manifest

from . import signals


manifest = Manifest("boundaries", signals=signals)


__all__ = ["manifest"]
