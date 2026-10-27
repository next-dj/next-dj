"""Resolve the realtime transports available to the current Django deployment."""

import functools
import logging
import threading

from next.conf.scopes import scope_value
from next.static.runtime import forget_realtime_offer


logger = logging.getLogger(__name__)

_websocket_mounted = threading.Event()
_unmounted_warned = threading.Event()
_warning_lock = threading.Lock()


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


def warn_unmounted_websocket() -> None:
    """Log once per process that `ws` is offered while no WebSocket app is mounted.

    Only an explicit `TRANSPORTS` gets here, so every page tries a socket bound to fail.
    """
    if _websocket_mounted.is_set():
        return
    with _warning_lock:
        if _unmounted_warned.is_set():
            return
        _unmounted_warned.set()
    logger.warning(
        "REALTIME['TRANSPORTS'] lists 'ws' but this process mounts no WebSocket, "
        "so every page tries a socket that fails before it falls back to SSE. "
        "Serve next.realtime.application() or drop 'ws' from TRANSPORTS."
    )


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
    "warn_unmounted_websocket",
    "withdraw_websocket",
]
