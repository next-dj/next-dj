"""Signals emitted during template loading, context collection, and rendering.

These signals let external subscribers observe the page rendering
pipeline without subclassing internal collaborators.
"""

from __future__ import annotations

from django.dispatch import Signal


template_loaded: Signal = Signal()
context_registered: Signal = Signal()
page_rendered: Signal = Signal()
