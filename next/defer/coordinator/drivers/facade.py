"""The coordinator a response asks for its batches, on a thread or on a server loop."""

from collections.abc import AsyncGenerator, Generator

from next.defer.coordinator.machine import Emit, Finish, Totals
from next.defer.ledger import ResponseJobs

from .base import COALESCE, Idle
from .loop import AsyncDriver
from .thread import ThreadDriver


class Coordinator:
    """Run the deferred jobs of one response, handing its outcomes over batch by batch.

    Asking for the next batch acknowledges the last one as written, so a consumer that
    stops early leaves it unwritten and cancels what still runs.
    """

    def __init__(self, *, coalesce: float = COALESCE) -> None:
        """Run on the process backends, outcomes waiting `coalesce` to share a write."""
        self.coalesce = coalesce
        self.finish: Finish | None = None
        self.failed = False

    @property
    def totals(self) -> Totals | None:
        """Return the counts of the last response, `None` before it finished."""
        return None if self.finish is None else self.finish.totals

    def run(
        self,
        session: ResponseJobs,
        *,
        idle: float | None = None,
        buffered: bool = False,
    ) -> Generator[Emit | Idle, None, None]:
        """Yield the batches on the calling thread, a WSGI worker or a plain thread.

        With `idle` set, `IDLE` follows each quiet `idle` seconds, so a writer can ping.
        `buffered` says the consumer writes nothing before the last batch.
        """
        self.finish = None
        self.failed = False
        return ThreadDriver(self, session, idle=idle, buffered=buffered).run()

    def arun(self, session: ResponseJobs) -> AsyncGenerator[Emit, None]:
        """Yield the batches on the running server loop."""
        self.finish = None
        self.failed = False
        return AsyncDriver(self, session).run()


__all__ = ["Coordinator"]
