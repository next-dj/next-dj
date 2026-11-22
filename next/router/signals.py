"""Django signals of client router navigations, shed prefetches and cache clears."""

from django.dispatch import Signal


navigation_rendered: Signal = Signal(use_caching=True)
prefetch_shed: Signal = Signal(use_caching=True)
router_cleared: Signal = Signal(use_caching=True)


__all__ = ["navigation_rendered", "prefetch_shed", "router_cleared"]
