"""Django signals emitted by the seo subsystem."""

from django.dispatch import Signal


sitemap_items_registered: Signal = Signal(use_caching=True)
