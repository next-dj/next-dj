"""Work that runs at most once on its first claimant, and executors asgiref skips."""

import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import nullcontext
from typing import Any, ClassVar


class QuitParentExecutor(Executor):
    """An executor asgiref reads as a quit parent when bound as its current executor.

    asgiref walks the attributes of its own thread executor, which these stand in for.
    """

    _work_ready: ClassVar[nullcontext[None]] = nullcontext()
    _broken: ClassVar[bool] = True
    _old_executor: ClassVar[None] = None


class StealableTask[R]:
    """Work run at most once by its first claimant, its outcome kept on `future`.

    A waiter that finds the work unstarted runs it itself, so none waits on idle work.
    """

    __slots__ = ("_call", "_claim", "_hook", "future")

    def __init__(self, call: Callable[[], R]) -> None:
        """Hold `call` for its claimant."""
        self._call = call
        self._claim = threading.Lock()
        self._hook: tuple[Callable[[], object], threading.Lock] | None = None
        self.future: Future[R] = Future()

    def when_claimed(self, hook: Callable[[], object]) -> None:
        """Call `hook` once, when the work is claimed or its future ends unclaimed."""
        self._hook = (hook, threading.Lock())
        self.future.add_done_callback(self._ended)
        if self._claim.locked():
            self._fire()

    def claim(self) -> bool:
        """Take the work without blocking, false when taken already or cancelled."""
        if not self._claim.acquire(blocking=False):
            return False
        self._fire()
        return self.future.set_running_or_notify_cancel()

    def run(self) -> bool:
        """Run the work if this caller claims it and say whether it did.

        An interrupt reaches the future as a `RuntimeError`, which no task re-raises.
        """
        if not self.claim():
            return False
        try:
            result = self._call()
        except BaseException as error:
            if isinstance(error, Exception):
                self.future.set_exception(error)
                return True
            interrupted = RuntimeError("The thread was interrupted in this call.")
            interrupted.__cause__ = error
            self.future.set_exception(interrupted)
            raise
        self.future.set_result(result)
        return True

    def reject(self, error: Exception) -> bool:
        """Fail the work with `error` unless a runner took it, saying if it did."""
        if not self.claim():
            return False
        self.future.set_exception(error)
        return True

    def _ended(self, _future: Future[Any]) -> None:
        self._fire()

    def _fire(self) -> None:
        """Call the hook on the first of the claim and the end, never twice."""
        held = self._hook
        if held is None:
            return
        hook, once = held
        if once.acquire(blocking=False):
            hook()


__all__ = ["QuitParentExecutor", "StealableTask"]
