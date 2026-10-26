"""Realtime negotiation bound into the `next.ports` slot at app startup."""

from typing import override

from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.ports import RealtimeNegotiation

from .transports import active_transports


class RealtimeNegotiationImpl(RealtimeNegotiation):
    """Binds the port to the transport resolution and the active partial backend."""

    @override
    def transports(self) -> tuple[str, ...]:
        """Return the enabled realtime transports in preference order."""
        return active_transports()

    @override
    def content_types(self) -> tuple[str, str]:
        """Return the content type of the active partial backend and the stock one."""
        return (partial_backend_manager.get().content_type, CONTENT_TYPE)


__all__ = ["RealtimeNegotiationImpl"]
