"""Resolve the realtime transports available to the current Django deployment."""

import functools
import threading

from next.conf.scopes import scope_value
from next.static.runtime import forget_realtime_offer


_websocket_mounted = threading.Event()


@functools.cache
def active_transports() -> tuple[str, ...]:
    """Return enabled transports in preference order, always retaining SSE fallback.

    Without an explicit `TRANSPORTS` list, `ws` follows a WebSocket app mounted here.
    """
    selected = scope_value("REALTIME", "TRANSPORTS")
    if selected is None:
        return ("ws", "sse") if _websocket_mounted.is_set() else ("sse",)
    if isinstance(selected, list | tuple) and "ws" in selected:
        return ("ws", "sse")
    return ("sse",)


def announce_websocket() -> None:
    """Record that this process serves the realtime WebSocket endpoint."""
    _websocket_mounted.set()
    forget_transports()


def withdraw_websocket() -> bool:
    """Forget an announced WebSocket endpoint and report whether one was announced.

    Tests that start an ASGI server in process use it to hand the flag back.
    """
    announced = _websocket_mounted.is_set()
    _websocket_mounted.clear()
    forget_transports()
    return announced


def forget_transports(**kwargs: object) -> None:
    """Drop the cached transports when the settings or the WebSocket mount change."""
    active_transports.cache_clear()
    forget_realtime_offer()


__all__ = [
    "active_transports",
    "announce_websocket",
    "forget_transports",
    "withdraw_websocket",
]
