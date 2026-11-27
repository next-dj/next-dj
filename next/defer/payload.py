"""The stream flag and the reader bundle a page that streams adds to its scripts."""

from __future__ import annotations

import itertools
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from next.ports import ScriptShare
from next.static.runtime import BOUNDARIES_CHUNK_KEY, STREAM_PAYLOAD_KEY, payload_entry


if TYPE_CHECKING:
    from next.ports import ScriptRender


PRIORITY: Final = 300
"""The rank of the stream flag among the page script providers, before the router."""

partial_streams: ContextVar[bool] = ContextVar("next_partial_streams", default=False)
"""Whether the page under render may receive streamed partial responses."""

_STREAM_ENTRY: Final = payload_entry(STREAM_PAYLOAD_KEY, "true")
_READER: Final = (BOUNDARIES_CHUNK_KEY,)


def _streamed(*, streams: bool, partials: bool, runtime: bool) -> ScriptShare | None:
    """Return the share of one combination, the boundaries bundle reading a stream.

    The flag needs the reader only on a page whose runtime reads the flag.
    """
    if not (streams or partials):
        return None
    return ScriptShare(
        shared=_STREAM_ENTRY if partials else "",
        eager=_READER if streams else (),
        bundles=_READER if partials and runtime else (),
    )


_SHARES: Final = {
    (streams, partials, runtime): _streamed(
        streams=streams, partials=partials, runtime=runtime
    )
    for streams, partials, runtime in itertools.product((False, True), repeat=3)
}


class StreamScripts:
    """The defer engine's part in a page that streams its boundaries or its partials."""

    name: Final = "stream"
    priority: Final = PRIORITY
    keys: Final = frozenset({STREAM_PAYLOAD_KEY})

    def applies(self) -> bool:
        """Return true, as the stream policy reads each request."""
        return True

    def share(self, render: ScriptRender, /) -> ScriptShare | None:
        """Return the stream flag and the reader need of `render`, `None` for none.

        A streamed page loads the reader as soon as it can, since its chunks wait on it.
        """
        return _SHARES[render.streams, partial_streams.get(), render.runtime]


__all__ = ["StreamScripts", "partial_streams"]
