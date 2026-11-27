"""The realtime offer every runtime page carries, as a page script provider."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from next.caches import pure_cache
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.partial.stream import PROTOCOL
from next.ports import ScriptShare
from next.static.runtime import REALTIME_PAYLOAD_KEY, WS_CHUNK_KEY, payload_entry

from .transports import active_transports


if TYPE_CHECKING:
    from next.ports import ScriptRender


DEFAULT_TRANSPORTS: Final = ("sse",)
"""The transports the runtime assumes when the payload carries no `$realtime`."""

PRIORITY: Final = 200
"""The rank of the offer among the page script providers, after the release."""

_WEBSOCKET: Final = frozenset({WS_CHUNK_KEY})


@pure_cache(4)
def _offered(transports: tuple[str, ...], content_type: str) -> ScriptShare | None:
    """Return the share of one transport list and partial content type.

    The stock pair is the runtime default, so it adds nothing to the page.
    """
    if transports == DEFAULT_TRANSPORTS and content_type == CONTENT_TYPE:
        return None
    offer = json.dumps(
        {"protocol": PROTOCOL, "transports": transports, "content_type": content_type},
        separators=(",", ":"),
    )
    return ScriptShare(
        shared=payload_entry(REALTIME_PAYLOAD_KEY, offer),
        listed=_WEBSOCKET if WS_CHUNK_KEY in transports else frozenset(),
    )


class RealtimeScripts:
    """The `$realtime` entry and the `ws` chunk a page lists off the stock transports.

    The share is keyed on the transports and the content type, so a reset partial
    backend or a newly mounted WebSocket never leaves a stale one behind.
    """

    name: Final = "realtime"
    priority: Final = PRIORITY
    keys: Final = frozenset({REALTIME_PAYLOAD_KEY})

    def applies(self) -> bool:
        """Return true, as the transports may change without a settings reload."""
        return True

    def share(self, _render: ScriptRender, /) -> ScriptShare | None:
        """Return the offer of the active transports, `None` for the stock one."""
        content_type = partial_backend_manager.get().content_type
        return _offered(active_transports(), content_type)


__all__ = ["DEFAULT_TRANSPORTS", "RealtimeScripts"]
