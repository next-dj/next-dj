from pathlib import Path

import pytest
from django.test import Client
from django.test.utils import override_script_prefix
from django.urls import ResolverMatch

from next.pages.metadata.resolve import (
    _crumb_route,
    _filled_route,
    _host_crumbs,
    _host_url,
    _Mount,
    _mount_of,
    _page_match,
)
from tests.support import (
    NAMESPACED_URLCONF,
    handler_declared_here,
    installed_apps,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import (
    CONTEXT_CALLS,
    login_required_middleware,
    sign_in,
    write_proxy,
)


_SHOW = (
    "{% breadcrumbs as crumbs %}"
    "{% for c in crumbs %}{{ c.label }}={{ c.url }}={{ c.current }};{% endfor %}"
)
_PREFIX = "<str:section>/<int:pk>/"


def _crumb_page(root: Path, trail: str, label: str) -> None:
    write_page(root, trail, f"metadata = {{'breadcrumb': {label!r}}}\n", body=_SHOW)


def _apps(root: Path, host: dict[str, str]) -> None:
    write_app(root, "hub", {}, mounts={"pages": ""})
    for trail, label in host.items():
        _crumb_page(root / "hub" / "pages", trail, label)
    write_app(root, "audit", {}, mounts={"pages": _PREFIX})
    _crumb_page(root / "audit" / "pages", "", "Item")
    _crumb_page(root / "audit" / "pages", "history", "History")


def _crumbs(
    root: Path, url: str, *codenames: str, signed_in: bool = False
) -> list[tuple[str, ...]]:
    client = Client()
    with installed_apps(root, "hub", "audit", urlconf=NAMESPACED_URLCONF):
        if signed_in:
            sign_in(client, "visitor", *codenames)
        response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    return [tuple(item.split("=")) for item in body.split(";") if item]


_OWN = [("Item", "/books/1/", "False"), ("History", "/books/1/history/", "True")]

_LEADING = [("Home", "/", "False"), ("Section", "/books/", "False")]


def _guarded_host(root: Path, declaration: str) -> None:
    _apps(root, {"": "Home"})
    write_page(
        root / "hub" / "pages",
        "[str:section]",
        "from next.pages import page\n"
        "from tests.support.access import note_call\n\n"
        f"{declaration}\n\n\n@page.metadata\n"
        "def meta() -> dict:\n"
        "    return {'breadcrumb': note_call('Section')}\n",
        body=_SHOW,
    )


@pytest.mark.django_db()
class TestCrumbsAcrossMounts:
    """A mounted tree leads with the crumbs of the page its prefix passes through."""

    @pytest.mark.parametrize(
        ("host", "leading"),
        [
            pytest.param(
                {"": "Home", "[str:section]": "Section"},
                [("Home", "/", "False"), ("Section", "/books/", "False")],
                id="deepest-host",
            ),
            pytest.param({"": "Home"}, [("Home", "/", "False")], id="root-host"),
            pytest.param({"elsewhere": "Elsewhere"}, [], id="no-host"),
        ],
    )
    def test_the_host_crumbs_lead(self, tmp_path: Path, host, leading) -> None:
        _apps(tmp_path, host)
        assert _crumbs(tmp_path, "/books/1/history/") == [*leading, *_OWN]

    def test_under_a_script_prefix(self, tmp_path: Path) -> None:
        _apps(tmp_path, {"": "Home", "[str:section]": "Section"})
        with override_script_prefix("/app/"):
            crumbs = _crumbs(tmp_path, "/books/1/history/")
        assert [url for _label, url, _current in crumbs] == [
            "/app/",
            "/app/books/",
            "/app/books/1/",
            "/app/books/1/history/",
        ]

    def test_a_host_crumb_from_a_metadata_callable(self, tmp_path: Path) -> None:
        _apps(tmp_path, {"": "Home"})
        write_page(
            tmp_path / "hub" / "pages",
            "[str:section]",
            "from next.pages import page\n\n\n@page.metadata\n"
            "def meta(section: str) -> dict:\n"
            "    return {'breadcrumb': section.title()}\n",
            body=_SHOW,
        )
        assert _crumbs(tmp_path, "/books/1/history/") == [
            ("Home", "/", "False"),
            ("Books", "/books/", "False"),
            *_OWN,
        ]


@pytest.fixture()
def _no_calls():
    CONTEXT_CALLS.clear()
    yield
    CONTEXT_CALLS.clear()


@pytest.mark.django_db()
@pytest.mark.usefixtures("_no_calls")
class TestCrumbsOfAGuardedHost:
    """A host page the request may not reach leads with no crumbs and runs nothing."""

    @pytest.mark.parametrize(
        ("kind", "leading", "calls"),
        [
            pytest.param("anonymous", [], [], id="anonymous"),
            pytest.param("user", _LEADING, ["Section"], id="user"),
        ],
    )
    def test_a_login(self, tmp_path: Path, kind: str, leading, calls) -> None:
        _guarded_host(tmp_path, "page.login_required()")
        crumbs = _crumbs(tmp_path, "/books/1/history/", signed_in=kind == "user")
        assert crumbs == [*leading, *_OWN]
        assert calls == CONTEXT_CALLS

    @pytest.mark.parametrize(
        ("codenames", "leading"),
        [
            pytest.param((), [], id="refused"),
            pytest.param(("view_user",), _LEADING, id="held"),
        ],
    )
    def test_a_permission(self, tmp_path: Path, codenames, leading) -> None:
        _guarded_host(tmp_path, 'page.permission_required("auth.view_user")')
        crumbs = _crumbs(tmp_path, "/books/1/history/", *codenames, signed_in=True)
        assert crumbs == [*leading, *_OWN]

    def test_a_permission_on_an_object_adds_no_crumb(self, tmp_path: Path) -> None:
        _guarded_host(
            tmp_path,
            "from django.contrib.auth.models import Group\n"
            "from next.deps import DObject\n"
            'page.permission_required("auth.view_user", obj=DObject[Group])',
        )
        crumbs = _crumbs(tmp_path, "/books/1/history/", "view_user", signed_in=True)
        assert crumbs == _OWN
        assert CONTEXT_CALLS == []

    def test_a_broken_host_chain_adds_no_crumb(self, tmp_path: Path) -> None:
        _guarded_host(tmp_path, "")
        host = tmp_path / "hub" / "pages" / "[str:section]"
        write_proxy(host, "raise RuntimeError('boom')\n")
        assert _crumbs(tmp_path, "/books/1/history/", signed_in=True) == _OWN
        assert CONTEXT_CALLS == []

    def test_the_login_middleware_closes_a_host_declaring_nothing(
        self, tmp_path: Path
    ) -> None:
        _guarded_host(tmp_path, "")
        write_page(
            tmp_path / "audit" / "pages",
            "history",
            "from next.pages import page\n\npage.public()\n"
            "metadata = {'breadcrumb': 'History'}\n",
            body=_SHOW,
        )
        with login_required_middleware():
            crumbs = _crumbs(tmp_path, "/books/1/history/")
        assert crumbs == [("Item", "/books/1/", "False"), _OWN[1]]


@pytest.mark.django_db()
@pytest.mark.usefixtures("_no_calls")
class TestCrumbsInTheGraph:
    """The graph builds the host crumbs only for a trail it lists itself."""

    @pytest.mark.parametrize(
        ("jsonld", "calls"),
        [
            pytest.param("[]", ["Section"], id="listed"),
            pytest.param(
                "[{'@type': 'BreadcrumbList', 'name': 'own'}]", [], id="declared"
            ),
        ],
    )
    def test_the_host_folds_once_needed(self, tmp_path: Path, jsonld, calls) -> None:
        _guarded_host(tmp_path, "")
        write_page(
            tmp_path / "audit" / "pages",
            "",
            f"metadata = {{'breadcrumb': 'Item', 'jsonld': {jsonld}}}\n",
            body="{% metadata %}",
        )
        with installed_apps(tmp_path, "hub", "audit", urlconf=NAMESPACED_URLCONF):
            response = Client().get("/books/1/")
        assert response.status_code == 200
        assert calls == CONTEXT_CALLS


class TestHostLookup:
    """The host of a prefix is a page view the URLconf routes, else none."""

    def test_an_unrouted_url_has_no_page(self) -> None:
        assert _page_match("/no/such/page/", NAMESPACED_URLCONF) is None

    def test_a_prefix_kwarg_the_match_lacks_skips_the_trail(self) -> None:
        match = ResolverMatch(handler_declared_here, (), {}, namespaces=["next"])
        found = _host_crumbs(
            match, _Mount("[int:pk]", {}, ""), "/1/", NAMESPACED_URLCONF, lambda *_a: ()
        )
        assert found == ()


class _MountedView:
    next_route_prefix = "library"
    next_route_trail = "outline"

    def __call__(self) -> None:
        """Stand in for a page view of a mounted tree."""


class TestMountBase:
    """The route before a mounted tree is its base unless a regex matched it."""

    @pytest.mark.parametrize(
        ("outer", "base"),
        [("admin/", "admin/"), ("^admin/", None), ("(?P<org>[a-z]+)/", None)],
        ids=["route", "anchored-regex", "group-regex"],
    )
    def test_the_base(self, outer: str, base: str | None) -> None:
        own = _crumb_route("outline", "page_{name}").route
        match = ResolverMatch(_MountedView(), (), {}, route=f"{outer}{own}")
        assert _mount_of(match, "page_{name}").base == base


def _generic_host(root: Path) -> str:
    write_app(root, "hub", {}, mounts={"pages": ""})
    hub = root / "hub" / "pages"
    _crumb_page(hub, "", "Dashboard")
    _crumb_page(hub, "[str:app_label]/[str:model_name]", "Books")
    write_app(root, "library", {}, mounts={"pages": "library/book/<int:pk>/"})
    _crumb_page(root / "library" / "pages", "outline", "Outline")
    return write_urlconf(root, "admin_urls", 'path("admin/", include("next.urls"))')


class _RefusingConverter:
    regex = "[0-9]+"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: object) -> str:
        raise ValueError(value)


@pytest.mark.django_db()
class TestCrumbsByUrl:
    """A prefix trail no page name answers finds its host by the URL it builds."""

    def test_a_generic_host_page_leads(self, tmp_path: Path) -> None:
        urlconf = _generic_host(tmp_path)
        with installed_apps(tmp_path, "hub", "library", urlconf=urlconf):
            response = Client().get("/admin/library/book/1/outline/")
        assert response.status_code == 200
        body = response.content.decode()
        assert [tuple(item.split("=")) for item in body.split(";") if item] == [
            ("Dashboard", "/admin/", "False"),
            ("Books", "/admin/library/book/", "False"),
            ("Outline", "/admin/library/book/1/outline/", "True"),
        ]

    @pytest.mark.parametrize(
        ("route", "kwargs", "expected"),
        [
            pytest.param("a/<int:pk>/", {"pk": 7}, "a/7/", id="int"),
            pytest.param("<slug>/", {"slug": "a b"}, "a%20b/", id="quoted"),
            pytest.param("<nope:pk>/", {"pk": 7}, None, id="unknown-converter"),
            pytest.param("<int:pk>/", {}, None, id="missing-kwarg"),
        ],
    )
    def test_the_filled_route(self, route: str, kwargs, expected) -> None:
        assert _filled_route(route, kwargs) == expected

    def test_a_value_the_converter_refuses_builds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "next.pages.metadata.resolve.get_converters",
            lambda: {"odd": _RefusingConverter()},
        )
        assert _filled_route("<odd:pk>/", {"pk": 2}) is None

    def test_a_route_outside_the_matched_one_builds_nothing(self) -> None:
        match = ResolverMatch(handler_declared_here, (), {}, route="x/")
        assert _host_url("nowhere", match, "page_{name}", NAMESPACED_URLCONF, None) is (
            None
        )
