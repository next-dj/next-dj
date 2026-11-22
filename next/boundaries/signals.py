"""Django signals emitted by the error boundaries."""

from django.dispatch import Signal


boundary_failed: Signal = Signal(use_caching=True)
"""Sent once per failure a boundary contained, with the `contain` function as sender."""


__all__ = ["boundary_failed"]
