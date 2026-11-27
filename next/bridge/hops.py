"""The one module that reads and writes the private surface of asgiref.

The async bridge, the pool lane and the loader route their hops through these names, so
a release that moves the surface fails here, in the contract tests and the system check.
"""

import asyncio
import functools
import logging
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import Context, ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar, Final, cast, override

import asgiref
from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.local import Local
from asgiref.sync import AsyncToSync, SyncToAsync, ThreadSensitiveContext
from django.core.exceptions import ImproperlyConfigured

from next.caches import pure_cache

from .tasks import StealableTask


logger = logging.getLogger(__name__)

TESTED: Final = ((3, 12), (3, 14))
"""The asgiref releases the contract tests pass on, the floor kept, the ceiling not."""

_WALKED: Final = ("_work_ready", "_broken", "_old_executor")
_RELEASE: Final = re.compile(r"(\d+)\.(\d+)")
_UNQUEUED: Final = (
    "asgiref started the hop without submitting it to the pool lane, so the unit has "
    "no stealable task. The installed asgiref reorders SyncToAsync.__call__."
)


def current_executor() -> Executor | None:
    """Return the executor that takes the thread-sensitive calls of this context."""
    executor: Executor | None = getattr(AsyncToSync.executors, "current", None)
    return executor


def route_to(executor: Executor | None) -> None:
    """Send the thread-sensitive calls of this context to `executor`."""
    AsyncToSync.executors.current = executor


def sensitive_context() -> ThreadSensitiveContext | None:
    """Return the `ThreadSensitiveContext` whose thread serves this context's calls."""
    found: ThreadSensitiveContext | None = SyncToAsync.thread_sensitive_context.get(
        None
    )
    return found


def enter_sensitive(context: ThreadSensitiveContext) -> None:
    """Serve the thread-sensitive calls of this context on the thread of `context`."""
    SyncToAsync.thread_sensitive_context.set(context)


def parent_loop() -> asyncio.AbstractEventLoop | None:
    """Return the loop whose `sync_to_async` call runs this thread, as asgiref reads it.

    A loop marked before a fork belongs to the parent, so another pid reads as none.
    """
    markers = SyncToAsync.threadlocal
    if getattr(markers, "main_event_loop_pid", None) != os.getpid():
        return None
    loop: asyncio.AbstractEventLoop | None = getattr(markers, "main_event_loop", None)
    return loop


def parent_tasks() -> list[asyncio.Task[Any]] | None:
    """Return the list the `sync_to_async` call running this thread cancels through."""
    tasks: list[asyncio.Task[Any]] | None = getattr(
        SyncToAsync.threadlocal, "task_context", None
    )
    return tasks


def unmarked[R](call: Callable[[], R]) -> R:
    """Return what `call` returns, putting back the loop markers asgiref leaves here.

    A stale marker would send a later `async_to_sync` on this thread to that loop.
    """
    markers = vars(SyncToAsync.threadlocal)
    saved = dict(markers)
    try:
        return call()
    finally:
        markers.clear()
        markers.update(saved)


class QuitParentExecutor(Executor):
    """An executor asgiref reads as a quit parent when bound as its current executor.

    asgiref walks the attributes of its own thread executor, which these stand in for.
    """

    _work_ready: ClassVar[nullcontext[None]] = nullcontext()
    _broken: ClassVar[bool] = True
    _old_executor: ClassVar[None] = None


def hop_on[**P, R](
    func: Callable[P, R], executor: Executor, context: Context | None = None
) -> SyncToAsync[P, R]:
    """Return `func` as a hop that asgiref submits to `executor` from the loop.

    asgiref annotates the executor as a `ThreadPoolExecutor` but hands it straight to
    `run_in_executor`, which takes any `Executor`, so no thread pool is built for it.
    """
    return SyncToAsync(
        func,
        thread_sensitive=False,
        executor=cast("ThreadPoolExecutor", executor),
        context=context,
    )


class InlineExecutor(Executor):
    """Run each call on the submitting thread at once, a hop that moves nowhere.

    asgiref moves the `Local` values of a hop to the thread it runs on, here the loop.
    """

    @override
    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Run the call here, its outcome on the returned future."""
        task = StealableTask(functools.partial(fn, *args, **kwargs))
        unmarked(task.run)
        return task.future


class Handoff(Executor):
    """Queue the call each `start` makes asgiref submit as one stealable unit."""

    def __init__(self, queue: Callable[[StealableTask[Any]], object]) -> None:
        """Queue onto `queue`, which raises when it refuses a unit."""
        self._queue = queue
        self._taken: StealableTask[Any] | None = None

    @override
    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Queue the call, raising what the queue refuses it with."""
        task = StealableTask(
            functools.partial(unmarked, functools.partial(fn, *args, **kwargs))
        )
        self._queue(task)
        self._taken = task
        return task.future

    def start(
        self,
        call: Callable[[], None],
        context: Context,
        loop: asyncio.AbstractEventLoop,
    ) -> tuple[asyncio.Task[None], StealableTask[None]]:
        """Start the hop of `call` in `context` and return it with the unit it queued.

        asgiref submits before the first await of an eagerly started hop, so the unit
        exists on return, and a refusal of the queue is raised here.
        """
        task = asyncio.Task(hop_on(call, self, context)(), loop=loop, eager_start=True)
        if task.done():
            task.result()
        taken, self._taken = self._taken, None
        if taken is None:
            task.cancel()
            raise RuntimeError(_UNQUEUED)
        return task, taken


def release() -> tuple[int, int] | None:
    """Return the major and minor number of the installed asgiref, `None` if unread."""
    found = _RELEASE.match(str(getattr(asgiref, "__version__", "")))
    if found is None:
        return None
    return int(found[1]), int(found[2])


def _named(release: tuple[int, int]) -> str:
    return f"{release[0]}.{release[1]}"


def gaps() -> tuple[str, ...]:
    """Name the private asgiref attributes the hops use that the installed one lacks."""
    try:
        walked = vars(CurrentThreadExecutor(None))
    except TypeError:
        walked = {}
    present = {
        "AsyncToSync.executors": isinstance(
            getattr(AsyncToSync, "executors", None), Local
        ),
        "SyncToAsync.threadlocal": isinstance(
            getattr(SyncToAsync, "threadlocal", None), threading.local
        ),
        "SyncToAsync.thread_sensitive_context": isinstance(
            getattr(SyncToAsync, "thread_sensitive_context", None), ContextVar
        ),
    } | {f"CurrentThreadExecutor.{name}": name in walked for name in _WALKED}
    return tuple(name for name, found in present.items() if not found)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Why the installed asgiref cannot carry the hops, and why it is untested."""

    error: str | None = None
    warning: str | None = None


@pure_cache()
def verdict() -> Verdict:
    """Judge the installed asgiref once per process, by its attributes and release."""
    floor, ceiling = TESTED
    missing = gaps()
    if missing:
        return Verdict(
            error=(
                f"The installed asgiref lacks {', '.join(missing)}, which routes the "
                "thread-sensitive calls of async page code to the request thread. "
                f"Install asgiref {_named(floor)} or later."
            )
        )
    found = release()
    if found is not None and found < floor:
        return Verdict(
            error=(
                f"The installed asgiref {_named(found)} predates {_named(floor)}, the "
                "first release whose Local the async bridge carries across threads. "
                f"Install asgiref {_named(floor)} or later."
            )
        )
    if found is None or found >= ceiling:
        named = "of an unknown release" if found is None else _named(found)
        return Verdict(
            warning=(
                f"The installed asgiref {named} is outside the releases the async "
                f"bridge is tested on, {_named(floor)} up to {_named(ceiling)}, so a "
                "thread-sensitive call of async page code may reach the wrong thread."
            )
        )
    return Verdict()


@pure_cache()
def check_asgiref() -> None:
    """Refuse to drive coroutines on an asgiref the hops cannot use, warning once.

    It is the last guard where no system check runs, as under gunicorn or uvicorn.
    """
    found = verdict()
    if found.error is not None:
        raise ImproperlyConfigured(found.error)
    if found.warning is not None:
        logger.warning("%s", found.warning)


__all__ = [
    "TESTED",
    "Handoff",
    "InlineExecutor",
    "QuitParentExecutor",
    "Verdict",
    "check_asgiref",
    "current_executor",
    "enter_sensitive",
    "gaps",
    "hop_on",
    "parent_loop",
    "parent_tasks",
    "release",
    "route_to",
    "sensitive_context",
    "unmarked",
    "verdict",
]
