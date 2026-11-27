"""The startup wiring of the async runtime, its checks alone."""

from next.manifest import Manifest

from . import checks


manifest = Manifest("bridge", checks=checks)


__all__ = ["manifest"]
