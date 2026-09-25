"""Signals the pages area emits while loading, registering, and rendering pages."""

from django.dispatch import Signal


template_loaded: Signal = Signal(use_caching=True)
context_registered: Signal = Signal(use_caching=True)
metadata_registered: Signal = Signal(use_caching=True)
page_rendered: Signal = Signal(use_caching=True)
