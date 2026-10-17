from pathlib import Path

import pytest

from next.permissions.endpoints import (
    Endpoint,
    action_endpoint,
    endpoints,
    page_endpoint,
    resolve_page,
)
from next.permissions.markers import AccessRule, Requirement
from next.urls.routes import collect_routes
from tests.support import isolated_form_registries, routed, write_page
from tests.support.access import (
    COMPONENTS,
    login_required_middleware,
    write_access_project,
)


_FILE = Path("/tree/page.py")

_PROXY = Path("/tree/proxy.py")


@pytest.fixture(autouse=True)
def _form_registries():
    with isolated_form_registries():
        yield


@pytest.fixture()
def project(tmp_path) -> Path:
    root = write_access_project(tmp_path / "pages")
    with routed(root, COMPONENT_BACKENDS=COMPONENTS):
        yield root


def _summary(found: tuple[Endpoint, ...], root: Path) -> list[tuple[str, str, str]]:
    return [
        (endpoint.kind, endpoint.name, endpoint.access)
        for endpoint in found
        if endpoint.file.is_relative_to(root)
    ]


class TestEndpointWords:
    """The access column spells a rule the way the tables print it."""

    @pytest.mark.parametrize(
        ("endpoint", "access", "sources"),
        [
            (Endpoint("page", "/", _FILE, AccessRule()), "-", ()),
            (
                Endpoint("page", "/", _FILE, AccessRule(), closed=True),
                "login (middleware)",
                (),
            ),
            (
                Endpoint("page", "/", _FILE, AccessRule(public=True, sources=(_FILE,))),
                "public",
                (_FILE,),
            ),
            (
                Endpoint("page", "/", _FILE, AccessRule(), broken=_PROXY),
                "broken",
                (_PROXY,),
            ),
            (
                Endpoint(
                    "page",
                    "/",
                    _FILE,
                    AccessRule(
                        (
                            Requirement("login", _PROXY),
                            Requirement("permission", _FILE, ("a.x", "a.y"), "note"),
                            Requirement("permission", _PROXY, ("a.z",)),
                        ),
                        public=True,
                    ),
                ),
                "login, perm a.x on note, perm a.y on note, perm a.z",
                (_PROXY, _FILE),
            ),
            (
                Endpoint(
                    "action",
                    "save",
                    _FILE,
                    AccessRule(
                        (
                            Requirement("login", _PROXY),
                            Requirement("permission", _PROXY, ("a.x",)),
                            Requirement("login", _FILE),
                            Requirement("permission", _FILE, ("a.x", "a.y")),
                        )
                    ),
                ),
                "login, perm a.x, perm a.y",
                (_PROXY, _FILE),
            ),
        ],
        ids=["open", "middleware", "public", "broken", "requirements", "repeated"],
    )
    def test_access_and_sources(self, endpoint, access, sources) -> None:
        assert (endpoint.access, endpoint.sources) == (access, sources)


class TestEndpoints:
    """Every page, action and declaring component of the project, read statically."""

    def test_the_whole_project(self, project) -> None:
        assert _summary(endpoints(), project) == [
            ("page", "/", "public"),
            ("page", "/broken/", "broken"),
            (
                "page",
                "/notes/<int:group>/",
                "login, perm auth.change_group on auth.Group",
            ),
            ("page", "/open/", "-"),
            ("action", "guarded_ping", "login, perm auth.add_group"),
            ("action", "save_note", "login, perm auth.change_group on auth.Group"),
            ("action", "open_ping", "-"),
            ("action", "login_ping", "login"),
            ("component", "card", "login"),
        ]

    def test_one_page_keeps_its_actions_and_its_components(self, project) -> None:
        found = endpoints(project / "page.py")

        assert _summary(found, project) == [
            ("page", "/", "public"),
            ("action", "guarded_ping", "login, perm auth.add_group"),
            ("component", "card", "login"),
        ]

    def test_under_the_middleware_an_open_endpoint_is_closed(self, project) -> None:
        with login_required_middleware():
            found = endpoints(project / "open" / "page.py")

        assert _summary(found, project) == [
            ("page", "/open/", "login (middleware)"),
            ("action", "open_ping", "login (middleware)"),
            ("action", "login_ping", "login"),
        ]

    def test_the_middleware_leaves_a_public_page_open(self, project) -> None:
        with login_required_middleware():
            [route] = [item for item in collect_routes() if item.url == "/"]
            endpoint = page_endpoint(route)

        assert (endpoint.closed, endpoint.access) == (False, "public")

    def test_an_action_outside_every_tree_answers_to_its_guard(self, project) -> None:
        meta = {"name": "x", "file_path": "/elsewhere/views.py"}

        endpoint = action_endpoint(meta)

        assert (endpoint.access, endpoint.broken, endpoint.closed) == ("-", None, False)

    def test_a_page_whose_template_breaks_is_marked_broken(self, tmp_path) -> None:
        root = tmp_path / "pages"
        page_path = write_page(root, "bad", 'template = "{% if %}"\n')

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [found] = endpoints(page_path)

        assert (found.kind, found.access, found.sources) == (
            "page",
            "broken",
            (page_path,),
        )


class TestResolvePage:
    """A URL maps to the `page.py` its route serves, and to nothing else."""

    def test_a_page_url(self, project) -> None:
        assert (
            resolve_page("/notes/1/?a=1")
            == project / "notes" / "[int:group]" / "page.py"
        )

    def test_an_empty_url_reads_as_the_root(self, project) -> None:
        assert resolve_page("") == project / "page.py"

    @pytest.mark.parametrize("url", ["/missing/", "/_next/csrf/"], ids=["404", "view"])
    def test_anything_but_a_page_is_none(self, project, url) -> None:
        assert resolve_page(url) is None
