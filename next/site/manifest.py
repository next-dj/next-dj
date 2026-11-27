"""The startup wiring of the site configuration."""

from next.manifest import Manifest

from . import checks


manifest = Manifest("site", checks=checks)


__all__ = ["manifest"]
