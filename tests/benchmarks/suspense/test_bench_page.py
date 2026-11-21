from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING

import pytest
from django.urls import resolve

from next.pages.manager import page as page_singleton
from next.suspense import StreamingPageResponse
from tests.benchmarks.factories import (
    REQUEST_ROUNDS,
    REQUEST_WARMUP_ROUNDS,
    build_component,
    each_round,
    threaded_get,
)
from tests.pages.checks.metadata.trees import COMPONENTS
from tests.support import plain_get, routed, write_page


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase


_LAYOUT = (
    "<!DOCTYPE html><html><head>{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
_CARD = """
from next.components import component


@component.context("value")
def value() -> str:
    return "ready"
"""
_CARD_BODY = "<p>{{ n }} {{ value }}</p>"
_PUBLIC = "x = 1\n"
_BOUNDARIES = pytest.mark.parametrize("boundaries", [1, 10], ids=["one", "ten"])
_COLD_ROUNDS = 50
_COLD_WARMUP_ROUNDS = 5


def _cards(count: int, name: str = "card") -> str:
    """Return a page body calling `name` at `count` call sites of its own."""
    calls = "".join(f'{{% component "{name}" n={i} %}}' for i in range(count))
    return f"<main>{calls}</main>"


@contextmanager
def _site(
    root: Path,
    body: str,
    *,
    loading: str | None = "<p>loading</p>",
    error: bool = False,
) -> Iterator[Callable[[HttpRequest], HttpResponseBase]]:
    """Route a one-page site around the `card` component and yield the page view."""
    (root / "layout.djx").write_text(_LAYOUT)
    build_component(root, "card", body=_CARD_BODY, module=_CARD, loading=loading)
    build_component(
        root, "guarded", body=_CARD_BODY, module=_CARD, error="<b>{{ error.kind }}</b>"
    )
    write_page(root, "cards", _PUBLIC, body=body)
    if error:
        (root / "cards" / "error.djx").write_text("<b>{{ error.kind }}</b>")
    with routed(root, COMPONENT_BACKENDS=COMPONENTS):
        try:
            yield resolve("/cards/").func
        finally:
            page_singleton.clear_template_caches()


def _read(
    view: Callable[[HttpRequest], HttpResponseBase], request: HttpRequest
) -> bytes:
    """Return the whole body of a streamed page, closed as the server closes it."""
    response = view(request)
    assert isinstance(response, StreamingPageResponse)
    try:
        return b"".join(response.streaming_content)
    finally:
        response.close()


class TestBenchDeferredPageInline:
    """A GET of a page whose deferred jobs settle before the response is returned.

    A sync worker serves one request at a time, so the policy assembles the page whole.
    """

    @_BOUNDARIES
    @pytest.mark.benchmark(group="suspense.page")
    def test_inline_assembly(self, tmp_path: Path, boundaries: int, benchmark) -> None:
        request = plain_get("/cards/")
        with _site(tmp_path, _cards(boundaries)) as view:
            response = view(request)
            assert response.content.count(b'data-next-state="ready"') == boundaries
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="suspense.page")
    def test_inline_assembly_cold_composition(self, tmp_path: Path, benchmark) -> None:
        """Ten boundaries with the composed layers thrown away before every round."""
        request = plain_get("/cards/")
        with _site(tmp_path, _cards(10)) as view:
            view(request)

            benchmark.pedantic(
                view,
                setup=each_round(
                    partial(plain_get, "/cards/"),
                    before=page_singleton.clear_template_caches,
                ),
                rounds=_COLD_ROUNDS,
                warmup_rounds=_COLD_WARMUP_ROUNDS,
            )

    @_BOUNDARIES
    @pytest.mark.benchmark(group="suspense.page")
    def test_same_page_without_deferral(
        self, tmp_path: Path, boundaries: int, benchmark
    ) -> None:
        """The same components rendered in place, the floor a deferral is priced on."""
        request = plain_get("/cards/")
        with _site(tmp_path, _cards(boundaries), loading=None) as view:
            response = view(request)
            assert response.content.count(b" ready</p>") == boundaries
            assert b"data-next-boundary" not in response.content
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )


class TestBenchDeferredPageStreamed:
    """A GET of the same page under a threaded server, its streamed body read whole."""

    @_BOUNDARIES
    @pytest.mark.benchmark(group="suspense.page")
    def test_streamed(self, tmp_path: Path, boundaries: int, benchmark) -> None:
        request = threaded_get("/cards/")
        with _site(tmp_path, _cards(boundaries)) as view:
            body = _read(view, request)
            assert body.count(b'"s":"ready"') == boundaries
            benchmark.pedantic(
                partial(_read, view),
                setup=each_round(partial(threaded_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="suspense.page")
    def test_streamed_shell(self, tmp_path: Path, benchmark) -> None:
        """Ten boundaries up to the returned response, before any chunk is read."""
        request = threaded_get("/cards/")
        with _site(tmp_path, _cards(10)) as view:

            def shell(request: HttpRequest) -> None:
                view(request).close()

            shell(request)
            benchmark.pedantic(
                shell,
                setup=each_round(partial(threaded_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )


class TestBenchErrorBoundary:
    """A GET of a page guarded by an error boundary that contains no failure."""

    @pytest.mark.benchmark(group="suspense.page")
    def test_page_boundary_idle(self, tmp_path: Path, benchmark) -> None:
        """An `error.djx` in the page directory, around ten plain components."""
        request = plain_get("/cards/")
        with _site(tmp_path, _cards(10), loading=None, error=True) as view:
            response = view(request)
            assert response.status_code == 200
            assert response.content.count(b" ready</p>") == 10
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="suspense.page")
    def test_component_boundary_idle(self, tmp_path: Path, benchmark) -> None:
        """Ten calls of a component that carries an `error.djx` of its own."""
        request = plain_get("/cards/")
        with _site(tmp_path, _cards(10, "guarded")) as view:
            response = view(request)
            assert response.status_code == 200
            assert response.content.count(b" ready</p>") == 10
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/cards/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )
