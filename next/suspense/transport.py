"""The seam telling whether a response reaches its client chunk by chunk."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Final, override

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest

from next.backends import SingleBackendManager
from next.conf.defer import SCOPE, backend_option, defer_option, entry_options


_OPTION: Final = "option"
_ASGI: Final = "asgi"
_WSGI: Final = "wsgi"
_THREADS: Final = "wsgi.multithread"
_KEY: Final = "TRANSPORT"
_ATTR: Final = "_next_transport"


@dataclass(frozen=True, slots=True)
class TransportFacts:
    """How the response to one request reaches its client, each answer with its fact.

    `incremental` says a written chunk leaves at once, `concurrent` that others run too.
    """

    incremental: bool
    concurrent: bool
    incremental_reason: str
    concurrent_reason: str

    @property
    def reason(self) -> str:
        """Return the fact behind the answer holding a stream back, else the first."""
        if self.incremental and not self.concurrent:
            return self.concurrent_reason
        return self.incremental_reason


class StreamTransport(ABC):
    """Tell how the response to a request reaches its client, asked once per request."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        self.options: Mapping[str, object] = entry_options(config)

    @abstractmethod
    def facts(self, request: HttpRequest) -> TransportFacts:
        """Return whether the response to `request` streams, and alongside others."""


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


class _Marks:
    """The keys and key prefixes of one interface that name a buffering adapter.

    `heads` holds the first character of each prefix, so most keys fail on one lookup.
    """

    __slots__ = ("heads", "keys", "prefixes")

    def __init__(
        self, keys: tuple[str, ...] = (), prefixes: tuple[str, ...] = ()
    ) -> None:
        """Hold the exact keys and the prefixes, in the order they are tried."""
        self.keys = keys
        self.prefixes = prefixes
        self.heads = frozenset(prefix[:1] for prefix in prefixes)

    @classmethod
    def read(cls, table: object, kind: str) -> "_Marks":
        """Return the marks `table` lists for `kind`, none when it lists none."""
        entry = table.get(kind) if isinstance(table, Mapping) else None
        if not isinstance(entry, Mapping):
            return cls()
        return cls(_strings(entry.get("KEYS")), _strings(entry.get("PREFIXES")))

    def found(self, mapping: Mapping[str, object]) -> str | None:
        """Return the first mark `mapping` carries, a prefix for a prefixed key."""
        for key in self.keys:
            if key in mapping:
                return key
        prefixes, heads = self.prefixes, self.heads
        if prefixes:
            for key in mapping:
                if key[:1] in heads and key.startswith(prefixes):
                    return next(filter(key.startswith, prefixes))
        return None


type _Answer = tuple[bool, str]


def _answers(
    marks: _Marks, claim: _Answer, concurrent: _Answer
) -> dict[str | None, TransportFacts]:
    """Return the answer for no mark and for each mark of `marks`, keyed by the mark."""
    made: dict[str | None, TransportFacts] = {}
    for mark, (incremental, reason) in (
        (None, claim),
        *((mark, (False, mark)) for mark in (*marks.keys, *marks.prefixes)),
    ):
        made[mark] = TransportFacts(
            incremental=incremental,
            concurrent=concurrent[0],
            incremental_reason=reason,
            concurrent_reason=concurrent[1],
        )
    return made


class DefaultStreamTransport(StreamTransport):
    """Answer from the options, then the marks the project lists, then the server.

    Every answer is built at construction, so a request reads keys and builds nothing.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Read the options once and build every answer they leave possible."""
        super().__init__(config)
        incremental = self._flag("INCREMENTAL")
        concurrent = self._flag("CONCURRENT")
        table = self._option("BUFFERED_MARKS")
        detect = incremental is None
        self._asgi = _Marks.read(table, "ASGI") if detect else _Marks()
        self._wsgi = _Marks.read(table, "WSGI") if detect else _Marks()
        stated = (bool(incremental), _OPTION)
        held = None if concurrent is None else (concurrent, _OPTION)
        self._asgi_facts = _answers(
            self._asgi, (True, _ASGI) if detect else stated, held or (True, _ASGI)
        )
        claim = (True, _WSGI) if detect else stated
        self._sync_facts = _answers(self._wsgi, claim, held or (False, _THREADS))
        self._threaded_facts = _answers(self._wsgi, claim, held or (True, _THREADS))

    def _option(self, key: str) -> object:
        return backend_option(self.options, _KEY, key)

    def _flag(self, key: str) -> bool | None:
        value = self._option(key)
        return value if isinstance(value, bool) else None

    @override
    def facts(self, request: HttpRequest) -> TransportFacts:
        """Return the answer the options, the marks or the server's claims give."""
        if isinstance(request, ASGIRequest):
            return self._asgi_facts[self._asgi.found(request.scope)]
        meta = request.META
        answers = self._threaded_facts if meta.get(_THREADS) else self._sync_facts
        return answers[self._wsgi.found(meta)]


transports: Final = SingleBackendManager(
    SCOPE, base=StreamTransport, config=partial(defer_option, _KEY)
)
"""The transport backend of this process, below the manager the policy cannot import."""


def transport_of(request: HttpRequest) -> TransportFacts:
    """Return how the response to `request` reaches its client, asked once per request.

    The policy, the partial runtime and the connection facts all read it per response.
    """
    held = vars(request)
    facts = held.get(_ATTR)
    if not isinstance(facts, TransportFacts):
        facts = transports.get().facts(request)
        held[_ATTR] = facts
    return facts


__all__ = [
    "DefaultStreamTransport",
    "StreamTransport",
    "TransportFacts",
    "transport_of",
    "transports",
]
