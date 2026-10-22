from pathlib import Path

import pytest
from django.urls import set_script_prefix

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
    served_zones,
    view_endpoints,
)
from next.permissions import add_perm
from next.permissions.markers import AccessRule, Requirement
from next.urls.routes import collect_routes, with_intercepts
from tests.support import owns_group, routed, write_page
from tests.support.access import (
    COMPONENTS,
    PAGE_HEAD,
    login_required_middleware,
    write_access_project,
)
from tests.support.cases.intercepts import guarded_zone_body, zone_body


pytestmark = pytest.mark.usefixtures("form_registries")

_FILE = Path("/tree/page.py")

_ABOVE = Path("/page.py")

_VIEWS_URLCONF = "tests.permissions.urls_views"

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
    with routed(root, urlconf=_VIEWS_URLCONF, COMPONENT_BACKENDS=COMPONENTS):
        yield root


def _summary(found: tuple[Endpoint, ...], root: Path) -> list[tuple[str, str, str]]:
    return [
        (endpoint.kind, endpoint.name, endpoint.access)
        for endpoint in found
        if endpoint.file.is_relative_to(root)
    ]


def _site(root: Path, body: str, target_body: str = "<p>x</p>") -> tuple[Path, Path]:
    target = write_page(
        root, "feed/photo", f"{PAGE_HEAD}page.public()\n", body=target_body
    )
    rule = write_page(
        root, "feed/(.)photo", f"{PAGE_HEAD}page.login_required()\n", body=body
    )
    return target, rule


def _rows(root: Path):
    return [
        route
        for route in with_intercepts(collect_routes())
        if root in route.file.parents
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


class TestInterceptEndpoints:
    """An intercept row is named by its target and zones and answers to its own chain."""

    def test_the_row_names_its_target_and_zones(self, tmp_path) -> None:
        root = tmp_path / "pages"
        _target, rule = _site(root, zone_body("b", "a"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [_target_route, row] = _rows(root)
            found = page_endpoint(row)

        assert (found.kind, found.name, found.file) == (
            "intercept",
            "/feed/photo/ (b, a)",
            rule.resolve(),
        )
        assert (found.access, found.sources) == ("login", (rule.resolve(),))

    def test_the_target_row_keeps_its_page_kind(self, tmp_path) -> None:
        root = tmp_path / "pages"
        target, _rule = _site(root, zone_body("a"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [route, _row] = _rows(root)
            found = page_endpoint(route)

        assert (found.kind, found.name, found.access) == (
            "page",
            "/feed/photo/",
            "public",
        )
        assert found.file == target.resolve()

    def test_the_whole_project_lists_the_row_after_its_target(self, tmp_path) -> None:
        root = tmp_path / "pages"
        _site(root, zone_body("a"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            found = _summary(endpoints(), root)

        assert found == [
            ("page", "/feed/photo/", "public"),
            ("intercept", "/feed/photo/ (a)", "login"),
        ]

    def test_one_page_keeps_its_intercepts(self, tmp_path) -> None:
        root = tmp_path / "pages"
        target, _rule = _site(root, zone_body("a"))
        write_page(root, "other")

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            found = _summary(endpoints(target.resolve()), root)

        assert found == [
            ("page", "/feed/photo/", "public"),
            ("intercept", "/feed/photo/ (a)", "login"),
        ]

    def test_a_broken_target_marks_its_row_and_not_the_intercept(
        self, tmp_path
    ) -> None:
        root = tmp_path / "pages"
        target, _rule = _site(root, zone_body("a"), target_body="{% if %}")

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            routed_row, intercept_row = endpoints(target.resolve())

        assert (routed_row.kind, routed_row.access, routed_row.broken) == (
            "page",
            "broken",
            target.resolve(),
        )
        assert (intercept_row.kind, intercept_row.access, intercept_row.broken) == (
            "intercept",
            "login",
            None,
        )

    def test_a_healthy_target_marks_nothing(self, tmp_path) -> None:
        root = tmp_path / "pages"
        target, _rule = _site(root, zone_body("a"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            found = endpoints(target.resolve())

        assert [row.broken for row in found] == [None, None]


class TestServedZones:
    """The zones column lists the standalone zones an intercept serves in order."""

    @pytest.mark.parametrize(
        ("body", "zones"),
        [
            (zone_body("b", "a"), "b, a"),
            (zone_body("a") + guarded_zone_body("hidden"), "a"),
            (guarded_zone_body("hidden"), "-"),
            ("<p>x</p>", "-"),
            ("{% if %}", "-"),
        ],
        ids=["declaration_order", "guarded_filtered", "only_guarded", "none", "broken"],
    )
    def test_the_zones_of_an_intercept_row(self, tmp_path, body, zones) -> None:
        root = tmp_path / "pages"
        _site(root, body)

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [_target_route, row] = _rows(root)

            assert served_zones(row) == zones

    def test_a_routed_row_reads_blank(self, tmp_path) -> None:
        root = tmp_path / "pages"
        _site(root, zone_body("a"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [route, _row] = _rows(root)

            assert served_zones(route) == ""


@pytest.mark.usefixtures("isolated_permissions")
class TestGuardRule:
    """An action row prints the guard rule dispatch enforces, built in one place."""

    PERMISSIONS = ("auth.change_group", "auth.edit_group")

    @pytest.mark.parametrize(
        ("branch", "access"),
        [
            pytest.param(
                {"form_class": Form},
                "login, perm auth.change_group, perm auth.edit_group on form",
                id="form",
            ),
            pytest.param(
                {"handler": print},
                "login, perm auth.change_group, perm auth.edit_group",
                id="handler",
            ),
        ],
    )
    def test_a_registered_permission_is_asked_where_the_branch_says(
        self, branch: dict[str, object], access: str
    ) -> None:
        add_perm("auth.edit_group", owns_group)
        guard = ActionGuard(permissions=self.PERMISSIONS)
        meta = {"name": "x", "file_path": "/app/forms.py", "guard": guard, **branch}

        endpoint = action_endpoint(meta)

        assert endpoint.access == access
        assert endpoint.rule.requirements == guard_rule(meta).requirements


class TestResolvePage:
    """A URL maps to the `page.py` its route serves, and to nothing else."""

    def test_a_page_url(self, project) -> None:
        assert (
            resolve_page("/notes/1/?a=1")
            == project / "notes" / "[int:group]" / "page.py"
        )

    @pytest.mark.parametrize(
        "url", ["notes/1/", "/app/notes/1/"], ids=["bare", "prefixed"]
    )
    def test_a_path_without_slash_or_with_the_script_prefix(self, project, url) -> None:
        set_script_prefix("/app/")
        try:
            found = resolve_page(url)
        finally:
            set_script_prefix("/")
        assert found == project / "notes" / "[int:group]" / "page.py"

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
            (
                ViewEndpoint("/a/", "a.b", exempt=True, closed=False, open=True),
                "public",
            ),
            (ViewEndpoint("/a/", "a.b", exempt=True, closed=False), "no login"),
            (
                ViewEndpoint("/a/", "a.b", exempt=False, closed=True),
                "login (middleware)",
            ),
            (ViewEndpoint("/a/", "a.b", exempt=False, closed=False), "?"),
            (
                ViewEndpoint(
                    "/a/", "a.b", exempt=True, closed=False, deferred="action"
                ),
                "per action",
            ),
        ],
        ids=["public", "no-login", "middleware", "unread", "deferred"],
    )
    def test_access(self, view, access) -> None:
        assert view.access == access

    def test_the_urlconf_order_without_the_pages(self, views_project) -> None:
        found = view_endpoints()

        assert [(view.route, view.view, view.access) for view in found[:3]] == [
            ("/sw.js", "tests.permissions.urls_views.worker", "?"),
            ("/api/report/", "tests.permissions.urls_views.Report", "no login"),
            ("/robots.txt", "tests.permissions.urls_views.robots", "no login"),
        ]
        by_route = _views_by_route()
        assert by_route["/secret/"].access == "no login"
        assert by_route["/_next/csrf/"].access == "public"
        assert by_route["/_next/form/<str:uid>/"].access == "per action"
        assert not [view for view in found if view.route in {"/", "/open/"}]

    def test_under_the_middleware_an_unmarked_view_is_closed(
        self, views_project
    ) -> None:
        with login_required_middleware():
            worker, report, *_ = view_endpoints()

        assert (worker.closed, worker.access) == (True, "login (middleware)")
        assert (report.closed, report.access) == (False, "no login")


_UNMARKED_VIEWS = {
    "/account/": "account",
    "/staff/": "staff",
    "/members/": "Members",
    "/gate/": "Gate",
}

_UNMARKED_ROUTES = pytest.mark.parametrize(
    "route", list(_UNMARKED_VIEWS), ids=["login", "permission", "mixin", "dispatch"]
)


def _views_by_route() -> dict[str, ViewEndpoint]:
    return {view.route: view for view in view_endpoints()}


class TestUnmarkedViews:
    """A view the framework reads no mark on shows `?`, whatever guards it inside."""

    @_UNMARKED_ROUTES
    def test_an_unmarked_view_is_unread(self, views_project, route) -> None:
        by_route = _views_by_route()

        assert by_route[route].view.rpartition(".")[2] == _UNMARKED_VIEWS[route]
        assert by_route[route].access == "?"

    @_UNMARKED_ROUTES
    def test_the_middleware_reads_at_least_a_login(self, views_project, route) -> None:
        with login_required_middleware():
            by_route = _views_by_route()

        assert by_route[route].access == "login (middleware)"


class TestResolveView:
    """A URL maps to the view its pattern routes, and to nothing for a page or a 404."""

    def test_a_view_url(self, views_project) -> None:
        assert resolve_view("/sw.js?v=1") == ViewEndpoint(
            "/sw.js", "tests.permissions.urls_views.worker", exempt=False, closed=False
        )

    def test_a_view_under_an_include(self, views_project) -> None:
        found = resolve_view("/api/report/")

        assert found is not None
        assert (found.route, found.access) == ("/api/report/", "no login")

    @pytest.mark.parametrize("url", ["/missing/", "/open/"], ids=["404", "page"])
    def test_anything_but_a_view_is_none(self, views_project, url) -> None:
        assert resolve_view(url) is None
