"""Work run once by its first claimant, executors asgiref skips, context snapshots."""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor, Future
from contextlib import nullcontext
from contextvars import Context
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


class ContextSnapshot:
    """A snapshot of the request's context, with a copy kept for each thread.

    asgiref 3.12 shows a `Local` value only on a thread that its own hop carried it to.
    """

    __slots__ = ("_copies", "_landings", "context", "thread")

    def __init__(self, context: Context, thread: int | None = None) -> None:
        """Hold `context`, captured on `thread`, the calling one by default."""
        self.context = context
        self.thread = threading.get_ident() if thread is None else thread
        self._copies: dict[int, Context] = {}
        self._landings: dict[int, asyncio.Task[Context]] = {}

    def get(self) -> Context | None:
        """Return the context restored on this thread, `None` until one was kept."""
        here = threading.get_ident()
        if here == self.thread:
            return self.context
        return self._copies.get(here)

    def keep(self, context: Context) -> Context:
        """Hold `context`, which a hop restored on this thread, and return it."""
        self._copies[threading.get_ident()] = context
        return context

    def landing(
        self, carry: Callable[[], Coroutine[Any, Any, Context]]
    ) -> asyncio.Task[Context]:
        """Return the task restoring a copy on the running loop, started by the first.

        Every caller on one loop waits on the one task, so one hop serves all of them.
        """
        here = threading.get_ident()
        task = self._landings.get(here)
        if task is None:
            loop = asyncio.get_running_loop()
            task = loop.create_task(carry(), context=self.context.copy())
            self._landings[here] = task
        return task


__all__ = ["ContextSnapshot", "QuitParentExecutor", "StealableTask"]
