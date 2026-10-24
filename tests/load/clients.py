import asyncio
import json
import queue
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext, suppress
from dataclasses import dataclass
from http import HTTPStatus
from multiprocessing.queues import Queue
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.typing import Origin

from next.partial import keys
from tests.load.frames import (
    PATCH_EVENT,
    SseEvent,
    SseParser,
    delivery,
    hello_positions,
    patch_payload,
)
from tests.load.limits import raise_open_files
from tests.load.profiles import TOPIC_PREFIX
from tests.load.ready import Ready
from tests.load.stats import ClientRecord, ProcessReport, TransportTotals


type Connection = Callable[[str, Session, httpx.AsyncClient], Awaitable[None]]

PAYLOAD_ERRORS = (ValueError, KeyError, TypeError)
PONG = json.dumps({"type": "pong"})
LINGER = 0.5


@dataclass(frozen=True, slots=True)
class ClientSpec:
    """One subscriber, its transport and the stream route name it opens."""

    transport: str
    name: str


@dataclass(frozen=True, slots=True)
class ClientJob:
    """The subscribers one client process runs, its connect gate and its timeouts."""

    index: int
    port: int
    clients: tuple[ClientSpec, ...]
    ready_timeout: float
    totals_timeout: float
    drain_timeout: float
    connect_concurrency: int
    connect_timeout: float = 30.0
    read_timeout: float = 60.0
    max_reconnects: int = 3
    reconnect_delay: float = 0.5

    @property
    def base_url(self) -> str:
        """Return the server origin every client talks to."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        """Return the WebSocket form of the server origin."""
        return "ws" + self.base_url.removeprefix("http")


class Progress:
    """Count settled and complete clients so the process waits on events, not polls.

    A client settles on its first hello or when it gives up reconnecting.
    """

    def __init__(self, total: int) -> None:
        """Start with no client settled and no expected totals known yet."""
        self._total = total
        self._settled = 0
        self._complete = 0
        self._expected: Mapping[str, int] | None = None
        self.all_settled = asyncio.Event()
        self.all_complete = asyncio.Event()
        if total == 0:
            self.all_settled.set()
            self.all_complete.set()

    def settle(self, record: ClientRecord) -> None:
        """Mark a client settled once."""
        if record.settled:
            return
        record.settled = True
        self._settled += 1
        if self._settled >= self._total:
            self.all_settled.set()

    def check(self, record: ClientRecord) -> None:
        """Mark a client complete once it accounted for its topic's published total."""
        if self._expected is None or record.complete:
            return
        accounted = record.delivered + record.skipped
        if record.gave_up or accounted >= self._expected.get(record.topic, 0):
            record.complete = True
            self._complete += 1
            if self._complete >= self._total:
                self.all_complete.set()

    def arm(self, expected: Mapping[str, int], records: Sequence[ClientRecord]) -> None:
        """Learn the published totals and recheck every client against them."""
        self._expected = expected
        for record in records:
            self.check(record)


@dataclass(frozen=True, slots=True)
class Session:
    """What a running client shares with its frame handlers."""

    record: ClientRecord
    totals: TransportTotals
    progress: Progress
    job: ClientJob
    gate: asyncio.Semaphore
    tls: ssl.SSLContext


def run_clients(
    job: ClientJob,
    ready: Queue[Ready],
    control: Queue[dict[str, int]],
    reports: Queue[ProcessReport],
) -> None:
    """Run this process's subscribers on one event loop and report their totals."""
    raise_open_files()
    reports.put(asyncio.run(_run(job, ready, control)))


def _spawn(
    job: ClientJob,
    records: Sequence[ClientRecord],
    report: ProcessReport,
    progress: Progress,
    http: httpx.AsyncClient,
) -> list[asyncio.Task[None]]:
    """Start one task per subscriber, all sharing the connect gate and TLS context."""
    gate = asyncio.Semaphore(job.connect_concurrency)
    tls = ssl.create_default_context()
    return [
        asyncio.create_task(
            _client(
                spec,
                Session(
                    record=record,
                    totals=report.transports[spec.transport],
                    progress=progress,
                    job=job,
                    gate=gate,
                    tls=tls,
                ),
                http,
            )
        )
        for spec, record in zip(job.clients, records, strict=True)
    ]


def _fold(
    records: Sequence[ClientRecord],
    outcomes: Sequence[object],
    expected: Mapping[str, int],
    report: ProcessReport,
) -> None:
    """Fold every finished client into its transport, task crashes counted as errors."""
    for record, outcome in zip(records, outcomes, strict=True):
        if isinstance(outcome, BaseException) and not isinstance(
            outcome, asyncio.CancelledError
        ):
            record.errors[f"task_{type(outcome).__name__}"] += 1
        report.transports[record.transport].add(record, expected.get(record.topic, 0))


async def _run(
    job: ClientJob, ready: Queue[Ready], control: Queue[dict[str, int]]
) -> ProcessReport:
    """Connect, signal readiness, then drain until every published message arrived."""
    report = ProcessReport()
    records = [
        ClientRecord(spec.transport, f"{TOPIC_PREFIX}{spec.name}")
        for spec in job.clients
    ]
    progress = Progress(len(records))
    expected: Mapping[str, int] = {}
    async with httpx.AsyncClient(
        base_url=job.base_url,
        verify=ssl.create_default_context(),
        limits=httpx.Limits(max_connections=None, max_keepalive_connections=None),
        timeout=httpx.Timeout(job.connect_timeout, read=job.read_timeout),
    ) as http:
        tasks = _spawn(job, records, report, progress, http)
        try:
            await _within(progress.all_settled, job.ready_timeout)
            hello = sum(record.hello_seconds is not None for record in records)
            ready.put(Ready("clients", hello))
            with suppress(queue.Empty):
                expected = await asyncio.to_thread(
                    control.get, True, job.totals_timeout
                )
                progress.arm(expected, records)
                await _within(progress.all_complete, job.drain_timeout)
                await asyncio.sleep(LINGER)
        finally:
            for task in tasks:
                task.cancel()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    _fold(records, outcomes, expected, report)
    return report


async def _within(event: asyncio.Event, seconds: float) -> None:
    """Wait for an event, giving up quietly once the seconds pass."""
    with suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), seconds)


async def _client(spec: ClientSpec, session: Session, http: httpx.AsyncClient) -> None:
    """Keep one subscriber connected until it is cancelled or gives up.

    A WS client negotiates through its own cookie jar, so each one holds a session.
    """
    path = f"/stream/{spec.name}/"
    connection: Connection
    scope: AbstractAsyncContextManager[httpx.AsyncClient]
    if spec.transport == "ws":
        connection = _ws_connection
        scope = httpx.AsyncClient(
            base_url=session.job.base_url,
            verify=session.tls,
            timeout=session.job.connect_timeout,
        )
    else:
        connection = _sse_connection
        scope = nullcontext(http)
    async with scope as client:
        while True:
            await connection(path, session, client)
            if not await _retry(session):
                return


async def _retry(session: Session) -> bool:
    """Count a reconnect after a short pause, or give up past the allowance."""
    record = session.record
    if record.reconnects >= session.job.max_reconnects:
        record.gave_up = True
        session.progress.settle(record)
        session.progress.check(record)
        return False
    record.reconnects += 1
    await asyncio.sleep(session.job.reconnect_delay)
    return True


async def _sse_connection(path: str, session: Session, http: httpx.AsyncClient) -> None:
    """Hold one SSE response open and feed its lines through the event parser."""
    record = session.record
    request = http.build_request("GET", path, headers={"Accept": "text/event-stream"})
    try:
        async with session.gate:
            record.opened = time.monotonic()
            response = await http.send(request, stream=True)
        try:
            if response.status_code != HTTPStatus.OK:
                record.errors[f"sse_status_{response.status_code}"] += 1
                return
            parser = SseParser()
            async for line in response.aiter_lines():
                received = time.time()
                event = parser.feed(line)
                if event is not None:
                    _on_sse_event(event, received, session)
            record.errors["sse_eof"] += 1
        finally:
            await response.aclose()
    except httpx.HTTPError as exc:
        record.errors[f"sse_{type(exc).__name__}"] += 1


def _on_sse_event(event: SseEvent, received: float, session: Session) -> None:
    """Apply one SSE event to the client's record."""
    record = session.record
    try:
        if event.name == keys.SSE_HELLO:
            _on_hello(json.loads(event.data), session)
        elif event.name == PATCH_EVENT:
            _on_patch(json.loads(event.data), received, session)
        elif event.name == keys.SSE_SKIP:
            _on_skip(json.loads(event.data), session)
        elif event.name == keys.SSE_END:
            record.errors[f"sse_end_{json.loads(event.data).get('code')}"] += 1
        else:
            record.errors["sse_unknown_event"] += 1
    except PAYLOAD_ERRORS:
        record.errors["sse_payload"] += 1


async def _ws_connection(
    path: str, session: Session, negotiation: httpx.AsyncClient
) -> None:
    """Negotiate a ticket, open the socket with the session cookie and subscribe."""
    record = session.record
    job = session.job
    try:
        async with session.gate:
            record.opened = time.monotonic()
            response = await negotiation.get(path, headers={"X-Next-Transport": "ws"})
            if response.status_code != HTTPStatus.OK:
                record.errors[f"ws_negotiate_{response.status_code}"] += 1
                return
            try:
                grant = response.json()
                url, ticket = grant["url"], grant["ticket"]
            except PAYLOAD_ERRORS:
                record.errors["ws_negotiate_payload"] += 1
                return
            cookie = "; ".join(
                f"{name}={value}" for name, value in negotiation.cookies.items()
            )
            socket = await connect(
                f"{job.ws_url}{url}",
                origin=Origin(job.base_url),
                additional_headers={"Cookie": cookie},
                open_timeout=job.connect_timeout,
            )
        async with socket:
            await socket.send(
                json.dumps({"type": "sub", "ticket": ticket, "last_seq": None})
            )
            async for raw in socket:
                await _on_ws_frame(socket, raw, time.time(), session)
            record.errors["ws_closed"] += 1
    except ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd is not None else "none"
        record.errors[f"ws_close_{code}"] += 1
    except (httpx.HTTPError, WebSocketException, OSError) as exc:
        record.errors[f"ws_{type(exc).__name__}"] += 1


async def _on_ws_frame(
    socket: ClientConnection, raw: str | bytes, received: float, session: Session
) -> None:
    """Apply one WebSocket control or patch frame to the client's record."""
    record = session.record
    try:
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "hello":
            _on_hello(message, session)
        elif kind == "patches":
            _on_patch(patch_payload(message["data"]), received, session)
        elif kind == "ping":
            await socket.send(PONG)
        elif kind == "skip":
            _on_skip(message, session)
        elif kind == "error":
            record.errors[f"ws_error_{message.get('code')}"] += 1
        else:
            record.errors["ws_unknown_frame"] += 1
    except PAYLOAD_ERRORS:
        record.errors["ws_payload"] += 1


def _on_hello(frame: Mapping[str, Any], session: Session) -> None:
    """Record the hello position of the client's topic and settle it."""
    record = session.record
    record.on_hello(hello_positions(frame)[record.topic], time.monotonic())
    session.progress.settle(record)


def _on_patch(frame: Mapping[str, Any], received: float, session: Session) -> None:
    """Count a new position and sample both latency measures for it."""
    record = session.record
    item = delivery(frame)
    if item.topic != record.topic:
        record.errors["foreign_topic"] += 1
        return
    if record.advance(item.epoch, item.seq):
        record.delivered += 1
        session.totals.end_to_end.append(received - item.sent)
        session.totals.server_to_client.append(received - item.server_time)
        session.progress.check(record)


def _on_skip(frame: Mapping[str, Any], session: Session) -> None:
    """Advance past a message the server filtered for this client."""
    record = session.record
    if record.advance(str(frame[keys.EPOCH]), int(frame[keys.SEQ])):
        record.skipped += 1
        session.progress.check(record)
