from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.pages.loaders import load_python_module_memo
from next.pages.manager import page as page_singleton
from next.views.pages import unified_view
from tests.benchmarks.factories import (
    REQUEST_ROUNDS,
    REQUEST_WARMUP_ROUNDS,
    build_layout_page,
    each_round,
)
from tests.support import partial_meta, plain_get


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase


_STATIC_TEMPLATE = "<h1>{{ request.path }}</h1>" + "".join(
    f"<p>row {i}</p>" for i in range(10)
)
_DYNAMIC_PAGE = (
    "def render(request):\n"
    "    return '<h1>dynamic</h1>"
    + "".join(f"<p>row {i}</p>" for i in range(10))
    + "'\n"
)
# Cold rounds discard every composed layer, so keep the count modest.
_COLD_ROUNDS = 50
_COLD_WARMUP_ROUNDS = 5

_ZONED_TEMPLATE = (
    "<main>"
    + "".join(f'{{% zone "z_{i}" %}}<p>row {i}</p>{{% endzone %}}' for i in range(5))
    + "</main>"
)


def _page_get() -> HttpRequest:
    """Return the GET of the page every case here serves."""
    return plain_get("/")


def _forget_layers() -> None:
    """Throw away the composed source and the compiled template of every page."""
    page_singleton.clear_template_caches()


def _zone_get() -> HttpRequest:
    """Return the GET a client polling one zone sends."""
    request = plain_get("/")
    request.META.update(partial_meta(zones="z_0"))
    return request


@contextmanager
def _view_for(page_file: Path) -> Generator[Callable[..., HttpResponseBase]]:
    """Build the unified view of a page on the module-level singleton.

    Created the way the URL builder creates it, so only the per-request path is timed.
    """
    module = load_python_module_memo(page_file)
    view = unified_view(page_singleton, page_file, module)
    try:
        yield view
    finally:
        page_singleton.clear_template_caches()


@pytest.mark.walltime()
class TestBenchColdViewGet:
    """The same GETs with the composed layers thrown away before every round.

    `clear_template_caches` drops the composed source and compiled template.
    """

    @pytest.mark.benchmark(group="pages.view")
    def test_static_cold_composition(self, tmp_path: Path, benchmark) -> None:
        """A static body composed through two layouts and compiled per round."""
        page_file = build_layout_page(tmp_path, layouts=2, template=_STATIC_TEMPLATE)
        with _view_for(page_file) as view:
            view(_page_get())

            benchmark.pedantic(
                view,
                setup=each_round(_page_get, before=_forget_layers),
                rounds=_COLD_ROUNDS,
                warmup_rounds=_COLD_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="pages.view")
    def test_dynamic_cold_skeleton(self, tmp_path: Path, benchmark) -> None:
        """A `render()` body, so the round prices `compose_skeleton` on its own."""
        page_file = build_layout_page(tmp_path, layouts=2, page_body=_DYNAMIC_PAGE)
        with _view_for(page_file) as view:
            view(_page_get())

            benchmark.pedantic(
                view,
                setup=each_round(_page_get, before=_forget_layers),
                rounds=_COLD_ROUNDS,
                warmup_rounds=_COLD_WARMUP_ROUNDS,
            )

    @pytest.mark.parametrize("layouts", [0, 8], ids=["flat", "deep"])
    @pytest.mark.benchmark(group="pages.view")
    def test_static_cold_layout_depth(
        self, tmp_path: Path, layouts: int, benchmark
    ) -> None:
        """What layout-chain depth costs a composition rather than a replay."""
        page_file = build_layout_page(
            tmp_path, layouts=layouts, template=_STATIC_TEMPLATE
        )
        with _view_for(page_file) as view:
            view(_page_get())

            benchmark.pedantic(
                view,
                setup=each_round(_page_get, before=_forget_layers),
                rounds=_COLD_ROUNDS,
                warmup_rounds=_COLD_WARMUP_ROUNDS,
            )


class TestBenchUnifiedViewGet:
    """Per-GET cost of the unified page view, the path production serves."""

    @pytest.mark.walltime()
    @pytest.mark.benchmark(group="pages.view")
    def test_static_warm(self, tmp_path: Path, benchmark) -> None:
        page_file = build_layout_page(tmp_path, layouts=2, template=_STATIC_TEMPLATE)
        with _view_for(page_file) as view:
            view(_page_get())
            benchmark.pedantic(
                view,
                setup=each_round(_page_get),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.walltime()
    @pytest.mark.benchmark(group="pages.view")
    def test_dynamic_warm(self, tmp_path: Path, benchmark) -> None:
        page_file = build_layout_page(tmp_path, layouts=2, page_body=_DYNAMIC_PAGE)
        with _view_for(page_file) as view:
            view(_page_get())
            benchmark.pedantic(
                view,
                setup=each_round(_page_get),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.walltime()
    @pytest.mark.benchmark(group="pages.view")
    def test_static_warm_debug(self, tmp_path: Path, benchmark) -> None:
        """The same static GET under `DEBUG`, where the caches stat their sources.

        Every other case measures the production branch, this one the dev loop.
        """
        page_file = build_layout_page(tmp_path, layouts=2, template=_STATIC_TEMPLATE)
        with override_settings(DEBUG=True), _view_for(page_file) as view:
            view(_page_get())
            benchmark.pedantic(
                view,
                setup=each_round(_page_get),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.walltime()
    @pytest.mark.parametrize("layouts", [0, 2, 8], ids=["flat", "nested", "deep"])
    @pytest.mark.benchmark(group="pages.view")
    def test_static_layout_depth(self, tmp_path: Path, layouts: int, benchmark) -> None:
        """Layout-chain depth against a flat page, on one static body.

        The two-layout point repeats `test_static_warm` to report the stand's spread.
        """
        page_file = build_layout_page(
            tmp_path, layouts=layouts, template=_STATIC_TEMPLATE
        )
        with _view_for(page_file) as view:
            view(_page_get())
            benchmark.pedantic(
                view,
                setup=each_round(_page_get),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="pages.view")
    def test_zone_tick(self, tmp_path: Path, benchmark) -> None:
        """One zone GET through the view, the same page as `test_static_warm`."""
        page_file = build_layout_page(tmp_path, layouts=2, template=_ZONED_TEMPLATE)
        with _view_for(page_file) as view:
            view(_zone_get())
            benchmark.pedantic(
                view,
                setup=each_round(_zone_get),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )
