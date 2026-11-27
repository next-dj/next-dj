from collections.abc import Callable, Iterator
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections
from django.http import HttpRequest
from django.test import AsyncRequestFactory, RequestFactory

from next.bridge.hops import check_asgiref, verdict
from next.caches import SETTINGS
from next.defer.connections import detection
from next.defer.ledger import ResponseJobs
from next.defer.render import Frame, _frame


@pytest.fixture(autouse=True)
def _unchecked_asgiref() -> Iterator[None]:
    """Forget the cached asgiref verdict, so each test judges the asgiref it sees."""
    verdict.cache_clear()
    check_asgiref.cache_clear()
    yield
    verdict.cache_clear()
    check_asgiref.cache_clear()


@pytest.fixture(autouse=True)
def _closed_as_the_test_client_closes() -> Iterator[None]:
    """Close a response a test closes by hand as Django's test client closes one.

    The in-memory test database stays connected, which a test without access cannot read.
    """
    request_finished.disconnect(close_old_connections)
    try:
        yield
    finally:
        request_finished.connect(close_old_connections)


@pytest.fixture()
def deployment() -> Iterator[Callable[..., None]]:
    """Return a setter that fakes the apps, middleware and routers the detection reads.

    Each fake moves `SETTINGS` as a real settings change would, and so does its undoing.
    """
    with ExitStack() as faked:

        def declare(
            *,
            apps: tuple[object, ...] = (),
            middleware: tuple[object, ...] = (),
            routers: tuple[object, ...] = (),
        ) -> None:
            fake = SimpleNamespace(
                INSTALLED_APPS=list(apps),
                MIDDLEWARE=list(middleware),
                DATABASE_ROUTERS=list(routers),
            )
            faked.enter_context(patch.object(detection, "settings", fake))
            SETTINGS.move()

        yield declare
    SETTINGS.move()


@pytest.fixture()
def wsgi(rf: RequestFactory) -> Callable[..., HttpRequest]:
    """Build a WSGI request, claiming threads unless `threaded` is None."""

    def build(*, threaded: bool | None = True, **extra: object) -> HttpRequest:
        claim = {} if threaded is None else {"wsgi.multithread": threaded}
        return rf.get("/", **claim, **extra)

    return build


@pytest.fixture()
def asgi(async_rf: AsyncRequestFactory) -> Callable[..., HttpRequest]:
    """Build an ASGI request whose scope carries the given keys."""

    def build(**scope: object) -> HttpRequest:
        request = async_rf.get("/")
        request.scope.update(scope)
        return request

    return build


@pytest.fixture()
def in_job() -> Iterator[Callable[..., Frame]]:
    """Return a function that enters a job's frame, left again when the test ends."""
    tokens = []

    def enter(session: ResponseJobs, ident: str = "b-x-0", depth: int = 0) -> Frame:
        frame = Frame(session, ident, depth)
        tokens.append(_frame.set(frame))
        return frame

    yield enter
    for token in reversed(tokens):
        _frame.reset(token)
