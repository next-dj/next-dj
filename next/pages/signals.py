"""Signals emitted during template loading, context collection, and rendering.

These signals let external subscribers observe the page rendering
pipeline without subclassing internal collaborators.

The `template_loaded` signal fires after a template source is
registered on a page. The sender is `Page`. The keyword argument is
`file_path`.

The `context_registered` signal fires after a context callable is
attached to a page module. The sender is `PageContextRegistry`. The
keyword arguments are `file_path` and `key`.

The `page_rendered` signal fires after `Page.render` finishes
producing HTML and injecting static assets. The sender is `Page`. The
keyword arguments are `file_path`, `duration_ms`, `styles_count`,
`scripts_count`, and `context_keys`.
"""

from __future__ import annotations

from django.dispatch import Signal


template_loaded: Signal = Signal()
context_registered: Signal = Signal()
page_rendered: Signal = Signal()
