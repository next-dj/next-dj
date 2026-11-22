from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import RequestFactory

from next.components.info import ComponentInfo
from next.deps.objects import SCOPE_DEPENDENCY
from next.partial import Envelope, Patches
from next.partial.headers import (
    LAYOUTS,
    NAVIGATE,
    ORIGIN,
    PREFETCH,
    REQUEST_FLAG,
    SEC_FETCH_SITE,
    STREAM_ACCEPT,
)
from next.realtime import Message
from tests.support import bound_dependency


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator
    from pathlib import Path

    from django.http import HttpRequest

    from next.realtime import BrokerBackend, MemoryBroker, RedisBroker


_DEFAULT_LEAF_BODY = "def render(request):\n    return 'x'\n"
_CSRF_SECRET = "s" * 32

REQUEST_ROUNDS = 2000
REQUEST_WARMUP_ROUNDS = 200


def build_pages_tree(
    root: Path,
    depth: int,
    fanout: int,
    *,
    leaf: str = "page.py",
    leaf_body: str = _DEFAULT_LEAF_BODY,
) -> None:
    """Materialise a synthetic page tree of ``depth`` * ``fanout`` under ``root``."""
    if depth == 0:
        (root / leaf).write_text(leaf_body)
        return
    for i in range(fanout):
        child = root / f"n_{i}"
        child.mkdir()
        build_pages_tree(child, depth - 1, fanout, leaf=leaf, leaf_body=leaf_body)


def build_component_djx_dir(root: Path, count: int) -> None:
    """Write ``count`` simple ``.djx`` components into ``root`` (flat)."""
    for i in range(count):
        (root / f"comp_{i}.djx").write_text(f"<div>c{i}</div>")


def noop_form_handler(**kwargs) -> None:  # pragma: no cover - bench stub
    """Stub action handler for form-action benchmarks."""


def noop_signal_receiver(sender: object, **kwargs) -> None:  # pragma: no cover
    """No-op Django signal receiver for signal-overhead benchmarks."""
    del sender


def build_component_info(root: Path, name: str = "c") -> ComponentInfo:
    """Create one ``ComponentInfo`` without writing to the filesystem."""
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=root / f"{name}.djx",
        module_path=None,
        is_simple=True,
    )


def build_component_info_list(root: Path, count: int) -> list[ComponentInfo]:
    """Create ``count`` ``ComponentInfo`` objects without writing to the filesystem."""
    return [build_component_info(root, f"c_{i}") for i in range(count)]


def build_layout_page(
    root: Path, *, layouts: int, template: str | None = None, page_body: str = "x = 1\n"
) -> Path:
    """Materialise a page nested under ``layouts`` ancestor ``layout.djx`` wrappers.

    The layouts sit strictly above the page directory, the shape the walk pays for.
    """
    directory = root
    for i in range(layouts):
        (directory / "layout.djx").write_text(
            f'<div class="l{i}">{{% template %}}</div>'
        )
        directory = directory / f"seg_{i}"
        directory.mkdir()
    (directory / "page.py").write_text(page_body)
    if template is not None:
        (directory / "template.djx").write_text(template)
    return directory / "page.py"


def build_component(
    root: Path,
    name: str,
    *,
    body: str,
    module: str | None = None,
    loading: str | None = None,
    error: str | None = None,
) -> None:
    """Write the component `name` under `root`, a `loading.djx` making it deferred."""
    folder = root / "_components" / name
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text(body)
    for file_name, text in (
        ("component.py", module),
        ("loading.djx", loading),
        ("error.djx", error),
    ):
        if text is not None:
            (folder / file_name).write_text(text)


def threaded_get(path: str) -> HttpRequest:
    """Return a GET as a threaded WSGI server hands it over, so its page may stream."""
    return RequestFactory().get(path, **{"wsgi.multithread": True})


def router_get(
    path: str,
    origin: str,
    layouts: str | None,
    *,
    prefetch: str | None = None,
    streamed: bool = False,
) -> HttpRequest:
    """Return the GET the client router sends for a link on the `origin` page.

    `layouts` is the declaration or None to send none, `prefetch` the depth of a press,
    and `streamed` asks as a stream-reading client does under a threaded server.
    """
    headers = {
        REQUEST_FLAG: "1",
        NAVIGATE: "1",
        ORIGIN: origin,
        SEC_FETCH_SITE: "same-origin",
    }
    if layouts is not None:
        headers[LAYOUTS] = layouts
    if prefetch is not None:
        headers[PREFETCH] = prefetch
    extra: dict[str, object] = {}
    if streamed:
        headers["Accept"] = STREAM_ACCEPT
        extra["wsgi.multithread"] = True
    return RequestFactory().get(path, headers=headers, **extra)


def each_round(
    build: Callable[[], HttpRequest],
    *rest: object,
    before: Callable[[], object] | None = None,
    **kwargs: object,
) -> Callable[[], tuple[tuple[object, ...], dict[str, object]]]:
    """Return a pedantic `setup` handing every round a request nothing has read yet.

    A reused request answers from what the round before held on it. Each one carries
    the CSRF secret of a returning visitor, where the CSRF middleware leaves it.
    """

    def setup() -> tuple[tuple[object, ...], dict[str, object]]:
        if before is not None:
            before()
        request = build()
        request.META["CSRF_COOKIE"] = _CSRF_SECRET
        return (request, *rest), kwargs

    return setup


class StandInRows:
    """Rows answering every lookup with one unsaved row, so no query is ever made."""

    def __init__(self, row: Group) -> None:
        """Answer every lookup with `row`."""
        self._row = row

    def visible_to(self, scope: object) -> Self:
        """Return the rows themselves, whatever the scope."""
        return self

    def filter(self, **lookups: object) -> Self:
        """Return the rows themselves, whatever the lookups."""
        return self

    def order_by(self) -> Self:
        """Return the rows themselves, unordered."""
        return self

    def __getitem__(self, window: slice) -> list[Group]:
        """Return the one row, whatever the window."""
        return [self._row]


STAND_IN_PK = 7
"""The primary key of the one row a stand-in `Group` manager answers with."""


@contextmanager
def stand_in_rows(pk: int = STAND_IN_PK) -> Iterator[None]:
    """Give the `Group` manager a `visible_to` that answers one unsaved row of `pk`."""
    rows = StandInRows(Group(pk=pk, name="bench"))
    with patch.object(
        Group._default_manager, "visible_to", rows.visible_to, create=True
    ):
        yield


@contextmanager
def stand_in_scope(pk: int = STAND_IN_PK) -> Iterator[None]:
    """Answer every `Group` lookup with one unsaved row of `pk` and bind the `scope`."""
    with stand_in_rows(pk), bound_dependency(SCOPE_DEPENDENCY, lambda: "tenant"):
        yield


def topic_names(count: int) -> list[str]:
    """Return ``count`` distinct valid realtime topic names."""
    return [f"room:{i}" for i in range(count)]


def small_envelope() -> Envelope:
    """Build a topic-safe envelope with a single event op."""
    return Patches(None).event("saved", {"id": 7}).envelope()


def medium_envelope() -> Envelope:
    """Build a topic-safe envelope mixing event, toast, refresh and remove ops."""
    return (
        Patches(None)
        .event("saved", {"id": 7, "title": "Draft"})
        .toast("Saved", variant="success")
        .refresh(zone="results")
        .remove({"zone": "banner"})
        .envelope()
    )


def topic_message(topic: str = "room:1", seq: int = 1) -> Message:
    """Build a numbered broker message carrying the small envelope."""
    return Message(topic, seq, small_envelope(), 1.5, "e1")


def published_topics(broker: BrokerBackend, count: int) -> list[str]:
    """Publish the small envelope once on ``count`` topics and return their names."""
    topics = topic_names(count)
    envelope = small_envelope()
    for topic in topics:
        broker.publish(topic, envelope)
    return topics


def quiet_presence[B: MemoryBroker | RedisBroker](broker: B) -> B:
    """Close the snapshot schedule so no background publish lands in a measurement."""
    broker.presence_schedule.close()
    return broker


def joined_connections(
    broker: MemoryBroker | RedisBroker, count: int, topic: str = "room:1"
) -> list[str]:
    """Join ``count`` identified connections for an hour and return their ids."""
    connections = [f"c{index}" for index in range(count)]
    for connection in connections:
        broker.join(topic, connection, {"id": connection}, 3600)
    return connections


def run_without_loop[T](coro: Coroutine[object, object, T]) -> T:
    """Drive a coroutine that never awaits real I/O to completion without a loop."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    msg = "The coroutine suspended instead of completing."
    raise RuntimeError(msg)


class SteppingClock:
    """A clock that advances by a fixed step on every reading."""

    def __init__(self, step: float) -> None:
        """Start at zero and move ``step`` seconds forward per call."""
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        """Return the advanced time."""
        self._now += self._step
        return self._now
