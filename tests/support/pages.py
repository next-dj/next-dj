from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.http import HttpRequest

from next.pages.loaders import _load_python_module_memo
from tests.support.partial_requests import partial_meta


if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest
    from django.http.response import HttpResponseBase

    from next.pages import Page


def build_page_request() -> HttpRequest:
    """Return the minimal ``HttpRequest`` the unified page view accepts."""
    request = HttpRequest()
    request.method = "GET"
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    return request


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
    (root / "layout.djx").write_text(
        "<html>{% block template %}{% endblock template %}</html>"
    )
    mid = root / "mid"
    mid.mkdir()
    (mid / "layout.djx").write_text(
        "<main>{% block template %}{% endblock template %}</main>"
    )
    leaf = mid / "leaf"
    leaf.mkdir()
    page_file = leaf / "page.py"
    page_file.write_text("x = 1")
    (leaf / "template.djx").write_text(body)
    return page_file


def unified_view(page: Page, page_file: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of `page_file` the way the URL builder creates it."""
    return page._create_unified_view(page_file, {}, _load_python_module_memo(page_file))


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

    The real method still runs, so a recorded call reports a syscall the
    render performed rather than replacing it.
    """
    calls: list[Path] = []
    original = getattr(Path, method)

    def counting(self: Path, *args, **kwargs):
        if keep is None or keep(self):
            calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, counting)
    return calls
