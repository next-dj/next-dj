"""The startup wiring of page and component access."""

from next.manifest import Manifest

from . import checks


manifest = Manifest("permissions", checks=checks)


__all__ = ["manifest"]
