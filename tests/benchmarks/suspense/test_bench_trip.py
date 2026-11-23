from __future__ import annotations

import re
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory
from django.urls import resolve

from next.pages.manager import page as page_singleton
from next.partial.headers import BOUNDARY, REQUEST_FLAG, ZONE
from tests.benchmarks.factories import (
    REQUEST_ROUNDS,
    REQUEST_WARMUP_ROUNDS,
    build_component,
    each_round,
)
from tests.support import plain_get, routed, write_page
from tests.support.access import COMPONENTS


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase


_LAYOUT = "<html><body>{% template %}</body></html>"
_CARD = """
from next.components import component

component.defer("visible", props=("sku", "n"))


@component.context("title")
def title(sku: str, n: int) -> str:
    return f"card {sku} x{n * 2}"
"""
_PUBLIC = "from next import page\n\npage.public()\n"
_WRAPPER = re.compile(
    r'<div id="(?P<id>b-[0-9a-f]{8}-[0-9]+)"[^>]* data-next-token="(?P<token>[^"]+)"'
)
_BOUNDARIES = 10


@contextmanager
def _site(root: Path, boundaries: int) -> Generator[Callable[..., HttpResponseBase]]:
    """Route a page of `boundaries` client-trip cards and yield its view."""
    (root / "layout.djx").write_text(_LAYOUT)
    build_component(
        root, "card", body="<p>{{ title }}</p>", module=_CARD, loading="<i>wait</i>"
    )
    calls = "".join(
        f'{{% component "card" sku="a" n={i} %}}' for i in range(boundaries)
    )
    write_page(root, "home", _PUBLIC, body=f"<main>{calls}</main>")
    with routed(root, COMPONENT_BACKENDS=COMPONENTS):
        yield resolve("/home/").func
        page_singleton.clear_template_caches()


def _reload(found: re.Match[str], token: str | None = None) -> HttpRequest:
    """Return the GET the runtime sends once the wrapper `found` scrolls into view."""
    headers = {REQUEST_FLAG: "1", ZONE: found["id"], BOUNDARY: token or found["token"]}
    return RequestFactory().get("/home/", headers=headers)


class TestBenchClientTrip:
    """A boundary the browser asks for later, from its shell to its follow-up GET."""

    @pytest.mark.benchmark(group="suspense.trip")
    def test_shell_mints_tokens(self, tmp_path: Path, benchmark) -> None:
        """The document GET, which runs no card and mints one token per wrapper."""
        request = plain_get("/home/")
        with _site(tmp_path, _BOUNDARIES) as view:
            html = view(request).content.decode()
            assert len(_WRAPPER.findall(html)) == _BOUNDARIES
            assert "card a" not in html
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/home/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="suspense.trip")
    def test_follow_up_get(self, tmp_path: Path, benchmark) -> None:
        """One boundary reloaded by its token, as an envelope with a single op."""
        with _site(tmp_path, 1) as view:
            found = _WRAPPER.search(view(plain_get("/home/")).content.decode())
            assert found is not None
            request = _reload(found)
            response = view(request)
            assert response.status_code == 200
            assert b"card a x0" in response.content
            benchmark.pedantic(
                view,
                setup=each_round(partial(_reload, found)),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="suspense.trip")
    def test_follow_up_get_refused(self, tmp_path: Path, benchmark) -> None:
        """A reload whose token fails its seal, refused before anything renders."""
        with _site(tmp_path, 1) as view:
            found = _WRAPPER.search(view(plain_get("/home/")).content.decode())
            assert found is not None
            request = _reload(found, found["token"][:-2] + "AA")
            response = view(request)
            assert b"card a" not in response.content
            benchmark.pedantic(
                view,
                setup=each_round(partial(_reload, found, found["token"][:-2] + "AA")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )
