"""Django signals emitted by the seo subsystem."""

from django.dispatch import Signal


sitemap_items_registered: Signal = Signal(use_caching=True)
sitemap_backend_loaded: Signal = Signal(use_caching=True)


__all__ = ["sitemap_backend_loaded", "sitemap_items_registered"]
