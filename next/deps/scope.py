"""Per-request memo that shares async dependency values across the jobs of a render."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, NoReturn, cast

from next.bridge import drive
from next.bridge.loop import running_loop

from .cache import _CACHE_MISS, RequestMemo, request_memo
from .errors import DependencyCycleError


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Mapping
    from contextlib import AbstractContextManager
    from types import TracebackType


_OWNER: ContextVar[tuple[RequestScope, str] | None] = ContextVar(
    "next_deps_scope_owner", default=None
)

join_waits: ContextVar[
    Callable[[RequestScope], AbstractContextManager[None]] | None
] = ContextVar("next_deps_scope_join_waits", default=None)
"""What a wait on a key pending elsewhere runs inside, given the root scope."""


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


class _Tree:
    """What a root scope and its layers share, closed for every layer by the root."""

    __slots__ = ("closed", "guard", "loop", "slots")

    def __init__(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Open the tree on the request loop, `None` until a resolve binds one."""
        self.loop = loop
        self.closed = False
        self.guard = threading.RLock()
        self.slots: dict[str, object] = {}


class RequestScope:
    """Compute each shared dependency once per request as a task the scope owns.

    Only its loop thread computes into it, while `get` and `layer` take any thread.
    """

    __slots__ = (
        "_chains",
        "_closed",
        "_closing",
        "_errors",
        "_layers",
        "_parent",
        "_pending",
        "_tasks",
        "_tree",
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
        tree = _Tree(running_loop() if loop is None else loop)
        self._open({} if seed is None else dict(seed), None, tree)

    def _open(
        self, values: dict[str, object], parent: RequestScope | None, tree: _Tree
    ) -> None:
        """Hold `values` with no work yet, the task, layer and callback sets unmade."""
        self._values = values
        self._errors: dict[str, _Failure] = {}
        self._pending: dict[str, asyncio.Future[object] | None] = {}
        self._chains: dict[str, list[str]] = {}
        self._tasks: set[asyncio.Task[object]] | None = None
        self._layers: set[RequestScope] | None = None
        self._closing: list[Callable[[], object]] | None = None
        self._parent = parent
        self._used = False
        self._closed = False
        self._tree = tree

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the loop of the request, the one its root was built or used on."""
        return self._tree.loop

    def slot[T](self, key: str, make: Callable[[RequestScope], T]) -> T:
        """Return what the root scope holds under `key`, made from the root on a miss.

        Any thread may call it, and `make` runs once a key under the scope lock.
        """
        tree = self._tree
        found = tree.slots.get(key, _CACHE_MISS)
        if found is _CACHE_MISS:
            with tree.guard:
                found = tree.slots.get(key, _CACHE_MISS)
                if found is _CACHE_MISS:
                    found = tree.slots[key] = make(self._root())
        return cast("T", found)

    def on_close(self, callback: Callable[[], object]) -> None:
        """Call `callback` when the scope closes, at once when it is closed already.

        Callbacks run before any computation is cancelled, so they can fail its waiters.
        """
        tree = self._tree
        with tree.guard:
            if not self._shut():
                self._enlist()
                if self._closing is None:
                    self._closing = []
                self._closing.append(callback)
                return
        callback()

    def layer(self) -> RequestScope:
        """Return a local layer that keeps its writes and reads this scope on a miss.

        Any thread may call it. Its parent holds it from its first claim or callback on.
        """
        child: RequestScope = RequestScope.__new__(RequestScope)
        child._open({}, self, self._tree)
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
        tree = self._tree
        if self._shut():
            msg = f'The request scope is closed, so "{key}" cannot be computed.'
            raise RuntimeError(msg)
        if tree.loop is None:
            tree.loop = asyncio.get_running_loop()
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
        with self._tree.guard:
            self._mark_closed()
            callbacks, self._closing = self._closing or (), None
            layers, self._layers = self._layers or (), None
        for callback in callbacks:
            callback()
        if layers:
            await asyncio.gather(*(layer.close() for layer in layers))
        tasks = list(self._tasks or ())
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
        with self._tree.guard:
            if self._used:
                return False
            self._mark_closed()
        self._values.clear()
        return True

    def _shut(self) -> bool:
        return self._closed or self._tree.closed

    def _mark_closed(self) -> None:
        """Close this scope alone, or the whole request when it is the root."""
        if self._parent is None:
            self._tree.closed = True
        else:
            self._closed = True

    def _root(self) -> RequestScope:
        scope = self
        while scope._parent is not None:
            scope = scope._parent
        return scope

    def _enlist(self) -> None:
        """Mark the scope as owning work, so its parent holds it until the close.

        A layer that never claims stays unheld, and its render drops it at the end.
        """
        with self._tree.guard:
            held: RequestScope = self
            while not held._used:
                held._used = True
                if held._parent is None:
                    return
                parent: RequestScope = held._parent
                if parent._layers is None:
                    parent._layers = set()
                parent._layers.add(held)
                held = parent

    async def _claim(
        self, key: str, compute: Callable[[], Coroutine[Any, Any, object]]
    ) -> object:
        if not self._used:
            self._enlist()
        context = copy_context()
        context.run(_OWNER.set, (self, key))
        coroutine = context.run(compute)
        self._pending[key] = None
        task = asyncio.create_task(coroutine, context=context)
        tasks = self._tasks
        if tasks is None:
            tasks = self._tasks = set()
        tasks.add(task)
        task.add_done_callback(partial(self._settle, tasks, key))
        return await self._wait(self._owner(), key, task)

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
            return await self._wait(owner, key, future)

    async def _wait(
        self, owner: str | None, key: str, outcome: asyncio.Future[object]
    ) -> object:
        """Wait for `outcome`, which a cancel of the waiter leaves running.

        Not `asyncio.shield`, as on 3.14 its cancelled waiter logs a memoised failure.
        """
        chain = None if owner is None else self._chains.setdefault(owner, [])
        if chain is not None:
            chain.append(key)
        try:
            if not outcome.done():
                await asyncio.wait((outcome,))
            return outcome.result()
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

    def _settle(
        self, tasks: set[asyncio.Task[object]], key: str, task: asyncio.Task[object]
    ) -> None:
        """Publish the outcome of `task` before any waiter resumes.

        The memo owns a failure, so the waiter future marks it retrieved for the log.
        """
        tasks.discard(task)
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


@asynccontextmanager
async def render_scope(seed: Mapping[str, object]) -> AsyncGenerator[RequestScope]:
    """Open the request scope of one async render and close it once the render ends.

    It is seeded from the shell cache and shared through the request memo.
    """
    scope = RequestScope(seed)
    token = request_memo.set(RequestMemo(scope))
    try:
        yield scope
    finally:
        request_memo.reset(token)
        await scope.close()


async def _scoped[T](seed: Mapping[str, object], work: Callable[[], Awaitable[T]]) -> T:
    async with render_scope(seed):
        return await work()


def bridged_scope[T](seed: Mapping[str, object], work: Callable[[], Awaitable[T]]) -> T:
    """Await `work` through the bridge from sync code, in a render scope of its own."""
    return drive(partial(_scoped, seed, work))


__all__ = ["RequestScope", "bridged_scope", "join_waits", "render_scope"]
