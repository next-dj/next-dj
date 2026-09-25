"""Signals the seo area emits, sent by `SitemapItemsRegistry` on every registration."""

from django.dispatch import Signal


sitemap_items_registered: Signal = Signal(use_caching=True)
