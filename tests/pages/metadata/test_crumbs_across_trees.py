from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client
from django.test.utils import override_script_prefix
from django.urls import ResolverMatch, resolve

from next.pages.metadata.resolve import _filled_route, _fits, _start
from tests.support import (
    NAMESPACED_URLCONF,
    installed_apps,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import login_required_middleware, sign_in, write_proxy


_SHOW = (
    "{% breadcrumbs as crumbs %}"
    "{% for c in crumbs %}{{ c.label }}={{ c.url }}={{ c.current }};{% endfor %}"
)


def _crumb_page(root: Path, trail: str, label: str, head: str = "") -> None:
    source = f"{head}metadata = {{'breadcrumb': {label!r}}}\n"
    write_page(root, trail, source, body=_SHOW)


def _crumbs(
    root: Path,
    url: str,
    *codenames: str,
    urlconf: str = NAMESPACED_URLCONF,
    signed_in: bool = False,
) -> list[tuple[str, ...]]:
    client = Client()
    with installed_apps(root, "hub", "audit", urlconf=urlconf):
        if signed_in:
            sign_in(client, "visitor", *codenames)
        response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    return [tuple(item.split("=")) for item in body.split(";") if item]


def _hub(root: Path, host: dict[str, str]) -> None:
    write_app(root, "hub", {})
    for trail, label in host.items():
        _crumb_page(root / "hub" / "pages", trail, label)


def _audit(root: Path) -> None:
    write_app(root, "audit", {})
    pages = root / "audit" / "pages"
    _crumb_page(pages, "[str:section]/[int:pk]", "Item")
    _crumb_page(pages, "[str:section]/[int:pk]/history", "History")


_OWN = [("Item", "/books/1/", "False"), ("History", "/books/1/history/", "True")]


@pytest.mark.django_db()
class TestCrumbsAcrossAppTrees:
    """A tree without a root page leads with the crumbs of the page above it."""

    @pytest.mark.parametrize(
        ("host", "leading"),
        [
            pytest.param(
                {"": "Home", "[str:section]": "Section"},
                [("Home", "/", "False"), ("Section", "/books/", "False")],
                id="deepest-host",
            ),
            pytest.param({"": "Home"}, [("Home", "/", "False")], id="no-middle-page"),
            pytest.param({"elsewhere": "Elsewhere"}, [], id="no-host"),
        ],
    )
    def test_the_host_crumbs_lead(self, tmp_path: Path, host, leading) -> None:
        _hub(tmp_path, host)
        _audit(tmp_path)
        assert _crumbs(tmp_path, "/books/1/history/") == [*leading, *_OWN]

    def test_under_a_script_prefix(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "[str:section]": "Section"})
        _audit(tmp_path)
        with override_script_prefix("/app/"):
            crumbs = _crumbs(tmp_path, "/books/1/history/")
        assert [url for _label, url, _current in crumbs] == [
            "/app/",
            "/app/books/",
            "/app/books/1/",
            "/app/books/1/history/",
        ]

    def test_a_tree_with_a_root_page_keeps_its_own_crumbs(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "books": "Books"})
        write_app(tmp_path, "audit", {})
        assert _crumbs(tmp_path, "/books/") == [
            ("Home", "/", "False"),
            ("Books", "/books/", "True"),
        ]


@pytest.mark.django_db()
class TestCrumbsOfAPagesTree:
    """A `pages()` tree under a prefix leads with the crumbs of the prefix pages."""

    def test_the_prefix_pages_lead(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "books": "Books"})
        write_app(tmp_path, "audit", {})
        pages = tmp_path / "audit" / "pages"
        _crumb_page(pages, "", "Item")
        _crumb_page(pages, "history", "History")
        urlconf = write_urlconf(
            tmp_path,
            "crumbs_prefix_urls",
            'path("books/<int:pk>/", pages("audit"))',
            'path("", include("next.urls"))',
        )
        assert _crumbs(tmp_path, "/books/1/history/", urlconf=urlconf) == [
            ("Home", "/", "False"),
            ("Books", "/books/", "False"),
            ("Item", "/books/1/", "False"),
            ("History", "/books/1/history/", "True"),
        ]


@pytest.mark.django_db()
class TestCrumbsOfAGuardedHost:
    """A host page the request may not reach adds no crumbs to the trail."""

    @pytest.mark.parametrize(
        ("codenames", "leading"),
        [
            pytest.param((), [], id="refused"),
            pytest.param(
                ("view_user",),
                [("Home", "/", "False"), ("Section", "/books/", "False")],
                id="held",
            ),
        ],
    )
    def test_a_permission(self, tmp_path: Path, codenames, leading) -> None:
        _hub(tmp_path, {"": "Home"})
        _crumb_page(
            tmp_path / "hub" / "pages",
            "[str:section]",
            "Section",
            "from next.pages import page\n\n"
            'page.permission_required("auth.view_user")\n\n',
        )
        _audit(tmp_path)
        crumbs = _crumbs(tmp_path, "/books/1/history/", *codenames, signed_in=True)
        assert crumbs == [*leading, *_OWN]

    def test_a_broken_host_chain_adds_no_crumb(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "[str:section]": "Section"})
        write_proxy(tmp_path / "hub" / "pages" / "[str:section]", "raise RuntimeError")
        _audit(tmp_path)
        assert _crumbs(tmp_path, "/books/1/history/") == _OWN

    def test_the_login_middleware_admits_a_signed_in_user(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "[str:section]": "Section"})
        _audit(tmp_path)
        with login_required_middleware():
            crumbs = _crumbs(tmp_path, "/books/1/history/", signed_in=True)
        assert crumbs == [
            ("Home", "/", "False"),
            ("Section", "/books/", "False"),
            *_OWN,
        ]


def _routed_view(trail: str, page_path: Path) -> object:
    def view(request: object) -> None:
        del request

    view.next_route_trail = trail
    view.next_page_path = page_path
    return view


class TestHostWalkEdges:
    """The host walk reads only a file router view under a plain `path()` route."""

    def test_a_view_without_a_trail_walks_nothing(self) -> None:
        assert _start(ResolverMatch(print, (), {}, route="x/")) is None

    def test_a_regex_route_walks_nothing(self, tmp_path: Path) -> None:
        view = _routed_view("x", tmp_path / "x" / "page.py")
        assert _start(ResolverMatch(view, (), {}, route="^x/$")) is None

    def test_a_route_part_without_its_kwarg_fills_nothing(self) -> None:
        assert _filled_route("<int:pk>/", {}) is None


class _Refusing:
    """A converter whose `to_url` refuses every value, as Django lets one."""

    def to_url(self, value: object) -> str:
        msg = f"refused {value!r}"
        raise ValueError(msg)


def test_a_converter_refusing_the_value_fills_nothing() -> None:
    with patch(
        "next.pages.metadata.resolve.get_converters", return_value={"int": _Refusing()}
    ):
        assert _filled_route("<int:pk>/", {"pk": 1}) is None


def _resolves(root: Path, urlconf: str, *urls: str) -> list[int]:
    counts: list[int] = []
    with (
        installed_apps(root, "hub", "audit", urlconf=urlconf),
        patch("next.pages.metadata.resolve.resolve", wraps=resolve) as spy,
    ):
        for url in urls:
            spy.reset_mock()
            assert Client().get(url).status_code == 200
            counts.append(spy.call_count)
    return counts


@pytest.mark.django_db()
class TestHostWalkCost:
    """The host walk resolves a prefix only where another tree may answer it, once."""

    def test_one_tree_under_a_prefix_resolves_nothing(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "books/[int:pk]/history": "History"})
        write_app(tmp_path, "audit", {})
        urlconf = write_urlconf(
            tmp_path, "crumbs_one_tree_urls", 'path("site/", include("next.urls"))'
        )
        assert _resolves(tmp_path, urlconf, "/site/", "/site/books/1/history/") == [
            0,
            0,
        ]

    def test_a_repeated_request_resolves_nothing_again(self, tmp_path: Path) -> None:
        _hub(tmp_path, {"": "Home", "[str:section]": "Section"})
        _audit(tmp_path)
        first, second = _resolves(
            tmp_path, NAMESPACED_URLCONF, "/books/1/history/", "/books/1/history/"
        )
        assert first > 0
        assert second == 0


@pytest.mark.parametrize(
    ("prefix", "fits"),
    [
        pytest.param(("shop", "a", "b"), True, id="rest-of-the-url"),
        pytest.param(("shop",), False, id="nothing-left"),
        pytest.param(("blog", "a"), False, id="another-head"),
    ],
)
def test_a_path_parameter_takes_the_rest(prefix: tuple[str, ...], fits) -> None:
    assert _fits(prefix, ("shop", "<path:rest>")) is fits
