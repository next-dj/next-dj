"""The startup wiring of the development server, its checks and signals."""

from next.manifest import Manifest

from . import checks, signals


manifest = Manifest("server", checks=checks, signals=signals)


__all__ = ["manifest"]
