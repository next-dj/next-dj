import asyncio
import contextvars
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import StreamingHttpResponse
from django.test import Client

from next.boundaries import Boundary, boundary_failed, contain
from next.components import ComponentInfo
from next.defer import BoundaryError, ErrorKind
from next.suspense.connections import pool_forced
from next.testing import (
    NextClient,
    boundary_errors,
    boundary_gate,
    parallel_boundaries,
    plugin,
    render_page,
)
from next.testing.boundaries import (
    RecordedFailure,
    in_document_order,
    raise_first,
    recorded_failures,
)
from next.testing.rendering import render_component_by_name
from tests.pages.checks.metadata.trees import (
    COMPONENTS,
    component_template,
    layout_root,
    templated_page,
)
from tests.support import routed


DOWN = "widget down"


class _Fallback:
    def retry(self) -> bool:
        return True

    def retry_token(self, error: BoundaryError) -> str:
        return ""

    def tag(self) -> str | None:
        return "div"

    def render(self, error: BoundaryError, kind: ErrorKind) -> str:
        return "E"


def _fail(name: str, exc: Exception) -> str:
    def render() -> str:
        raise exc

    return contain(Boundary(name, "component"), render, _Fallback())


def _mode() -> str:
    try:
        raise_first([_failure()])
    except ValueError:
        return "raise"
    return "render"


def _failure(ident: str = "b-1-1", exc: Exception | None = None) -> RecordedFailure:
    return RecordedFailure(
        ident, "card", "component", ErrorKind.ERROR, "d", exc or ValueError()
    )


class TestBoundaryErrors:
    """Contained failures re-raise unless a test asks for the error UI."""

    def test_raise_is_the_default(self) -> None:
        assert _mode() == "raise"

    def test_render_opts_in_for_the_block(self) -> None:
        with boundary_errors("render"):
            assert _mode() == "render"
            with boundary_errors("raise"):
                assert _mode() == "raise"
            assert _mode() == "render"
        assert _mode() == "raise"

    def test_an_unknown_mode_is_refused(self) -> None:
        mode: object = "quiet"
        with pytest.raises(ValueError, match="'raise' or 'render'"):
            boundary_errors(mode).__enter__()

    def test_nothing_recorded_raises_nothing(self) -> None:
        raise_first([])

    def test_the_first_failure_is_raised(self) -> None:
        first, second = KeyError("first"), KeyError("second")
        with pytest.raises(KeyError) as caught:
            raise_first([_failure("b-1", first), _failure("b-2", second)])
        assert caught.value is first

    def test_the_fixture_renders_for_the_test(self) -> None:
        fixture = plugin.rendered_boundary_errors.__wrapped__()
        next(fixture)
        assert _mode() == "render"
        fixture.close()
        assert _mode() == "raise"


class TestRecordedFailures:
    """Every failure contained in the block on this context is recorded."""

    def test_records_in_render_order(self) -> None:
        with recorded_failures() as failures:
            _fail("a", ValueError("a"))
            _fail("b", KeyError("b"))
        assert [failure.name for failure in failures] == ["a", "b"]
        assert failures[0].kind is ErrorKind.ERROR
        assert failures[0].addressing == "component"
        assert str(failures[0].exception) == "a"
        assert failures[0].id.startswith("b-")
        assert len(failures[0].digest) == 12

    def test_a_failure_outside_any_block_is_recorded_nowhere(self) -> None:
        _fail("a", ValueError())
        with recorded_failures() as failures:
            pass
        assert failures == []

    def test_a_failure_on_another_thread_stays_out(self) -> None:
        with recorded_failures() as failures:
            thread = threading.Thread(target=_fail, args=("other", ValueError()))
            thread.start()
            thread.join()
        assert failures == []

    def test_a_job_in_a_copy_of_the_context_is_recorded(self) -> None:
        with recorded_failures() as failures:
            context = contextvars.copy_context()
            thread = threading.Thread(
                target=context.run, args=(_fail, "job", ValueError())
            )
            thread.start()
            thread.join()
        assert [failure.name for failure in failures] == ["job"]

    def test_an_enclosing_block_receives_the_inner_failures(self) -> None:
        with recorded_failures() as outer:
            _fail("a", ValueError())
            with recorded_failures() as inner:
                _fail("b", ValueError())
                assert [failure.name for failure in outer] == ["a"]
        assert [failure.name for failure in inner] == ["b"]
        assert [failure.name for failure in outer] == ["a", "b"]

    def test_the_receiver_is_connected_once(self) -> None:
        with recorded_failures():
            assert boundary_failed.has_listeners(contain) is True
        receivers = len(boundary_failed.receivers)
        with recorded_failures():
            pass
        assert len(boundary_failed.receivers) == receivers
        assert boundary_failed.has_listeners(contain) is True


class TestDocumentOrder:
    """Failures sort by where their boundaries stand in the finished document."""

    def test_the_document_decides_over_the_render(self) -> None:
        late, early = _failure("b-x-2"), _failure("b-x-1")
        document = '<i data-next-boundary="b-x-1"></i><i data-next-boundary="b-x-2">'
        assert in_document_order([late, early], document) == (early, late)

    def test_an_id_is_not_found_inside_a_longer_one(self) -> None:
        first, tenth = _failure("b-x-1"), _failure("b-x-10")
        document = b'"b-x-10" then "b-x-1"'
        assert in_document_order([first, tenth], document) == (tenth, first)

    def test_a_discarded_failure_follows_in_render_order(self) -> None:
        gone, kept, also_gone = _failure("b-x-1"), _failure("b-x-2"), _failure("b-x-3")
        ordered = in_document_order([gone, kept, also_gone], "<p>b-x-2</p>")
        assert ordered == (kept, gone, also_gone)


class TestRenderPage:
    """`render_page` re-raises what a boundary contained during the render."""

    def test_raises_by_default(self) -> None:
        with (
            patch(
                "next.testing.rendering.page.render",
                side_effect=lambda *_: _fail("card", ValueError(DOWN)),
            ),
            pytest.raises(ValueError, match=DOWN),
        ):
            render_page("/nowhere/page.py")

    def test_renders_when_asked(self) -> None:
        with (
            patch(
                "next.testing.rendering.page.render",
                side_effect=lambda *_: _fail("card", ValueError(DOWN)),
            ),
            boundary_errors("render"),
        ):
            assert render_page("/nowhere/page.py").endswith(">E</div>")

    def test_the_first_failure_in_the_document_is_raised(self) -> None:
        def body(*_: object) -> str:
            first = _fail("first", KeyError("first"))
            second = _fail("second", KeyError("second"))
            return second + first

        with (
            patch("next.testing.rendering.page.render", side_effect=body),
            pytest.raises(KeyError, match="second"),
        ):
            render_page("/nowhere/page.py")


class TestRenderComponentByName:
    """`render_component_by_name` re-raises what a nested boundary contained."""

    @pytest.fixture()
    def failing(self, tmp_path: Path) -> Iterator[None]:
        info = ComponentInfo("card", tmp_path, "", tmp_path / "card.djx", None, True)
        with (
            patch(
                "next.testing.rendering.components_manager.get_component",
                return_value=info,
            ),
            patch(
                "next.testing.rendering.render_component",
                side_effect=lambda *_a, **_k: _fail("inner", ValueError(DOWN)),
            ),
        ):
            yield

    @pytest.mark.usefixtures("failing")
    def test_raises_by_default(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=DOWN):
            render_component_by_name("card", at=tmp_path / "page.djx")

    @pytest.mark.usefixtures("failing")
    def test_renders_when_asked(self, tmp_path: Path) -> None:
        with boundary_errors("render"):
            html = render_component_by_name("card", at=tmp_path / "page.djx")
        assert html.endswith(">E</div>")


_FAILING_MODULE = """
from next.components import component


@component.context("value")
def value():
    raise RuntimeError("widget down")
"""


@pytest.fixture()
def widget_pages(tmp_path: Path) -> Iterator[Path]:
    pages = layout_root(tmp_path)
    component_template(pages, "widget", "<b>{{ value }}</b>")
    folder = pages / "_components" / "widget"
    (folder / "component.py").write_text(_FAILING_MODULE)
    (folder / "error.djx").write_text("<p>{{ error.kind }}</p>")
    templated_page(pages / "home", "x = 1\n", '[{% component "widget" %}]')
    templated_page(pages / "calm", "x = 1\n", "<p>calm</p>")
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        yield pages


@pytest.mark.django_db()
@pytest.mark.usefixtures("widget_pages")
class TestNextClient:
    """The client holds the failures of a response and re-raises the first."""

    def test_the_first_failure_is_raised_when_the_response_is_complete(self) -> None:
        with pytest.raises(RuntimeError, match=DOWN):
            NextClient().get("/home/")

    def test_a_rendering_test_reads_the_failures_on_the_response(self) -> None:
        with boundary_errors("render"):
            response = NextClient().get("/home/")
        assert response.status_code == 200
        assert "<p>error</p></div>]" in response.content.decode()
        (failure,) = response.boundary_failures
        assert failure.name == "widget"
        assert isinstance(failure.exception, RuntimeError)

    def test_a_calm_response_holds_no_failure(self) -> None:
        response = NextClient().get("/calm/")
        assert response.boundary_failures == ()


def _streamed(*names: str) -> Iterator[bytes]:
    for name in names:
        yield _fail(name, KeyError(name)).encode()


async def _astreamed(*names: str) -> AsyncIterator[bytes]:
    for name in names:
        yield _fail(name, KeyError(name)).encode()


class TestStreamedResponses:
    """A stream is complete once consumed, and its failures settle only then."""

    def test_the_failures_of_each_chunk_settle_at_the_end(self) -> None:
        response = StreamingHttpResponse(_streamed("a", "b"))
        with patch.object(Client, "request", return_value=response):
            got = NextClient().get("/stream/")
        chunks = iter(got.streaming_content)
        next(chunks)
        assert "boundary_failures" not in vars(got)
        with boundary_errors("render"):
            rest = list(chunks)
        assert len(rest) == 1
        assert [failure.name for failure in got.boundary_failures] == ["a", "b"]

    def test_the_first_failure_is_raised_once_consumed(self) -> None:
        response = StreamingHttpResponse(_streamed("a"))
        with patch.object(Client, "request", return_value=response):
            got = NextClient().get("/stream/")
        with pytest.raises(KeyError, match="a"):
            b"".join(got.streaming_content)

    def test_an_async_stream_settles_at_its_end(self) -> None:
        response = StreamingHttpResponse(_astreamed("a", "b"))
        with patch.object(Client, "request", return_value=response):
            got = NextClient().get("/stream/")

        async def consume() -> list[bytes]:
            return [chunk async for chunk in got.streaming_content]

        with boundary_errors("render"):
            chunks = asyncio.run(consume())
        assert len(chunks) == 2
        assert [failure.name for failure in got.boundary_failures] == ["a", "b"]


class TestDeferredHelpers:
    """The helpers that force the pool and hold a component's jobs."""

    def test_parallel_boundaries_lifts_the_request_lane_rules(self) -> None:
        assert pool_forced() is False
        with parallel_boundaries():
            assert pool_forced() is True
        assert pool_forced() is False

    def test_boundary_gate_holds_the_named_component(self) -> None:
        with boundary_gate(component="card") as gate:
            assert gate.name == "card"
            assert gate.wait(0) is False
        assert gate.released.is_set()
