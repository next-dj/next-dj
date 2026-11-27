"""The part the transaction policy takes in the requests a page answers.

A safe request of a page that opens no alias and holds no error boundary runs bare.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

from next.lifecycle import Share

from .policy import atomic_scope, request_atomic, safe_aliases
from .savepoints import keep_savepoints


if TYPE_CHECKING:
    from types import TracebackType

    from next.lifecycle import Claiming, Gate, PageCall, PageFacts
    from next.requests import RequestKind

    from .policy import AtomicOverride


NAME: Final = "transactions"


class _Atomic:
    """The transaction one request runs in, its error boundaries sharing savepoints."""

    __slots__ = ("_atomic", "_kept")

    def __init__(self, override: AtomicOverride | None, call: PageCall) -> None:
        """Decide the request transaction from the method rule or `page.transaction`."""
        self._kept = keep_savepoints()
        self._atomic = request_atomic(atomic_scope(call.request, call.kind, override))

    def __enter__(self) -> None:
        """Keep the savepoint guards, then open the transaction."""
        self._kept.__enter__()
        try:
            self._atomic.__enter__()
        except BaseException as exc:
            self._kept.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the transaction, then let the savepoint guards go."""
        try:
            self._atomic.__exit__(exc_type, exc, traceback)
        finally:
            self._kept.__exit__(exc_type, exc, traceback)


class TransactionsParticipant:
    """The transaction policy's part in the requests a page answers."""

    name = NAME

    def claim(self) -> Claiming | None:
        """Return no claim, a transaction never tells a kind apart."""
        return None

    def gate(self, _kind: RequestKind) -> Gate | None:
        """Return no gate, a refused request opens no transaction."""
        return None

    def share(self, facts: PageFacts) -> Share | None:
        """Return the transaction a request needs, none for a bare safe one."""
        bare = facts.safe and not facts.guarded
        if bare and safe_aliases(facts.atomic) == ():
            return None
        return Share(around=partial(_Atomic, facts.atomic))


__all__ = ["NAME", "TransactionsParticipant"]
