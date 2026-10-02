"""Django signals emitted by the seo subsystem."""

from django.dispatch import Signal


sitemap_backend_loaded: Signal = Signal(use_caching=True)


__all__ = ["sitemap_backend_loaded"]
