"""Composed views the URL router mounts, above the areas they render through."""

from .pages import page_view
from .routes import RouteKind


__all__ = ["RouteKind", "page_view"]
