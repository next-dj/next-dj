"""The savepoint and the journal mark a guarded subtree keeps or undoes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Concatenate

from next.conf.boundaries import boundary_settings
from next.deps.runner import run_on_request_thread
from next.transactions.policy import probe_atomic_aliases
from next.transactions.savepoints import LazySavepoint

from .scope import current


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from next.static.collector import StaticCollector
    from next.transactions import AtomicMode


class Protection:
    """The savepoint and the collector mark of one guarded subtree, in that order."""

    __slots__ = ("_collector", "_mark", "_savepoint")

    def __init__(
        self, savepoint: LazySavepoint, collector: StaticCollector | None, mark: int
    ) -> None:
        """Hold what `open` took."""
        self._savepoint = savepoint
        self._collector = collector
        self._mark = mark

    @classmethod
    def open(
        cls,
        aliases: tuple[str, ...],
        atomic: AtomicMode,
        collector: StaticCollector | None,
    ) -> Protection:
        """Open the lazy savepoint, then mark the collector journal."""
        savepoint = LazySavepoint(aliases, atomic=atomic)
        savepoint.__enter__()
        mark = 0 if collector is None else collector.mark()
        return cls(savepoint, collector, mark)

    def keep(self) -> None:
        """Release the savepoint, then the mark, keeping what the subtree did."""
        try:
            self._savepoint.__exit__(None, None, None)
        finally:
            if self._collector is not None:
                self._collector.release()

    def undo(self) -> bool:
        """Roll back the savepoint, then the journal, telling whether the undo held.

        A subtree whose transaction failed before its savepoint leaves it to the next.
        """
        try:
            return self._savepoint.rollback()
        finally:
            if self._collector is not None:
                self._collector.rollback(self._mark)


def protect(collector: StaticCollector | None = None) -> Protection:
    """Open the savepoint and the journal mark a boundary guards non-template work with.

    The mode is the `ATOMIC` value `contain` reads, and `guarded` keeps or undoes it.
    """
    state = current()
    if state is None:
        errors = boundary_settings()
        return Protection.open(probe_atomic_aliases(), errors.atomic, collector)
    atomic = state.settings().atomic
    aliases = probe_atomic_aliases() if atomic is False else state.aliases
    return Protection.open(aliases, atomic, collector)


def guarded[T, H, **P](
    protection: Protection,
    work: Callable[[], T],
    hold: Callable[Concatenate[Exception, P], H | None],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T | H:
    """Return what `work` returns, else what `hold` makes of its failure once undone.

    A failure `hold` answers `None` for, or whose rollback did not hold, propagates.
    """
    try:
        result = work()
    except Exception as exc:
        held = hold(exc, *args, **kwargs) if protection.undo() else None
        if held is None:
            raise
        return held
    except BaseException:
        protection.undo()
        raise
    protection.keep()
    return result


async def aguarded[T, H, **P](
    work: Awaitable[T],
    hold: Callable[Concatenate[Exception, P], H | None],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T | H:
    """Await what `guarded` runs, the protection kept on the thread of the request.

    Every blocking segment of the work runs there, so the savepoint covers it.
    """
    protection = await run_on_request_thread(protect)
    try:
        result = await work
    except Exception as exc:
        undone = await run_on_request_thread(protection.undo)
        held = hold(exc, *args, **kwargs) if undone else None
        if held is None:
            raise
        return held
    except BaseException:
        await run_on_request_thread(protection.undo)
        raise
    await run_on_request_thread(protection.keep)
    return result


__all__ = ["Protection", "aguarded", "guarded", "protect"]
