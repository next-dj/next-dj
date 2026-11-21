"""Per-request memo that shares async dependency values across the jobs of a render."""

from __future__ import annotations

import asyncio
import threading
from contextlib import nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, NoReturn, cast

from .cache import _CACHE_MISS
from .errors import DependencyCycleError


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping
    from contextlib import AbstractContextManager
    from types import TracebackType


_OWNER: ContextVar[tuple[RequestScope, str] | None] = ContextVar(
    "next_deps_scope_owner", default=None
)

join_waits: ContextVar[
    Callable[[RequestScope], AbstractContextManager[None]] | None
] = ContextVar("next_deps_scope_join_waits", default=None)
"""What a wait on a key pending elsewhere runs inside, given the root scope."""


def running_loop() -> asyncio.AbstractEventLoop | None:
    """Return the event loop the calling thread runs, `None` on a thread without one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _throw(error: BaseException, traceback: TracebackType | None) -> NoReturn:
    raise error.with_traceback(traceback)


@dataclass(frozen=True, slots=True)
class _Failure:
    """A memoised error with the traceback and context it was first raised with."""

    error: BaseException
    traceback: TracebackType | None
    context: BaseException | None

    def rethrow(self) -> NoReturn:
        """Raise the error as first raised, whatever exception the reader is handling.

        A raise chains the exception being handled into `__context__`, so it is reset.
        """
        try:
            _throw(self.error, self.traceback)
        except BaseException:
            self.error.__context__ = self.context
            raise


class RequestScope:
    """Compute each shared dependency once per request as a task the scope owns.

    Only its loop thread computes into it, while `get` and `layer` take any thread.
    """

    __slots__ = (
        "_chains",
        "_closed",
        "_closing",
        "_errors",
        "_guard",
        "_layers",
        "_loop",
        "_parent",
        "_pending",
        "_slots",
        "_tasks",
        "_used",
        "_values",
    )

    def __init__(
        self,
        seed: Mapping[str, object] | None = None,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Copy `seed` in, so no write reaches its owner, and bind the request loop.

        That is `loop`, else the running one, so a scope built off it has it at once.
        """
        self._values: dict[str, object] = {} if seed is None else dict(seed)
        self._errors: dict[str, _Failure] = {}
        self._pending: dict[str, asyncio.Future[object] | None] = {}
        self._chains: dict[str, list[str]] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._layers: set[RequestScope] = set()
        self._parent: RequestScope | None = None
        self._closed = False
        self._closing: list[Callable[[], object]] = []
        self._slots: dict[str, object] = {}
        self._guard = threading.RLock()
        self._used = False
        self._loop = running_loop() if loop is None else loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the loop of the request, the one its root was built or used on."""
        root: RequestScope = self._root()
        return root._loop

    def slot[T](self, key: str, make: Callable[[RequestScope], T]) -> T:
        """Return what the root scope holds under `key`, made from the root on a miss.

        Any thread may call it, and `make` runs once a key under the scope lock.
        """
        root: RequestScope = self._root()
        found = root._slots.get(key, _CACHE_MISS)
        if found is _CACHE_MISS:
            with root._guard:
                found = root._slots.get(key, _CACHE_MISS)
                if found is _CACHE_MISS:
                    found = root._slots[key] = make(root)
        return cast("T", found)

    def on_close(self, callback: Callable[[], object]) -> None:
        """Call `callback` when the scope closes, at once when it is closed already.

        Callbacks run before any computation is cancelled, so they can fail its waiters.
        """
        with self._guard:
            if not self._closed:
                self._used = True
                self._closing.append(callback)
                return
        callback()

    def layer(self) -> RequestScope:
        """Return a local layer that keeps its writes and reads this scope on a miss.

        Any thread may call it. Layers are held strongly, as a loop holds tasks weakly.
        """
        child = RequestScope(loop=self._loop)
        child._parent = self
        with self._guard:
            self._used = True
            child._closed = self._closed
            if not self._closed:
                self._layers.add(child)
        return child

    def get(self, key: str) -> object:
        """Return the value of `key` or `_CACHE_MISS`, raising a memoised error.

        A key pending elsewhere reads as a miss, so a pool thread computes it privately.
        """
        value = self._values.get(key, _CACHE_MISS)
        if value is _CACHE_MISS:
            failure = self._errors.get(key)
            if failure is not None:
                failure.rethrow()
            if self._parent is not None:
                return self._parent.get(key)
        return value

    def snapshot(self) -> dict[str, object]:
        """Return a copy of every finished value, the base of a private cache."""
        if self._parent is None:
            return dict(self._values)
        return {**self._parent.snapshot(), **self._values}

    async def resolve(
        self,
        key: str,
        compute: Callable[[], Coroutine[Any, Any, object]],
        *,
        shared: bool = True,
    ) -> object:
        """Return the value of `key`, computing it at most once when it is shared.

        `compute` is called only on a miss and at most once per claim.
        """
        if self._closed:
            msg = f'The request scope is closed, so "{key}" cannot be computed.'
            raise RuntimeError(msg)
        if self._loop is None:
            self._bind_loop()
        value = self.get(key)
        if value is not _CACHE_MISS:
            return value
        if not shared:
            return await compute()
        holder: RequestScope = self
        while True:
            if key in holder._pending:
                return await holder._join(key)
            parent = holder._parent
            if parent is None:
                return await self._claim(key, compute)
            holder = parent

    async def close(self) -> None:
        """Run the close callbacks, close the layers, then cancel what is left.

        Every map is dropped at the end, the slots stay for a late reader to see closed.
        """
        with self._guard:
            self._closed = True
            callbacks, self._closing = self._closing, []
            layers = list(self._layers)
            self._layers.clear()
        for callback in callbacks:
            callback()
        if layers:
            await asyncio.gather(*(layer.close() for layer in layers))
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._values.clear()
        self._errors.clear()
        self._pending.clear()
        self._chains.clear()

    def close_unused(self) -> bool:
        """Close the scope on this thread when nothing ever ran on its loop, else not.

        Without a claim, a layer or a close callback its loop owns nothing to close.
        """
        with self._guard:
            if self._used:
                return False
            self._closed = True
        self._values.clear()
        return True

    def _root(self) -> RequestScope:
        scope = self
        while scope._parent is not None:
            scope = scope._parent
        return scope

    def _bind_loop(self) -> None:
        """Adopt the running loop as the request loop, for a scope built off it."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        root: RequestScope = self._root()
        if root._loop is None:
            root._loop = loop

    async def _claim(
        self, key: str, compute: Callable[[], Coroutine[Any, Any, object]]
    ) -> object:
        self._used = True
        context = copy_context()
        context.run(_OWNER.set, (self, key))
        coroutine = context.run(compute)
        self._pending[key] = None
        task = asyncio.create_task(coroutine, context=context)
        self._tasks.add(task)
        task.add_done_callback(partial(self._settle, key))
        return await self._wait(self._owner(), key, asyncio.shield(task))

    async def _join(self, key: str) -> object:
        owner = self._owner()
        if owner is not None:
            cycle = self._cycle(owner, key)
            if cycle is not None:
                raise DependencyCycleError(cycle)
        future = self._pending[key]
        if future is None:
            future = self._pending[key] = asyncio.get_running_loop().create_future()
        hook = join_waits.get()
        waits = nullcontext() if hook is None else hook(self._root())
        with waits:
            return await self._wait(owner, key, asyncio.shield(future))

    async def _wait(
        self, owner: str | None, key: str, waiter: asyncio.Future[object]
    ) -> object:
        chain = None if owner is None else self._chains.setdefault(owner, [])
        if chain is not None:
            chain.append(key)
        try:
            return await waiter
        except BaseException as error:
            failure = self._errors.get(key)
            if failure is not None and failure.error is error:
                failure.rethrow()
            raise
        finally:
            if owner is not None and chain is not None:
                chain.remove(key)
                if not chain:
                    self._chains.pop(owner, None)

    def _owner(self) -> str | None:
        owner = _OWNER.get()
        if owner is None or owner[0] is not self:
            return None
        return owner[1]

    def _cycle(self, owner: str, key: str) -> list[str] | None:
        """Return the wait path from `key` back to `owner`, visiting each chain once."""
        paths = [[owner, key]]
        seen: set[str] = set()
        while paths:
            path = paths.pop()
            last = path[-1]
            if last == owner:
                return path
            if last in seen:
                continue
            seen.add(last)
            paths.extend([*path, awaited] for awaited in self._chains.get(last, ()))
        return None

    def _settle(self, key: str, task: asyncio.Task[object]) -> None:
        """Publish the outcome of `task` before any waiter resumes.

        The memo owns a failure, so the waiter future marks it retrieved for the log.
        """
        self._tasks.discard(task)
        future = self._pending.pop(key, None)
        if task.cancelled():
            if future is not None:
                future.cancel()
            return
        error = task.exception()
        if error is None:
            value = task.result()
            self._values[key] = value
            if future is not None:
                future.set_result(value)
            return
        self._errors[key] = _Failure(error, error.__traceback__, error.__context__)
        if future is not None:
            future.set_exception(error)
            future.exception()


__all__ = ["RequestScope", "join_waits", "running_loop"]
