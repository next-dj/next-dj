from collections.abc import Callable

import pytest
from django.http import StreamingHttpResponse

from next.suspense import assembly, policy
from next.suspense.policy import StreamSlots
from next.suspense.render import Gate
from next.testing import StreamedPage


type Opens = Callable[[list[bytes]], bool]


def isolated_slots(monkeypatch: pytest.MonkeyPatch) -> StreamSlots:
    """Count the streams of one test alone, apart from any another test left open."""
    fresh = StreamSlots()
    monkeypatch.setattr(policy, "open_streams", fresh)
    monkeypatch.setattr(assembly, "open_streams", fresh)
    return fresh


def read_releasing(
    response: StreamingHttpResponse, gate: Gate, opens: Opens
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
    response: StreamingHttpResponse, gate: Gate, opens: Opens
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
