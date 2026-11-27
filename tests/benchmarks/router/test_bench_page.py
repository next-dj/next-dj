from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.urls import resolve

from next.pages.manager import page as page_singleton
from next.paths import PAGE_MARK
from tests.benchmarks.factories import REQUEST_ROUNDS, REQUEST_WARMUP_ROUNDS, each_round
from tests.support import plain_get, routed, write_page


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase


_PUBLIC = "x = 1\n"
_NAV = (
    "<nav>"
    + "".join(f'<a href="/docs/topic-{i}/">Topic {i}</a>' for i in range(20))
    + '<a href="/docs/">Docs</a></nav>'
)
_HEAD = "<!DOCTYPE html><html><head><title>Docs</title>{% collect_styles %}</head>"
_TAIL = "{% template %}{% collect_scripts %}</body></html>"
_NAV_LAYOUT = f"{_HEAD}<body>{_NAV}{_TAIL}"
_BARE_LAYOUT = f"{_HEAD}<body>{_TAIL}"
_SMALL = "<h1>Docs</h1><p>one paragraph</p>"
_LATIN = "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit sed do.</p>"
_CYRILLIC = "<p>Съешь же ещё этих мягких французских булок, да выпей чаю.</p>"
_LARGE_BYTES = 60_000
_DEFAULTS: dict[str, object] = {}
_LINKS_OFF = {"ACTIVE_LINKS": {"ENABLED": False}}


def _large(paragraph: str) -> str:
    """Return about 60 KB of markup made of `paragraph`."""
    size = len(paragraph.encode())
    return paragraph * (_LARGE_BYTES // size)


def _page_get() -> HttpRequest:
    """Return the GET of the page every case here serves."""
    return plain_get("/docs/")


@contextmanager
def _served(
    root: Path, layout: str, body: str, router: dict[str, object]
) -> Generator[Callable[[HttpRequest], HttpResponseBase]]:
    """Route one page under `layout` with the `ROUTER` settings given, yield its view."""
    (root / "layout.djx").write_text(layout)
    write_page(root, "docs", _PUBLIC, body=body)
    with routed(root, ROUTER=router):
        yield resolve("/docs/").func
        page_singleton.clear_template_caches()


def _bench(view: Callable[[HttpRequest], HttpResponseBase], benchmark) -> None:
    benchmark.pedantic(
        view,
        setup=each_round(_page_get),
        rounds=REQUEST_ROUNDS,
        warmup_rounds=REQUEST_WARMUP_ROUNDS,
    )


class TestBenchPageUnderDefaultRouter:
    """A page GET under the shipped `ROUTER` defaults, the injector and the pass run."""

    @pytest.mark.benchmark(group="router.page")
    def test_small_page(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, _NAV_LAYOUT, _SMALL, _DEFAULTS) as view:
            html = view(_page_get()).content.decode()
            assert "nx:s" in html
            assert html.count(PAGE_MARK) == 1
            _bench(view, benchmark)

    @pytest.mark.benchmark(group="router.page")
    def test_large_page_with_nav(self, tmp_path: Path, benchmark) -> None:
        """About 60 KB of ASCII body under a layout holding a nav of links."""
        with _served(tmp_path, _NAV_LAYOUT, _large(_LATIN), _DEFAULTS) as view:
            html = view(_page_get()).content.decode()
            assert "nx:s" in html
            assert html.count(PAGE_MARK) == 1
            assert len(html) > _LARGE_BYTES
            _bench(view, benchmark)

    @pytest.mark.benchmark(group="router.page")
    def test_large_cyrillic_page_without_nav(self, tmp_path: Path, benchmark) -> None:
        """About 60 KB of Cyrillic body and no nav container for the pass to find."""
        with _served(tmp_path, _BARE_LAYOUT, _large(_CYRILLIC), _DEFAULTS) as view:
            html = view(_page_get()).content.decode()
            assert "nx:s" in html
            assert PAGE_MARK not in html
            assert len(html.encode()) > _LARGE_BYTES
            _bench(view, benchmark)

    @pytest.mark.benchmark(group="router.page")
    def test_large_page_current_off(self, tmp_path: Path, benchmark) -> None:
        """The large nav page with `ROUTER.ACTIVE_LINKS` disabled, the reference row."""
        with _served(tmp_path, _NAV_LAYOUT, _large(_LATIN), _LINKS_OFF) as view:
            html = view(_page_get()).content.decode()
            assert "nx:s" in html
            assert PAGE_MARK not in html
            _bench(view, benchmark)
