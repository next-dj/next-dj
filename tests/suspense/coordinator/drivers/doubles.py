import asyncio
import contextlib
import contextvars
import inspect
import threading
import time
from collections.abc import Callable, Coroutine, Iterator, Sequence
from concurrent.futures import Future, wait
from typing import Any

from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.test import override_settings

from next.defer import BoundaryOptions, DeferMode, ErrorKind
from next.deps import Loader
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope
from next.suspense import (
    Admit,
    BoundaryExecutor,
    BoundaryShedError,
    Capacity,
    ExecutorStoppedError,
    Job,
    LaneExecutor,
    Server,
)
from next.suspense.coordinator import (
    Admitter,
    Coordinator,
    Emit,
    Foreground,
    Settlement,
)
from next.suspense.executors import background_loop
from next.suspense.markers import (
    Addressing,
    Caps,
    ForegroundFacts,
    Outcome,
    Rendered,
    Site,
)
from next.suspense.session import BoundarySession
from next.tasks import ContextSnapshot, StealableTask
from tests.support.waits import QUIET, WAIT


WSGI = ForegroundFacts(Server.WSGI_THREADED)
ASGI = ForegroundFacts(Server.ASGI)
ATOMIC_WSGI = ForegroundFacts(Server.WSGI_THREADED, atomic=("default",))
ATOMIC_ASGI = ForegroundFacts(Server.ASGI, atomic=("default",))
RUNNING_TIMEOUT = 0.3

type Body = Callable[[], Rendered[str]]
type AsyncBody = Callable[[], Coroutine[Any, Any, Rendered[str]]]


def noop() -> None:
    return None


def fallback_ui(kind: ErrorKind) -> Rendered[str]:
    return Rendered(f"fallback:{kind}", "")


def new_session(
    *,
    facts: ForegroundFacts | None = WSGI,
    deadline: float = 5.0,
    grace: float = 1.0,
    per_site: int = 8,
    per_request: int = 4,
) -> BoundarySession[str]:
    session: BoundarySession[str] = BoundarySession.open(
        Caps(deadline, grace, 16, per_site, 3, 64, per_request)
    )
    session.facts = facts
    session.snapshot = ContextSnapshot(contextvars.copy_context())
    return session


def named_site(name: str) -> Site:
    return Site(Addressing.COMPONENT, name, DeferMode.STREAM, "list.djx")


def queue_job(
    session: BoundarySession[str],
    body: Body | None = None,
    *,
    abody: AsyncBody | None = None,
    site: Site | None = None,
    timeout: float | None = None,
    parallel: bool = True,
) -> Job[str]:
    job_id = session.next_id()
    options = BoundaryOptions(timeout=timeout, parallel=parallel)
    job = Job(
        job_id,
        "k",
        site or named_site(job_id),
        options,
        session.deadline_for(options, time.monotonic()),
        fallback_ui,
        run=body,
        arun=abody,
    )
    session.queue(job)
    return job


def ready_body(text: str) -> Body:
    def body() -> Rendered[str]:
        return Rendered(text, "")

    return body


def sleeping_body(seconds: float) -> Body:
    def body() -> Rendered[str]:
        time.sleep(seconds)
        return Rendered(str(threading.get_ident()), "")

    return body


def ident_body() -> Rendered[str]:
    return Rendered(str(threading.get_ident()), "")


def meeting_body(barrier: threading.Barrier) -> Body:
    """Return a body that passes only once every party is in flight with it."""

    def body() -> Rendered[str]:
        barrier.wait()
        return Rendered(str(threading.get_ident()), "")

    return body


def gated_body(started: threading.Event, release: threading.Event) -> Body:
    def body() -> Rendered[str]:
        started.set()
        release.wait(WAIT)
        return Rendered("late", "")

    return body


def recorded_fetch(log: list[object]) -> Callable[[Sequence[int]], dict[int, int]]:
    def fetch(keys: Sequence[int]) -> dict[int, int]:
        log.append(sorted(keys))
        return {key: key * 10 for key in keys}

    return fetch


def loading_body(
    loader: Loader[int, int], key: int, barrier: threading.Barrier | None = None
) -> Body:
    def body() -> Rendered[str]:
        if barrier is not None:
            barrier.wait()
        return Rendered(str(loader.load_sync(key)), "")

    return body


def scoped_session(session: BoundarySession[str]) -> RequestScope:
    """Give the jobs of `session` a request scope on the background loop."""
    scope = RequestScope(loop=background_loop.get())
    session.scope = scope
    assert session.snapshot is not None
    session.snapshot.context.run(batch_scope.set, scope)
    return scope


def close_scope(scope: RequestScope) -> None:
    loop = background_loop.get()
    asyncio.run_coroutine_threadsafe(scope.close(), loop).result(WAIT)


async def hop_body() -> Rendered[str]:
    return Rendered(str(await sync_to_async(threading.get_ident)()), "")


async def forever_body() -> Rendered[str]:
    await asyncio.sleep(60)
    return Rendered("never", "")


def html_of(item: Outcome[str] | Settlement[str]) -> str:
    assert isinstance(item, Outcome)
    return item.rendered.html


def ids_of(batches: list[Emit[str]]) -> list[list[str]]:
    return [[item.id for item in batch.items] for batch in batches]


def items_of(batches: list[Emit[str]]) -> dict[str, Outcome[str] | Settlement[str]]:
    return {item.id: item for batch in batches for item in batch.items}


class ScriptedAdmission:
    def __init__(self, *verdicts: Admit) -> None:
        """Answer with `verdicts` in turn, the last one for good."""
        self.verdicts = list(verdicts) or [Admit.POOL]

    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        if len(self.verdicts) > 1:
            return self.verdicts.pop(0)
        return self.verdicts[0]


class FailingAdmission:
    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        msg = "admission broke"
        raise RuntimeError(msg)


class CountingPool(LaneExecutor):
    """A pool that counts the units submitted to it."""

    def __init__(self, workers: int) -> None:
        """Start a pool of `workers` that counts the units it is given."""
        super().__init__({"OPTIONS": {"MAX_WORKERS": workers, "CONN_IDLE": 60.0}})
        self.submitted = 0

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.submitted += 1
        return super().submit(task)


class HeldPool(BoundaryExecutor):
    """A pool whose busy workers never start a unit, so the test decides what runs."""

    def __init__(self, *, stealable: bool = True) -> None:
        """Hold units without running them, stealable unless told otherwise."""
        self.stealable = stealable
        self.queued: list[StealableTask[Any]] = []
        self.asked: list[StealableTask[Any]] = []

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.queued.append(task)
        return task.future

    def steal(self, task: StealableTask[Any]) -> bool:
        self.asked.append(task)
        if not self.stealable or task not in self.queued:
            return False
        self.queued.remove(task)
        return task.run()

    def capacity(self) -> Capacity:
        return Capacity(4, 4, len(self.queued), 0.0)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.queued.clear()

    def start(self, index: int) -> StealableTask[Any]:
        """Take a queued unit as a worker would, without running it."""
        task = self.queued.pop(index)
        assert task.claim()
        return task


class IdlePool(HeldPool):
    """A pool reporting free workers, which start a unit only when the test runs it."""

    def __init__(self, units: int) -> None:
        """Expect `units` submissions and signal once they have arrived."""
        super().__init__()
        self.units = units
        self.submitted = threading.Event()

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        future = super().submit(task)
        if len(self.queued) == self.units:
            self.submitted.set()
        return future

    def capacity(self) -> Capacity:
        return Capacity(4, 0, len(self.queued), 0.0)


class StartedPool(HeldPool):
    """A pool whose one worker claims the first unit before its submission returns."""

    def __init__(self) -> None:
        """Start with no worker yet."""
        super().__init__()
        self.worker: threading.Thread | None = None

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        if self.worker is not None:
            return super().submit(task)
        claimed = threading.Event()
        task.when_claimed(claimed.set)
        self.worker = threading.Thread(target=task.run)
        self.worker.start()
        assert claimed.wait(WAIT)
        return task.future

    def join(self) -> None:
        if self.worker is not None:
            self.worker.join(WAIT)


class FullPool(HeldPool):
    """A pool whose queue is always full, counting the units it refused."""

    def __init__(self) -> None:
        """Start with nothing refused."""
        super().__init__()
        self.refused = 0

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.refused += 1
        raise BoundaryShedError


class EagerPool(IdlePool):
    """An idle pool that starts each unit on a thread, the first before its submit returns.

    The first submission waits until its unit is done or `QUIET` passed.
    """

    def __init__(self) -> None:
        """Start no thread yet."""
        super().__init__(0)
        self.threads: list[threading.Thread] = []
        self.held: list[bool] = []

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        thread = threading.Thread(target=task.run)
        self.threads.append(thread)
        thread.start()
        if len(self.threads) == 1:
            self.held.append(not wait((task.future,), timeout=QUIET).done)
        return task.future

    def join(self) -> None:
        for thread in self.threads:
            thread.join(WAIT)


class IdleFullPool(FullPool):
    """A pool reporting free workers whose queue still refuses every unit."""

    def capacity(self) -> Capacity:
        return Capacity(4, 0, 0, 0.0)


class StoppingIdlePool(IdlePool):
    """An idle pool that refuses its first unit as stopped and queues the rest."""

    def __init__(self, units: int) -> None:
        """Refuse the first submission, then expect `units` more."""
        super().__init__(units)
        self.stopped = False

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        if not self.stopped:
            self.stopped = True
            raise ExecutorStoppedError
        return super().submit(task)


@contextlib.contextmanager
def queue_timeout(seconds: float) -> Iterator[None]:
    options = {"EXECUTOR": {"OPTIONS": {"QUEUE_TIMEOUT": seconds}}}
    with override_settings(NEXT_FRAMEWORK={"DEFER": options}):
        yield


class RejectingPool(HeldPool):
    """A pool that rejects every unit it is handed."""

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        task.reject(ExecutorStoppedError())
        return task.future


class StoppedPool(HeldPool):
    """A pool that has stopped and refuses every unit."""

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        raise ExecutorStoppedError


class LingeringPermits(Foreground):
    """Permits whose wait outlasts the deadline it is given."""

    async def wait(self, until: float) -> bool:
        return await super().wait(until + WAIT)


class ExpiringPermits(Foreground):
    """Permits whose wait gives up at once, as if its deadline had passed."""

    async def wait(self, until: float) -> bool:
        return await super().wait(time.monotonic())


class RefusingPermits(Foreground):
    """Permits counting the takes they refuse."""

    def __init__(self, limit: int) -> None:
        """Hold `limit` permits and count the takes refused."""
        super().__init__(limit)
        self.refused = 0

    def take(self) -> bool:
        taken = super().take()
        self.refused += not taken
        return taken


class AwaitedPermits(Foreground):
    """Permits noting that a caller waits for one, before it queues up."""

    def __init__(self, limit: int) -> None:
        """Hold `limit` permits and a flag the first waiter sets."""
        super().__init__(limit)
        self.waiting = threading.Event()

    async def wait(self, until: float) -> bool:
        self.waiting.set()
        return await super().wait(until)


def coordinator_over(
    executor: BoundaryExecutor,
    *,
    admission: Admitter | None = None,
    permits: Foreground | None = None,
) -> Coordinator[str]:
    return Coordinator(
        executor=executor,
        admission=admission or ScriptedAdmission(),
        permits=permits or Foreground(4),
    )


async def loop_turns() -> None:
    """Let ready tasks and one thread hop run, without waiting on any clock."""
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.to_thread(lambda: None)
    for _ in range(20):
        await asyncio.sleep(0)


async def left_tasks() -> list[asyncio.Task[Any]]:
    """Return the other tasks of the running loop that do not end within `WAIT`."""
    current = asyncio.current_task()
    others = {task for task in asyncio.all_tasks() if task is not current}
    if not others:
        return []
    _, pending = await asyncio.wait(others, timeout=WAIT)
    return list(pending)


def loop_tasks() -> list[asyncio.Task[Any]]:
    """Return the tasks of the background loop that do not end within `WAIT`."""
    loop = background_loop.current()
    if loop is None:
        return []
    return asyncio.run_coroutine_threadsafe(left_tasks(), loop).result(2 * WAIT)


def view_session(
    facts: ForegroundFacts, *bodies: Body | AsyncBody, deadline: float = 5.0
) -> tuple[BoundarySession[str], int]:
    session = new_session(facts=facts, deadline=deadline)
    for body in bodies:
        if inspect.iscoroutinefunction(body):
            queue_job(session, abody=body)
        else:
            queue_job(session, body)
    return session, threading.get_ident()


async def consume_stream(
    coordinator: Coordinator[str], facts: ForegroundFacts, *bodies: Body | AsyncBody
) -> tuple[list[Emit[str]], int]:
    async with ThreadSensitiveContext():
        session, view = await sync_to_async(view_session, thread_sensitive=True)(
            facts, *bodies
        )
        batches = [batch async for batch in coordinator.arun(session)]
    return batches, view
