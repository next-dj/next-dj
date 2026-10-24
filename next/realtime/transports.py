"""Resolve the realtime transports available to the current Django deployment."""

from __future__ import annotations

import functools
import importlib.util
from collections.abc import Mapping

from django.apps import apps

from next.conf import next_framework_settings


@functools.cache
def active_transports() -> tuple[str, ...]:
    """Return enabled transports in preference order, always retaining SSE fallback.

    The answer is cached because `find_spec` scans `sys.path` on every call.
    """
    realtime = getattr(next_framework_settings, "REALTIME", {})
    selected = realtime.get("TRANSPORTS") if isinstance(realtime, Mapping) else None
    channels_ready = importlib.util.find_spec(
        "channels"
    ) is not None and apps.is_installed("channels")
    if selected is None:
        return ("ws", "sse") if channels_ready else ("sse",)
    requested = tuple(item for item in selected if item in {"ws", "sse"})
    if "ws" in requested and channels_ready:
        return ("ws", "sse")
    return ("sse",)


def forget_transports(**kwargs: object) -> None:
    """Drop the cached transports when settings or installed apps change."""
    active_transports.cache_clear()


__all__ = ["active_transports", "forget_transports"]
