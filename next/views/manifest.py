"""The startup wiring of the page view, the factory the file router mounts."""

from next.manifest import Bind, Manifest
from next.ports import page_views_slot

from .pages import page_view


manifest = Manifest("views", ports=(Bind(page_views_slot, page_view),))


__all__ = ["manifest"]
