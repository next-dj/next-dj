from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.http import HttpRequest

from next.pages.loaders import load_page_module
from next.pages.manager.views import unified_view as build_unified_view
from next.pages.metadata import resolve_metadata
from next.pages.metadata.chain import MetadataThunk
from next.seeding import METADATA_KEY
from tests.support.partial_requests import partial_meta


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import pytest
    from django.http.response import HttpResponseBase

    from next.pages import Page
    from next.pages.metadata import ResolvedMetadata


def build_page_request() -> HttpRequest:
    """Return the minimal ``HttpRequest`` the unified page view accepts."""
    request = HttpRequest()
    request.method = "GET"
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    return request


def resolve_page_metadata(
    page: Page, file_path: Path, request: HttpRequest | None = None, **kwargs: object
) -> ResolvedMetadata:
    """Resolve the metadata of `file_path` by building its whole render context."""
    context_data = page.build_render_context(file_path, request, **kwargs)
    thunk = context_data[METADATA_KEY]
    assert isinstance(thunk, MetadataThunk)
    return resolve_metadata(thunk.fold(context_data), request=request)


def build_zone_request(zone: str) -> HttpRequest:
    """Return a page GET asking for one zone, the shape a poll tick has."""
    request = build_page_request()
    request.META.update(partial_meta(zones=zone))
    return request


def build_nested_page(root: Path, *, body: str = "<h1>{{ title }}</h1>") -> Path:
    """Write a page under two ancestor layouts with a sibling ``template.djx``.

    The ancestor layouts wrap the body in ``<html>`` and ``<main>``, so a
    composition reads back as the chain that produced it.
    """
    (root / "layout.djx").write_text("<html>{% template %}</html>")
    mid = root / "mid"
    mid.mkdir()
    (mid / "layout.djx").write_text("<main>{% template %}</main>")
    leaf = mid / "leaf"
    leaf.mkdir()
    page_file = leaf / "page.py"
    page_file.write_text("x = 1")
    (leaf / "template.djx").write_text(body)
    return page_file


def write_page(
    root: Path,
    trail: str = "",
    source: str = 'template = "ok"\n',
    *,
    body: str | None = None,
) -> Path:
    """Write one ``page.py`` at ``trail`` under ``root`` and return it.

    A ``body`` also writes the sibling ``template.djx`` the page renders.
    """
    directory = root / trail
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(source)
    if body is not None:
        (directory / "template.djx").write_text(body)
    return page_file


def write_page_chain(root: Path, specs: Sequence[tuple[str, str]]) -> list[Path]:
    """Write one nested ``page.py`` per spec under ``root``, returned root first."""
    directory = root
    pages: list[Path] = []
    for name, source in specs:
        directory = directory / name
        directory.mkdir(exist_ok=True)
        page_file = directory / "page.py"
        page_file.write_text(source)
        pages.append(page_file)
    return pages


def page_naming_one_style(root: Path, *, directory: str = "named") -> Path:
    """Write a page whose ``styles`` list names a staticfiles asset, not a URL.

    The name is what makes the plan hold a URL a backend resolved rather than a
    literal, so every test about resolved plans starts from this one shape.
    """
    page_dir = root / directory
    page_dir.mkdir()
    page_path = page_dir / "page.py"
    page_path.write_text('styles = ["css/x.css"]\n')
    return page_path


def unified_view(page: Page, page_file: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of `page_file` the way the URL builder creates it."""
    return build_unified_view(page, page_file, load_page_module(page_file)[0])


def path_under(root: Path) -> Callable[[Path], bool]:
    """Return a predicate matching `root` itself and everything below it."""

    def matches(path: Path) -> bool:
        return path == root or root in path.parents

    return matches


def record_path_calls(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    keep: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """Collect the paths `method` is called on for the rest of the test.

    The real method still runs, so a recorded call reports a syscall the render made.
    """
    calls: list[Path] = []
    original = getattr(Path, method)

    def counting(self: Path, *args, **kwargs):
        if keep is None or keep(self):
            calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, counting)
    return calls
