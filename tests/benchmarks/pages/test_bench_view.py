from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.pages.loaders import _load_python_module_memo
from next.pages.manager import page as page_singleton
from tests.benchmarks.factories import build_layout_page
from tests.support import partial_meta, plain_get


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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
_ZONED_TEMPLATE = (
    "<main>"
    + "".join(f'{{% zone "z_{i}" %}}<p>row {i}</p>{{% endzone %}}' for i in range(5))
    + "</main>"
)


def _zone_get() -> HttpRequest:
    """Return the GET a client polling one zone sends."""
    request = plain_get("/")
    request.META.update(partial_meta(zones="z_0"))
    return request


@contextmanager
def _view_for(page_file: Path) -> Iterator[Callable[..., HttpResponseBase]]:
    """Build the unified view of a page on the module-level singleton.

    Created the way the URL builder creates it, so the benchmark times the
    per-request path and none of the build-time work.
    """
    module = _load_python_module_memo(page_file)
    view = page_singleton._create_unified_view(page_file, {}, module)
    try:
        yield view
    finally:
        page_singleton.clear_template_caches()


class TestBenchUnifiedViewGet:
    """Per-GET cost of the unified page view, the path production serves."""

    @pytest.mark.benchmark(group="pages.view")
    def test_static_warm(self, tmp_path: Path, benchmark) -> None:
        page_file = build_layout_page(tmp_path, layouts=2, template=_STATIC_TEMPLATE)
        request = plain_get("/")
        with _view_for(page_file) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.benchmark(group="pages.view")
    def test_dynamic_warm(self, tmp_path: Path, benchmark) -> None:
        page_file = build_layout_page(tmp_path, layouts=2, page_body=_DYNAMIC_PAGE)
        request = plain_get("/")
        with _view_for(page_file) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.benchmark(group="pages.view")
    def test_static_warm_debug(self, tmp_path: Path, benchmark) -> None:
        """The same static GET under `DEBUG`, where the caches stat their sources.

        Every other case here measures the production branch, this one
        prices the development loop.
        """
        page_file = build_layout_page(tmp_path, layouts=2, template=_STATIC_TEMPLATE)
        request = plain_get("/")
        with override_settings(DEBUG=True), _view_for(page_file) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.parametrize("layouts", [0, 2, 8], ids=["flat", "nested", "deep"])
    @pytest.mark.benchmark(group="pages.view")
    def test_static_layout_depth(self, tmp_path: Path, layouts: int, benchmark) -> None:
        """Layout-chain depth against a flat page, on one static body.

        The two-layout point repeats `test_static_warm` on purpose, so the
        pair reports the spread the stand puts on one configuration.
        """
        page_file = build_layout_page(
            tmp_path, layouts=layouts, template=_STATIC_TEMPLATE
        )
        request = plain_get("/")
        with _view_for(page_file) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.benchmark(group="pages.view")
    def test_zone_tick(self, tmp_path: Path, benchmark) -> None:
        """One zone GET through the view, the same page as `test_static_warm`."""
        page_file = build_layout_page(tmp_path, layouts=2, template=_ZONED_TEMPLATE)
        request = _zone_get()
        with _view_for(page_file) as view:
            view(request)
            benchmark(view, request)
