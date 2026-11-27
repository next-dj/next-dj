from collections.abc import Callable, Generator
from contextlib import contextmanager
from unittest import mock

import pytest
from django.http import StreamingHttpResponse
from django.test.client import RequestFactory

from next.defer import policy
from next.defer.assembly import jobs as assembly_jobs
from next.defer.policy import StreamSlots
from next.testing import StreamedPage
from next.testing.boundaries import ComponentGate


type Opens = Callable[[list[bytes]], bool]

_base_environ = RequestFactory._base_environ


def _threaded(factory: RequestFactory, **request: object) -> dict[str, object]:
    return _base_environ(factory, **{"wsgi.multithread": True, **request})


@contextmanager
def streaming() -> Generator[None]:
    """Build every test request inside the block as a threaded WSGI worker serves it.

    The test client reads as a single-threaded worker, which never streams a page.
    """
    with mock.patch.object(RequestFactory, "_base_environ", _threaded):
        yield


def isolated_slots(monkeypatch: pytest.MonkeyPatch) -> StreamSlots:
    """Count the streams of one test alone, apart from any another test left open."""
    fresh = StreamSlots()
    monkeypatch.setattr(policy, "open_streams", fresh)
    monkeypatch.setattr(assembly_jobs, "open_streams", fresh)
    return fresh


def read_releasing(
    response: StreamingHttpResponse, gate: ComponentGate, opens: Opens
) -> StreamedPage:
    """Read a sync stream, releasing `gate` once `opens` holds for the writes so far."""
    writes: list[bytes] = []
    try:
        for write in response:
            writes.append(write)
            if opens(writes):
                gate.release()
    finally:
        response.close()
    return StreamedPage(writes, response)


async def aread_releasing(
    response: StreamingHttpResponse, gate: ComponentGate, opens: Opens
) -> StreamedPage:
    """Read an async stream, releasing `gate` once `opens` holds for the writes read."""
    writes: list[bytes] = []
    async for write in response:
        writes.append(write)
        if opens(writes):
            gate.release()
    return StreamedPage(writes, response)


def after_opening(writes: list[bytes]) -> bool:
    """Whether a write followed the opening one, which only the ungated job sends."""
    return len(writes) > 1


def at_heartbeat(writes: list[bytes]) -> bool:
    """Whether the latest write is a heartbeat comment."""
    return writes[-1].startswith(b": ping\n\n")
