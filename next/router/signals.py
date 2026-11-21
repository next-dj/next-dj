"""Django signals of client router navigations, shed prefetches and cache clears."""

from django.dispatch import Signal


navigation_rendered: Signal = Signal(use_caching=True)
"""Sent once per navigation or prefetch the page view answered, with its cut."""

prefetch_shed: Signal = Signal(use_caching=True)
"""Sent once per prefetch refused with 429 while the pool queue lags."""

router_cleared: Signal = Signal(use_caching=True)
"""Sent once per request whose response tells the client router to clear its caches."""


__all__ = ["navigation_rendered", "prefetch_shed", "router_cleared"]
