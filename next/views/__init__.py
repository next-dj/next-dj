"""Composed views the URL router mounts, above the areas they render through."""

from .pages import page_view
from .routes import Route


__all__ = ["Route", "page_view"]
