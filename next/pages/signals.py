"""Signals emitted during template loading, context collection, and rendering.

`Page` sends `template_loaded` and `page_rendered`, `PageContextRegistry` sends
`context_registered`, `PageMetadataRegistry` sends `metadata_registered`, and the
signals reference lists the keyword arguments.
"""

from django.dispatch import Signal


template_loaded: Signal = Signal(use_caching=True)
context_registered: Signal = Signal(use_caching=True)
metadata_registered: Signal = Signal(use_caching=True)
page_rendered: Signal = Signal(use_caching=True)
