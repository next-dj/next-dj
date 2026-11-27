from dataclasses import dataclass

from next.partial.headers import VARY_HEADERS


_PARTIAL = frozenset(VARY_HEADERS)
_STREAMED = _PARTIAL | {"Accept", "Accept-Encoding", "Cookie"}
_ZONE = _PARTIAL | {"Accept", "X-Next-Origin"}
_RELOAD = _PARTIAL | {"Accept", "Cookie"}
_STREAM = "private, no-cache, no-store, no-transform"
_NO_STORE = "private, no-store"


@dataclass(frozen=True, slots=True)
class HeaderCase:
    """One kind of request to a page of one outcome, and the caching headers it ends.

    A `reload` names the reloaded component in `target`, and `stream` asks for SSE.
    """

    id: str
    kind: str
    page: str
    cache_control: str
    vary: frozenset[str]
    stream: bool = False
    target: str = ""
    status: int = 200


HEADER_CASES: tuple[HeaderCase, ...] = (
    HeaderCase("page", "page", "plain", "public, max-age=60", _PARTIAL),
    HeaderCase("page-degraded", "page", "degraded", _NO_STORE, _PARTIAL),
    HeaderCase("page-caught", "page", "caught", _NO_STORE, _PARTIAL, status=500),
    HeaderCase("page-stream", "page", "streamed", "private, no-cache", _STREAMED),
    HeaderCase(
        "page-stream-degraded", "page", "both", "no-store, private, no-cache", _STREAMED
    ),
    HeaderCase("zone", "zone", "plain", _NO_STORE, _ZONE),
    HeaderCase("zone-degraded", "zone", "degraded", _NO_STORE, _ZONE),
    HeaderCase("zone-caught", "zone", "caught", _NO_STORE, _ZONE),
    HeaderCase(
        "zone-stream",
        "zone",
        "streamed",
        _STREAM,
        _STREAMED | {"X-Next-Origin"},
        stream=True,
    ),
    HeaderCase(
        "zone-stream-degraded",
        "zone",
        "both",
        _STREAM,
        _STREAMED | {"X-Next-Origin"},
        stream=True,
    ),
    HeaderCase("navigate", "navigate", "plain", _NO_STORE, _PARTIAL),
    HeaderCase("navigate-degraded", "navigate", "degraded", _NO_STORE, _PARTIAL),
    HeaderCase("navigate-caught", "navigate", "caught", _NO_STORE, _PARTIAL),
    HeaderCase(
        "navigate-stream", "navigate", "streamed", _STREAM, _STREAMED, stream=True
    ),
    HeaderCase(
        "navigate-stream-degraded", "navigate", "both", _STREAM, _STREAMED, stream=True
    ),
    HeaderCase("reload", "reload", "streamed", _NO_STORE, _RELOAD, target="slow"),
    HeaderCase(
        "reload-caught", "reload", "degraded", _NO_STORE, _RELOAD, target="broken"
    ),
    HeaderCase(
        "reload-stream",
        "reload",
        "card",
        _STREAM,
        _STREAMED,
        stream=True,
        target="card",
    ),
)


__all__ = ["HEADER_CASES", "HeaderCase"]
