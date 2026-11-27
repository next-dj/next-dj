import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future, InvalidStateError
from contextlib import contextmanager, suppress
from typing import Any

from django.db import connection

from next.bridge.request import RequestThreadExecutor
from tests.support.waits import WAIT


@contextmanager
def expiring(future: Future[Any]) -> Generator[None]:
    def expire() -> None:
        with suppress(InvalidStateError):
            future.set_exception(TimeoutError("the wait never ended"))

    timer = threading.Timer(WAIT, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


def interrupt() -> None:
    raise KeyboardInterrupt


def fail() -> None:
    msg = "callback failed"
    raise ValueError(msg)


async def connect() -> None:
    connection.ensure_connection()


def submit_elsewhere(
    executor: RequestThreadExecutor, fn: Callable[..., object], *args: object
) -> Future[object]:
    hops: list[Future[object]] = []
    thread = threading.Thread(
        target=lambda: hops.append(executor.submit(fn, *args)), daemon=True
    )
    thread.start()
    thread.join(WAIT)
    assert not thread.is_alive()
    return hops[0]
