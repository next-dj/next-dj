import multiprocessing
import queue
import time
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from multiprocessing.context import SpawnContext, SpawnProcess
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event

import redis

from tests.load.clients import ClientJob, ClientSpec, run_clients
from tests.load.profiles import Profile
from tests.load.publisher import PublisherJob, PublisherReport, run_publisher
from tests.load.ready import Ready
from tests.load.sampler import ResourceSampler
from tests.load.server import serve
from tests.load.stats import ProcessReport, TransportTotals, combine


SETUP_TIMEOUT = 30.0
DRAIN_TIMEOUT = 10.0
JOIN_TIMEOUT = 10.0
EXIT_GRACE = 2.0
SLICE = 0.5


class HarnessError(RuntimeError):
    """A harness process failed or missed its deadline, so the run has no result."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything one run measured, before it is shaped into the report."""

    transports: dict[str, TransportTotals]
    publishers: tuple[PublisherReport, ...]
    server: dict[str, float | int | None]
    open_seconds: float
    publish_seconds: float
    drain_seconds: float

    @property
    def published(self) -> int:
        """Return how many messages the broker accepted across all publishers."""
        return sum(sum(item.published.values()) for item in self.publishers)

    @property
    def publish_errors(self) -> int:
        """Return how many publishes failed across all publishers."""
        return sum(sum(item.errors.values()) for item in self.publishers)


@dataclass(frozen=True, slots=True)
class Measured:
    """What publishing and draining produced, before the server figures join."""

    publishers: tuple[PublisherReport, ...]
    reports: list[ProcessReport]
    publish_seconds: float
    drain_seconds: float


@dataclass(frozen=True, slots=True)
class Channels:
    """The queues and events the parent shares with every worker."""

    ready: Queue[Ready]
    published: Queue[PublisherReport]
    received: Queue[ProcessReport]
    start: Event
    abort: Event
    failures: Queue[tuple[str, str]]


def supervise(
    target: Callable[..., None], failures: Queue[tuple[str, str]], *args: object
) -> None:
    """Run a worker target and hand its traceback to the parent before it dies."""
    try:
        target(*args)
    except Exception:
        failures.put((multiprocessing.current_process().name, traceback.format_exc()))
        raise


def _failure(process: SpawnProcess, failures: Queue[tuple[str, str]]) -> str:
    """Describe a failed worker, with the traceback it sent when it sent one."""
    msg = f"{process.name} exited with code {process.exitcode}."
    deadline = time.monotonic() + SLICE
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            name, trace = failures.get(timeout=remaining)
        except queue.Empty:
            break
        if name == process.name:
            return f"{msg}\n{trace}"
    return msg


def _get[T](
    source: Queue[T],
    timeout: float,
    missing: str,
    watched: Sequence[SpawnProcess],
    failures: Queue[tuple[str, str]],
) -> T:
    """Take one item off a queue in short slices, failing as soon as a worker dies.

    A clean exit is how a worker ends after its report, so only a failure counts.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HarnessError(missing)
        try:
            return source.get(timeout=min(SLICE, remaining))
        except queue.Empty:
            for process in watched:
                if process.exitcode is not None and process.exitcode != 0:
                    raise HarnessError(_failure(process, failures)) from None


def client_jobs(profile: Profile, port: int) -> tuple[ClientJob, ...]:
    """Spread both transports over the client processes, topics round-robin."""
    names = profile.topic_names
    specs = [
        ClientSpec("sse", names[index % len(names)])
        for index in range(profile.sse_clients)
    ] + [
        ClientSpec("ws", names[index % len(names)])
        for index in range(profile.ws_clients)
    ]
    count = profile.client_processes
    return tuple(
        ClientJob(
            index=index,
            port=port,
            clients=tuple(specs[index::count]),
            connect_concurrency=max(profile.connect_concurrency // count, 1),
            ready_timeout=profile.ready_timeout,
            totals_timeout=profile.ready_timeout + profile.duration + SETUP_TIMEOUT,
            drain_timeout=DRAIN_TIMEOUT,
        )
        for index in range(count)
    )


def publisher_jobs(profile: Profile) -> tuple[PublisherJob, ...]:
    """Give every publisher the full topic set at the profile's rate."""
    return tuple(
        PublisherJob(
            index=index,
            topics=profile.topic_names,
            rate=profile.rate,
            messages=profile.messages,
            start_timeout=profile.ready_timeout + 2 * SETUP_TIMEOUT,
        )
        for index in range(profile.publishers)
    )


def _stop(processes: Sequence[SpawnProcess]) -> None:
    """Give started processes one shared grace, then terminate and kill the rest."""
    started = [process for process in processes if process.pid is not None]
    deadline = time.monotonic() + EXIT_GRACE
    for process in started:
        process.join(max(deadline - time.monotonic(), 0))
    stragglers = [process for process in started if process.is_alive()]
    for process in stragglers:
        process.terminate()
    for process in stragglers:
        process.join(JOIN_TIMEOUT)
        if process.is_alive():
            process.kill()
            process.join()


def _await_ready(
    profile: Profile,
    channels: Channels,
    workers: Sequence[SpawnProcess],
    watched: Sequence[SpawnProcess],
) -> None:
    """Wait for every worker, then refuse to publish unless every client got hello.

    The server reports no readiness, so it is watched for failure but never awaited.
    """
    hello = 0
    silent = [worker.name for worker in workers]
    deadline = time.monotonic() + profile.ready_timeout + SETUP_TIMEOUT
    while silent:
        ready = _get(
            channels.ready,
            deadline - time.monotonic(),
            f"No readiness within the deadline from {', '.join(silent)}.",
            watched,
            channels.failures,
        )
        silent.remove(ready.worker)
        if ready.kind == "clients":
            hello += ready.hello
    clients = profile.sse_clients + profile.ws_clients
    if hello != clients:
        msg = f"Only {hello} of {clients} clients got hello, so nothing was published."
        raise HarnessError(msg)


def _measure(
    profile: Profile,
    channels: Channels,
    controls: Sequence[Queue[dict[str, int]]],
    workers: Sequence[SpawnProcess],
) -> Measured:
    """Publish, hand the totals to the clients, and collect every report."""
    begun = time.monotonic()
    channels.start.set()
    publishers = tuple(
        _get(
            channels.published,
            profile.duration + SETUP_TIMEOUT,
            "A publisher never reported its counts.",
            workers,
            channels.failures,
        )
        for _ in range(profile.publishers)
    )
    finished = time.monotonic()
    totals: Counter[str] = Counter()
    for report in publishers:
        totals.update(report.published)
    for control in controls:
        control.put(dict(totals))
    reports = [
        _get(
            channels.received,
            DRAIN_TIMEOUT + SETUP_TIMEOUT,
            "A client process never reported its totals.",
            workers,
            channels.failures,
        )
        for _ in controls
    ]
    return Measured(publishers, reports, finished - begun, time.monotonic() - finished)


def _workers(
    context: SpawnContext,
    profile: Profile,
    jobs: Sequence[ClientJob],
    controls: Sequence[Queue[dict[str, int]]],
    channels: Channels,
) -> list[SpawnProcess]:
    """Build the client and publisher processes without starting them."""
    workers = [
        context.Process(
            target=supervise,
            args=(
                run_clients,
                channels.failures,
                job,
                channels.ready,
                control,
                channels.received,
            ),
            name=f"load-clients-{job.index}",
            daemon=True,
        )
        for job, control in zip(jobs, controls, strict=True)
    ]
    workers.extend(
        context.Process(
            target=supervise,
            args=(
                run_publisher,
                channels.failures,
                job,
                channels.ready,
                channels.start,
                channels.abort,
                channels.published,
            ),
            name=f"load-publisher-{job.index}",
            daemon=True,
        )
        for job in publisher_jobs(profile)
    )
    return workers


def run(profile: Profile) -> Outcome:
    """Run server, publishers and clients in spawned processes and collect totals.

    Publishing starts only once every client reported its hello.
    """
    context = multiprocessing.get_context("spawn")
    ports: Queue[int] = context.Queue()
    channels = Channels(
        ready=context.Queue(),
        published=context.Queue(),
        received=context.Queue(),
        start=context.Event(),
        abort=context.Event(),
        failures=context.Queue(),
    )
    server = context.Process(
        target=supervise,
        args=(serve, channels.failures, ports),
        name="load-server",
        daemon=True,
    )
    workers: list[SpawnProcess] = []
    server.start()
    try:
        if server.pid is None:
            msg = "The server process has no pid."
            raise HarnessError(msg)
        port = _get(
            ports,
            SETUP_TIMEOUT,
            "The server never reported its port.",
            [server],
            channels.failures,
        )
        try:
            with ResourceSampler(server.pid) as sampler:
                jobs = client_jobs(profile, port)
                controls: list[Queue[dict[str, int]]] = [context.Queue() for _ in jobs]
                workers = _workers(context, profile, jobs, controls, channels)
                opened = time.monotonic()
                for worker in workers:
                    worker.start()
                _await_ready(profile, channels, workers, [server, *workers])
                open_seconds = time.monotonic() - opened
                measured = _measure(profile, channels, controls, [server, *workers])
        finally:
            channels.abort.set()
            _stop(workers)
    finally:
        server.terminate()
        _stop([server])
    return Outcome(
        transports=combine(measured.reports),
        publishers=measured.publishers,
        server=sampler.summary(),
        open_seconds=open_seconds,
        publish_seconds=measured.publish_seconds,
        drain_seconds=measured.drain_seconds,
    )


def purge(url: str, prefix: str) -> int:
    """Delete the run's sequence keys so a shared Redis keeps no harness state."""
    with redis.Redis.from_url(url) as client:
        keys = list(client.scan_iter(match=f"{prefix}:*", count=1000))
        return int(client.delete(*keys)) if keys else 0
