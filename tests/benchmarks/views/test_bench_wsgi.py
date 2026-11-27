from __future__ import annotations

import gzip
import io
import sys
import types
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.core.handlers.wsgi import WSGIHandler
from django.core.signals import request_finished, request_started
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse
from django.template import engines
from django.test import override_settings
from django.urls import include, path

from next.pages.manager import page as page_singleton
from next.router.render import LAYOUTS_ATTR
from tests.support import file_router_config_entry, write_page
from tests.support.access import COMPONENTS


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
_COMPRESSED = [
    _MIDDLEWARE[0],
    "next.middleware.CompressionMiddleware",
    *_MIDDLEWARE[1:],
]
_GZIP = {"HTTP_ACCEPT_ENCODING": "gzip"}
_ENVIRON = {
    "REQUEST_METHOD": "GET",
    "QUERY_STRING": "",
    "SCRIPT_NAME": "",
    "SERVER_NAME": "testserver",
    "SERVER_PORT": "80",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "REMOTE_ADDR": "127.0.0.1",
    "HTTP_HOST": "testserver",
    "HTTP_ACCEPT": "text/html",
    "HTTP_COOKIE": "csrftoken=" + "a" * 32,
    "HTTP_SEC_FETCH_SITE": "none",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_DEST": "document",
    "wsgi.version": (1, 0),
    "wsgi.url_scheme": "http",
    "wsgi.errors": sys.stderr,
    "wsgi.multiprocess": True,
    "wsgi.multithread": False,
    "wsgi.run_once": False,
}
_NAV = (
    '<nav><a href="/">Home</a><a href="/simple/">Simple</a>'
    '<a href="/cards/">Cards</a><a href="/about/">About</a></nav>'
)
_ROW = '<p class="row">Row {n} of the static body, the text a page carries.</p>\n'
_BODY = (
    "<h1>{{ title }}</h1><p>Hello, {{ user_name }}.</p>"
    "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>\n"
)
_CARD = '<div class="card"><h3>{{ title }}</h3><p>Item {{ n }}</p></div>'
_CARDS = 8
_CONTEXT = """
from next import context


@context("title")
def title() -> str:
    return "Dashboard"


@context("items")
def items() -> list[str]:
    return ["alpha", "beta", "gamma"]


@context("user_name")
def user_name() -> str:
    return "guest"
"""
_VALUES = {"title": "Dashboard", "items": ["alpha", "beta", "gamma"]}
_OFF = {"ROUTER": {"ENABLED": False, "ACTIVE_LINKS": {"ENABLED": False}}}
_SIZES = pytest.mark.parametrize(
    "rows", [pytest.param(20, id="2kb"), pytest.param(270, id="20kb")]
)
_LARGE = 2700
_LAYOUTS = f'<script type="application/json" {LAYOUTS_ATTR}'.encode()


def _rows(count: int) -> str:
    """Return `count` static paragraphs, about 75 bytes each."""
    return "".join(_ROW.format(n=n) for n in range(count))


def _layout(slot: str, *, scripts: bool) -> str:
    """Return the root layout around `slot`, the collect tags written when `scripts`."""
    styles = "{% collect_styles %}" if scripts else ""
    tail = "{% collect_scripts %}" if scripts else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Bench</title>{styles}</head><body><header>{_NAV}</header>"
        f"<main>{slot}</main><footer><p>Footer</p></footer>{tail}</body></html>"
    )


def _cards(call: str) -> str:
    """Return the body that renders the card `_CARDS` times through `call`."""
    return "<h1>{{ title }}</h1><section>" + call * _CARDS + "</section>"


def _write_site(root: Path, rows: int) -> None:
    """Write the next.dj side, a simple page, a page of cards and a nested page."""
    root.mkdir(parents=True)
    (root / "layout.djx").write_text(_layout("{% template %}", scripts=True))
    write_page(root, "simple", _CONTEXT, body=_BODY + _rows(rows))
    write_page(
        root,
        "cards",
        _CONTEXT,
        body=_cards('{% component "card" title="Card" n=1 %}') + _rows(rows),
    )
    card = root / "_components" / "card"
    card.mkdir(parents=True)
    (card / "component.djx").write_text(_CARD)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / "a" / "layout.djx").write_text("<div><aside>A</aside>{% template %}</div>")
    (nested / "layout.djx").write_text("<div><h2>B</h2>{% template %}</div>")
    write_page(nested, "deep", _CONTEXT, body=_BODY + _rows(rows))


def _django_view(source: str) -> Callable[[HttpRequest], HttpResponse]:
    """Return a plain Django view rendering `source` over the page values."""
    compiled = engines["django"].from_string(source)

    def view(request: HttpRequest) -> HttpResponse:
        return HttpResponse(compiled.render({**_VALUES, "user_name": "guest"}, request))

    return view


def _urlconf(rows: int) -> types.ModuleType:
    """Return the URLconf serving the Django twins beside every next.dj page."""
    body = _BODY + _rows(rows)
    token = _layout(body, scripts=False).replace(
        "<body>", """<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>"""
    )
    card = _CARD.replace("{{ n }}", "1")
    module = types.ModuleType("bench_wsgi_urls")
    module.urlpatterns = [
        path("dj/simple/", _django_view(_layout(body, scripts=False))),
        path("dj/token/", _django_view(token)),
        path(
            "dj/cards/",
            _django_view(_layout(_cards(card) + _rows(rows), scripts=False)),
        ),
        path("", include("next.urls")),
    ]
    return module


def _started(*_status: object) -> None:
    """Take the status line and headers the handler starts the response with."""


def _get(
    handler: WSGIHandler, url: str, headers: dict[str, str] | None = None
) -> bytes:
    """Serve one GET as a WSGI server does, the body read whole and closed."""
    environ = {
        **_ENVIRON,
        **(headers or {}),
        "PATH_INFO": url,
        "wsgi.input": io.BytesIO(b""),
    }
    response = handler(environ, _started)
    try:
        return b"".join(response)
    finally:
        response.close()


@contextmanager
def _served(
    tmp_path: Path,
    rows: int,
    framework: dict[str, object],
    middleware: list[str] = _MIDDLEWARE,
) -> Generator[WSGIHandler]:
    """Serve the site under the framework settings given, the rest at their defaults."""
    root = tmp_path / "pages"
    _write_site(root, rows)
    settings = {
        "PAGE_BACKENDS": [file_router_config_entry(pages_dir=root)],
        "COMPONENT_BACKENDS": COMPONENTS,
        **framework,
    }
    # No database is wired for these pages, as the Django test client also leaves out.
    request_started.disconnect(close_old_connections)
    request_finished.disconnect(close_old_connections)
    try:
        with override_settings(
            ROOT_URLCONF=_urlconf(rows), MIDDLEWARE=middleware, NEXT_FRAMEWORK=settings
        ):
            yield WSGIHandler()
            page_singleton.clear_template_caches()
    finally:
        request_started.connect(close_old_connections)
        request_finished.connect(close_old_connections)


def _bench(
    handler: WSGIHandler, url: str, benchmark, *, routed: bool | None = None
) -> None:
    """Time `url`, checked first to render whole and routed as the case says."""
    body = _get(handler, url)
    assert b"<footer>" in body
    if b"/cards/" in url.encode():
        assert body.count(b'class="card"') == _CARDS
    if routed is not None:
        assert (_LAYOUTS in body) is routed
    benchmark(_get, handler, url)


class TestBenchDjangoSides:
    """The plain Django views a next.dj page is measured against."""

    @_SIZES
    @pytest.mark.benchmark(group="views.wsgi.simple")
    def test_django_view(self, tmp_path: Path, benchmark, rows: int) -> None:
        with _served(tmp_path, rows, {}) as handler:
            _bench(handler, "/dj/simple/", benchmark)

    @_SIZES
    @pytest.mark.benchmark(group="views.wsgi.simple")
    def test_django_view_with_a_csrf_token(
        self, tmp_path: Path, benchmark, rows: int
    ) -> None:
        with _served(tmp_path, rows, {}) as handler:
            _bench(handler, "/dj/token/", benchmark)

    @pytest.mark.benchmark(group="views.wsgi.cards")
    def test_django_includes(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 20, {}) as handler:
            _bench(handler, "/dj/cards/", benchmark)


class TestBenchPageAtDefaults:
    """A next.dj page under the shipped settings, the router and active links on."""

    @_SIZES
    @pytest.mark.benchmark(group="views.wsgi.simple")
    def test_simple_page(self, tmp_path: Path, benchmark, rows: int) -> None:
        with _served(tmp_path, rows, {}) as handler:
            _bench(handler, "/simple/", benchmark, routed=True)

    @pytest.mark.benchmark(group="views.wsgi.large")
    def test_large_page(self, tmp_path: Path, benchmark) -> None:
        """About 200 KB of static body, where every pass over the HTML shows."""
        with _served(tmp_path, _LARGE, {}) as handler:
            _bench(handler, "/simple/", benchmark, routed=True)

    @pytest.mark.benchmark(group="views.wsgi.cards")
    def test_eight_components(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 20, {}) as handler:
            _bench(handler, "/cards/", benchmark, routed=True)

    @pytest.mark.benchmark(group="views.wsgi.nested")
    def test_three_layouts(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 20, {}) as handler:
            _bench(handler, "/a/b/deep/", benchmark, routed=True)


class TestBenchCompressedPage:
    """A next.dj page through the compression middleware, the client taking gzip."""

    @pytest.mark.benchmark(group="views.wsgi.simple")
    def test_simple_page_in_gzip(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 270, {}, _COMPRESSED) as handler:
            body = gzip.decompress(_get(handler, "/simple/", _GZIP))
            assert b"<footer>" in body
            assert _LAYOUTS in body
            benchmark(_get, handler, "/simple/", _GZIP)


class TestBenchPageWithEveryFeatureOff:
    """The same pages with the router and active links off, the floor defaults pay."""

    @_SIZES
    @pytest.mark.benchmark(group="views.wsgi.simple")
    def test_simple_page(self, tmp_path: Path, benchmark, rows: int) -> None:
        with _served(tmp_path, rows, _OFF) as handler:
            _bench(handler, "/simple/", benchmark, routed=False)

    @pytest.mark.benchmark(group="views.wsgi.large")
    def test_large_page(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, _LARGE, _OFF) as handler:
            _bench(handler, "/simple/", benchmark, routed=False)

    @pytest.mark.benchmark(group="views.wsgi.cards")
    def test_eight_components(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 20, _OFF) as handler:
            _bench(handler, "/cards/", benchmark, routed=False)

    @pytest.mark.benchmark(group="views.wsgi.nested")
    def test_three_layouts(self, tmp_path: Path, benchmark) -> None:
        with _served(tmp_path, 20, _OFF) as handler:
            _bench(handler, "/a/b/deep/", benchmark, routed=False)
