from pathlib import Path

import pytest

from next.forms import ActionGuard, Form
from next.forms.dispatch.permissions import guard_rule
from next.management.endpoints import (
    Endpoint,
    ViewEndpoint,
    action_endpoint,
    endpoints,
    page_endpoint,
    resolve_page,
    resolve_view,
    view_endpoints,
)
from next.permissions import add_perm
from next.permissions.markers import AccessRule, Requirement
from next.urls.routes import collect_routes
from tests.support import owns_group, routed, write_page
from tests.support.access import (
    COMPONENTS,
    login_required_middleware,
    write_access_project,
)


pytestmark = pytest.mark.usefixtures("form_registries")

_FILE = Path("/tree/page.py")

_ABOVE = Path("/page.py")

VIEWS_URLCONF = "tests.permissions.urls_views"

_CARD_ACTION = """
from next.components import component
from next.forms import action

component.permission_required("auth.view_user", obj="account")


@action("card_ping")
def card_ping():
    return None
"""


@pytest.fixture()
def project(tmp_path) -> Path:
    root = write_access_project(tmp_path / "pages")
    with routed(root, COMPONENT_BACKENDS=COMPONENTS):
        yield root


@pytest.fixture()
def views_project(tmp_path) -> Path:
    root = write_access_project(tmp_path / "pages")
    with routed(root, urlconf=VIEWS_URLCONF, COMPONENT_BACKENDS=COMPONENTS):
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
                "login (default)",
                (),
            ),
            (
                Endpoint("page", "/", _FILE, AccessRule(public=True, sources=(_FILE,))),
                "public",
                (_FILE,),
            ),
            (
                Endpoint("page", "/", _FILE, AccessRule(), broken=_ABOVE),
                "broken",
                (_ABOVE,),
            ),
            (
                Endpoint(
                    "page",
                    "/",
                    _FILE,
                    AccessRule(
                        (
                            Requirement("login", _ABOVE),
                            Requirement("permission", _FILE, ("a.x", "a.y"), "note"),
                            Requirement("permission", _ABOVE, ("a.z",)),
                        ),
                        public=True,
                    ),
                ),
                "login, perm a.x on note, perm a.y on note, perm a.z",
                (_ABOVE, _FILE),
            ),
            (
                Endpoint(
                    "action",
                    "save",
                    _FILE,
                    AccessRule(
                        (
                            Requirement("login", _ABOVE),
                            Requirement("permission", _ABOVE, ("a.x",)),
                            Requirement("login", _FILE),
                            Requirement("permission", _FILE, ("a.x", "a.y")),
                        )
                    ),
                ),
                "login, perm a.x, perm a.y",
                (_ABOVE, _FILE),
            ),
        ],
        ids=["open", "default", "public", "broken", "requirements", "repeated"],
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
            ("page", "/open/", "login (default)"),
            ("action", "open_ping", "login (default)"),
            ("action", "login_ping", "login"),
        ]

    def test_under_the_setting_an_open_endpoint_is_closed(self, project) -> None:
        with routed(project, COMPONENT_BACKENDS=COMPONENTS, LOGIN_REQUIRED=True):
            found = endpoints(project / "open" / "page.py")

        assert _summary(found, project)[:2] == [
            ("page", "/open/", "login (default)"),
            ("action", "open_ping", "login (default)"),
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

    def test_an_action_of_a_component_answers_to_the_component(self, tmp_path) -> None:
        root = tmp_path / "pages"
        write_page(root, "", 'template = "{% component \\"card\\" %}"\n')
        card = root / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.djx").write_text("card")
        (card / "component.py").write_text(_CARD_ACTION)

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            found = _summary(endpoints(), root)

        assert ("action", "card_ping", "perm auth.view_user on account") in found

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


@pytest.mark.usefixtures("isolated_permissions")
class TestGuardRule:
    """An action row prints the guard rule dispatch enforces, built in one place."""

    PERMISSIONS = ("auth.change_group", "auth.edit_group")

    def _endpoint(self, **branch: object) -> tuple[Endpoint, dict[str, object]]:
        guard = ActionGuard(permissions=self.PERMISSIONS)
        meta = {"name": "x", "file_path": "/app/forms.py", "guard": guard, **branch}
        return action_endpoint(meta), meta

    def test_a_registered_permission_is_asked_of_the_form(self) -> None:
        add_perm("auth.edit_group", owns_group)

        endpoint, meta = self._endpoint(form_class=Form)

        assert endpoint.access == (
            "login, perm auth.change_group, perm auth.edit_group on form"
        )
        assert endpoint.rule.requirements == guard_rule(meta).requirements

    def test_a_handler_asks_every_permission_up_front(self) -> None:
        add_perm("auth.edit_group", owns_group)

        endpoint, meta = self._endpoint(handler=print)

        assert endpoint.access == "login, perm auth.change_group, perm auth.edit_group"
        assert endpoint.rule.requirements == guard_rule(meta).requirements


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


class TestViewEndpoints:
    """Every view of the URLconf outside the page trees, as the middleware reads it."""

    @pytest.mark.parametrize(
        ("view", "access"),
        [
            (ViewEndpoint("/a/", "a.b", public=True, closed=False), "public"),
            (
                ViewEndpoint("/a/", "a.b", public=False, closed=True),
                "login (middleware)",
            ),
            (ViewEndpoint("/a/", "a.b", public=False, closed=False), "-"),
            (
                ViewEndpoint(
                    "/a/", "a.b", public=True, closed=False, deferred="action"
                ),
                "per action",
            ),
        ],
        ids=["public", "middleware", "open", "deferred"],
    )
    def test_access(self, view, access) -> None:
        assert view.access == access

    def test_the_urlconf_order_without_the_pages(self, views_project) -> None:
        found = view_endpoints()

        assert [(view.route, view.view, view.access) for view in found[:3]] == [
            ("/sw.js", "tests.permissions.urls_views.worker", "-"),
            ("/api/report/", "tests.permissions.urls_views.Report", "public"),
            ("/robots.txt", "tests.permissions.urls_views.robots", "public"),
        ]
        by_route = {view.route: view for view in found}
        assert by_route["/_next/csrf/"].access == "public"
        assert by_route["/_next/form/<str:uid>/"].access == "per action"
        assert not [view for view in found if view.route in {"/", "/open/"}]

    def test_under_the_middleware_an_unmarked_view_is_closed(
        self, views_project
    ) -> None:
        with login_required_middleware():
            worker, report, *_ = view_endpoints()

        assert (worker.closed, worker.access) == (True, "login (middleware)")
        assert (report.closed, report.access) == (False, "public")


class TestResolveView:
    """A URL maps to the view its pattern routes, and to nothing for a page or a 404."""

    def test_a_view_url(self, views_project) -> None:
        assert resolve_view("/sw.js?v=1") == ViewEndpoint(
            "/sw.js", "tests.permissions.urls_views.worker", public=False, closed=False
        )

    def test_a_view_under_an_include(self, views_project) -> None:
        found = resolve_view("/api/report/")

        assert found is not None
        assert (found.route, found.access) == ("/api/report/", "public")

    @pytest.mark.parametrize("url", ["/missing/", "/open/"], ids=["404", "page"])
    def test_anything_but_a_view_is_none(self, views_project, url) -> None:
        assert resolve_view(url) is None
