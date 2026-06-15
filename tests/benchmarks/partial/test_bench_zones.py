from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory

from next.pages.manager import page as page_singleton
from next.partial import render_zone


if TYPE_CHECKING:
    from pathlib import Path

    from next.pages import Page


_ZONE_BODY = "<p>{{{{ v_{i} }}}}</p>"
_PLAIN_PARTS = "".join(f"<section>{_ZONE_BODY.format(i=i)}</section>" for i in range(5))
_ZONED_PARTS = "".join(
    f'{{% zone "z_{i}" %}}{_ZONE_BODY.format(i=i)}{{% endzone %}}' for i in range(5)
)
_PLAIN_TEMPLATE = f"<main>{_PLAIN_PARTS}</main>"
_ZONED_DJX = f"<main>{_ZONED_PARTS}</main>"
_KWARGS = {f"v_{i}": f"val_{i}" for i in range(5)}


def _write_zoned_page(directory: Path, body: str) -> Path:
    """Materialise a page.py plus template.djx and return the page path."""
    page_file = directory / "page.py"
    page_file.write_text("x = 1\n")
    (directory / "template.djx").write_text(body)
    return page_file


class TestBenchFiveZonePageAgainstBaseline:
    """Full render of a five-zone page against an equivalent plain page.

    The zone wrapper is concatenation only, so the gap against the plain
    baseline isolates the per-zone overhead and stays minimal.
    """

    @pytest.mark.benchmark(group="partial.full_render")
    def test_render_plain(self, tmp_path: Path, page_instance: Page, benchmark) -> None:
        page_path = tmp_path / "page.py"
        page_path.write_text("x = 1\n")
        page_instance.register_template(page_path, _PLAIN_TEMPLATE)
        benchmark(page_instance.render, page_path, **_KWARGS)

    @pytest.mark.benchmark(group="partial.full_render")
    def test_render_five_zones(
        self, tmp_path: Path, page_instance: Page, benchmark
    ) -> None:
        page_path = _write_zoned_page(tmp_path, _ZONED_DJX)
        benchmark(page_instance.render, page_path, **_KWARGS)


class TestBenchSingleZoneRender:
    """Standalone render of one zone over the full page context."""

    @pytest.mark.benchmark(group="partial.zone_render")
    def test_render_one_zone(self, tmp_path: Path, benchmark) -> None:
        page_file = _write_zoned_page(tmp_path, _ZONED_DJX)
        # render_zone drives the module-level page singleton, so seed its
        # composed-template cache once before the timed loop.
        page_singleton.composed_template_for(page_file)
        request = RequestFactory().get("/")
        benchmark(render_zone, page_file, ("z_0",), request, url_kwargs=_KWARGS)
