from collections.abc import Generator
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext

from next.deps import DObject, UnboundObjectError
from next.deps.objects import SCOPE_DEPENDENCY
from next.pages import page
from next.pages.access import (
    Gate,
    enforce,
    forget_page_access,
    owner_access,
    page_access,
    refusal,
    refuse_broken,
    url_lookup,
)
from next.pages.errors import PageModuleImportError
from next.pages.loaders import load_python_module_memo
from next.permissions import add_perm
from next.permissions.access import declarations
from next.permissions.markers import OPEN, AccessRule, Requirement
from tests.support import (
    PermissionHolder,
    bound_dependency,
    installed_apps,
    routed,
    touch_later,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import (
    PAGE_HEAD,
    client_as,
    login_required_middleware,
    page_source,
    sign_in,
    write_guard,
)


pytestmark = pytest.mark.django_db

_LOGIN_INHERIT = "page.login_required(inherit=True)\n"
_VIEW_INHERIT = 'page.permission_required("auth.view_group", inherit=True)\n'

_LIFT_URLS = ("/t/a/", "/t/a/kid/")


def _three_levels(root: Path) -> Path:
    write_guard(root)
    write_guard(root / "team", _LOGIN_INHERIT)
    return write_page(root, "team/notes", page_source())


class TestPageModule:
    """A `page.py` declares access for its page, and reloads by its mtime."""

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_rewritten_page_applies_before_the_next_request(
        self, root, client
    ) -> None:
        declaring = write_page(root, "", page_source())

        with routed(root):
            first = client.get("/")
            declaring.write_text(page_source("page.login_required()\n"))
            touch_later(declaring)
            second = client.get("/")

        assert (first.status_code, second.status_code) == (200, 302)


class TestInheritedAccess:
    """What a `page.py` declares with `inherit=True` binds every page below it."""

    def test_a_requirement_at_the_middle_level_binds_the_leaf(self, root) -> None:
        page_path = _three_levels(root)

        with routed(root):
            rule = page_access(page_path).rule

        assert [(item.kind, item.source) for item in rule.requirements] == [
            ("login", root / "team" / "page.py")
        ]

    @pytest.mark.parametrize(
        ("declaration", "statuses"),
        [
            pytest.param("page.login_required()\n", [302, 200, 200], id="own"),
            pytest.param(_LOGIN_INHERIT, [302, 302, 302], id="inherit"),
            pytest.param(
                'page.permission_required("auth.view_group")\n',
                [302, 200, 200],
                id="own-permission",
            ),
            pytest.param(_VIEW_INHERIT, [302, 302, 302], id="inherit-permission"),
        ],
    )
    def test_a_declaration_reaches_the_child_and_the_grandchild_under_inherit(
        self, root, client, declaration, statuses
    ) -> None:
        write_page(root, "a", page_source(declaration))
        write_page(root, "a/b", page_source())
        write_page(root, "a/b/[int:n]", page_source())

        with routed(root):
            answered = [
                client.get(url).status_code for url in ("/a/", "/a/b/", "/a/b/7/")
            ]

        assert answered == statuses

    def test_a_guard_page_serves_no_url_under_a_layout(self, root, client) -> None:
        (root / "layout.djx").write_text("<main>{% template %}</main>")
        write_guard(root / "a", _LOGIN_INHERIT)
        write_page(root, "a/b", page_source())

        with routed(root):
            guard = client.get("/a/")
            below = client.get("/a/b/")

        assert (guard.status_code, below.status_code) == (404, 302)

    @pytest.mark.parametrize(
        ("source", "body"),
        [
            pytest.param("def render(request):\n    return 'ok'\n", None, id="render"),
            pytest.param("", "ok", id="template-djx"),
        ],
    )
    def test_a_page_with_a_body_and_an_inherited_guard_serves_itself(
        self, root, client, source, body
    ) -> None:
        guard = f"{PAGE_HEAD}page.login_required(inherit=True)\n{source}"
        write_page(root, "a", guard, body=body)
        write_page(root, "a/b", page_source())

        with routed(root):
            own = client.get("/a/")
            below = client.get("/a/b/")

        assert (own.status_code, below.status_code) == (302, 302)

    def test_three_levels_join_root_first(
        self, root, client, isolated_permissions
    ) -> None:
        write_guard(root, _LOGIN_INHERIT)
        write_guard(root / "a", _VIEW_INHERIT)
        write_page(
            root, "a/b", page_source('page.permission_required("auth.change_group")\n')
        )
        sign_in(client, "ada", "view_group")

        with routed(root):
            rule = page_access(root / "a" / "b" / "page.py").rule
            refused = client.get("/a/b/")

        assert [item.perms for item in rule.requirements] == [
            (),
            ("auth.view_group",),
            ("auth.change_group",),
        ]
        assert refused.status_code == 403

    @pytest.mark.parametrize(
        ("holder", "status", "location"),
        [(True, 200, None), (False, 302, "/accounts/login/?next=/a/%3Fx%3D1")],
        ids=["holder", "anonymous"],
    )
    def test_the_inherited_permission_decides(
        self, root, client, holder, status, location
    ) -> None:
        write_guard(root, _VIEW_INHERIT)
        write_page(root, "a", page_source())
        if holder:
            sign_in(client, "ada", "view_group")

        with routed(root):
            response = client.get("/a/?x=1")

        assert (response.status_code, response.get("Location")) == (status, location)

    def test_a_sibling_directory_stays_open(self, root, client) -> None:
        write_page(root, "closed", page_source(_LOGIN_INHERIT))
        write_page(root, "open", page_source())

        with routed(root):
            closed = client.get("/closed/")
            opened = client.get("/open/")

        assert (closed.status_code, opened.status_code) == (302, 200)

    def test_a_guard_above_the_tree_root_binds_nothing(self, tmp_path, client) -> None:
        write_guard(tmp_path, _LOGIN_INHERIT)
        root = tmp_path / "pages"
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")
            rule = page_access(root / "a" / "page.py").rule

        assert response.status_code == 200
        assert rule.requirements == ()


class TestAccessThroughGroups:
    """A group `page.py` binds the pages inside the group and none outside it."""

    def test_an_inherited_guard_in_a_group_binds_the_pages_below_it(
        self, root, client
    ) -> None:
        write_guard(root / "(app)", _LOGIN_INHERIT)
        write_page(root, "(app)/dash", page_source())
        write_page(root, "open", page_source())

        with routed(root):
            inside = client.get("/dash/")
            outside = client.get("/open/")

        assert (inside.status_code, outside.status_code) == (302, 200)

    def test_the_rule_names_the_group_file_as_its_source(self, root) -> None:
        guard = write_guard(root / "(app)", _LOGIN_INHERIT)
        page_path = write_page(root, "(app)/dash", page_source())

        with routed(root):
            rule = page_access(page_path).rule

        assert [(item.kind, item.source) for item in rule.requirements] == [
            ("login", guard)
        ]


class TestMountedTrees:
    """A `pages()` tree starts its chain at its own root, never at the host."""

    @pytest.fixture()
    def mounted(self, tmp_path) -> Path:
        app = write_app(tmp_path, "wallets", {"pages": ()})
        write_guard(app, _LOGIN_INHERIT)
        write_guard(app / "pages")
        write_page(app / "pages", "ops", page_source(template="w"))
        write_guard(tmp_path / "site", _LOGIN_INHERIT)
        write_page(tmp_path / "site", "contact", page_source())
        urlconf = write_urlconf(
            tmp_path,
            "mounted_urls",
            'path("w/<int:wallet>/", pages("wallets"))',
            'path("", include("next.urls"))',
        )
        with installed_apps(
            tmp_path, "wallets", urlconf=urlconf, dirs=[tmp_path / "site"]
        ):
            yield app

    def test_the_mounted_page_reads_its_own_tree_alone(self, mounted, client) -> None:
        response = client.get("/w/1/ops/")

        assert (response.status_code, response.content) == (200, b"w")

    def test_the_host_tree_keeps_its_own_guard(self, mounted, client) -> None:
        assert client.get("/contact/").status_code == 302


class TestLiftedRequirements:
    """`public()` lifts the requirements above it, and a deeper page binds again."""

    @pytest.fixture()
    def booking(self, root) -> Path:
        booking = root / "booking"
        write_guard(booking, _LOGIN_INHERIT)
        write_page(root, "booking/[int:booking]", page_source())
        write_page(
            root,
            "booking/[int:booking]/receipt",
            page_source("page.public(inherit=True)\n"),
        )
        write_page(
            root, "booking/[int:booking]/receipt/admin", page_source(_LOGIN_INHERIT)
        )
        return booking

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            pytest.param("/booking/1/", 302, id="guarded-parent"),
            pytest.param("/booking/1/receipt/", 200, id="lifted"),
            pytest.param("/booking/1/receipt/admin/", 302, id="guarded-again"),
        ],
    )
    def test_an_anonymous_request(self, booking, client, url, status) -> None:
        with routed(booking.parent):
            response = client.get(url)

        assert response.status_code == status

    def test_the_lifted_rule_names_the_opening_file(self, booking) -> None:
        receipt = booking / "[int:booking]" / "receipt"

        with routed(booking.parent):
            lifted = page_access(receipt / "page.py").rule
            again = page_access(receipt / "admin" / "page.py").rule

        assert (lifted.public, lifted.requirements) == (True, ())
        assert lifted.sources == (receipt / "page.py",)
        assert again.public is False
        assert [item.source for item in again.requirements] == [
            receipt / "admin" / "page.py"
        ]

    def test_the_view_of_a_lifted_page_skips_the_middleware(
        self, booking, client
    ) -> None:
        with routed(booking.parent), login_required_middleware():
            lifted = client.get("/booking/1/receipt/")
            guarded = client.get("/booking/1/")
            flag = lifted.resolver_match.func.login_required

        assert (lifted.status_code, guarded.status_code, flag) == (200, 302, False)

    def test_a_public_page_records_itself_as_the_source(self, root) -> None:
        page_path = write_page(root, "a", page_source("page.public()\n"))

        with routed(root):
            rule = page_access(page_path).rule

        assert (rule.public, rule.requirements, rule.sources) == (
            True,
            (),
            (page_path,),
        )

    def test_a_public_page_lifts_for_itself_alone(self, root, client) -> None:
        write_guard(root, _LOGIN_INHERIT)
        page_path = write_page(root, "a", page_source("page.public()\n"))
        write_page(root, "a/b", page_source())

        with routed(root):
            opened = client.get("/a/")
            below = client.get("/a/b/")
            rule = page_access(page_path).rule

        assert (opened.status_code, below.status_code) == (200, 302)
        assert rule.sources == (page_path,)

    @pytest.mark.parametrize(
        ("declaration", "statuses"),
        [
            pytest.param("", [200, 200, 200], id="nothing"),
            pytest.param("page.login_required()\n", [200, 302, 200], id="own"),
            pytest.param(_LOGIN_INHERIT, [200, 302, 302], id="inherit"),
            pytest.param(
                'page.permission_required("auth.view_group")\n',
                [200, 302, 200],
                id="own-permission",
            ),
            pytest.param(_VIEW_INHERIT, [200, 302, 302], id="inherit-permission"),
        ],
    )
    def test_a_page_below_a_lifted_subtree_binds_again(
        self, root, client, declaration, statuses
    ) -> None:
        write_guard(root, _LOGIN_INHERIT)
        write_page(root, "a", page_source("page.public(inherit=True)\n"))
        write_page(root, "a/b", page_source(declaration))
        write_page(root, "a/b/c", page_source())

        with routed(root):
            answered = [
                client.get(url).status_code for url in ("/a/", "/a/b/", "/a/b/c/")
            ]

        assert answered == statuses

    def test_a_requirement_beside_public_still_binds(self, root, client) -> None:
        write_guard(
            root, "page.public(inherit=True)\npage.login_required(inherit=True)\n"
        )
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")
            rule = page_access(root / "a" / "page.py").rule

        assert response.status_code == 302
        assert rule.public is False

    @pytest.mark.parametrize(
        "declaration",
        [
            "page.public(inherit=True)\npage.login_required()\n",
            'page.public(inherit=True)\npage.permission_required("auth.view_user")\n',
            "page.public()\npage.login_required(inherit=True)\n",
            "page.public(inherit=True)\npage.login_required(inherit=True)\n",
        ],
        ids=["lift-beside-own", "lift-beside-permission", "own-beside-inherit", "both"],
    )
    def test_public_beside_a_requirement_lifts_nothing_below(
        self, root, declaration
    ) -> None:
        write_guard(
            root / "t", 'page.permission_required("auth.add_group", inherit=True)\n'
        )
        write_page(root, "t/a", page_source(declaration))
        write_page(root, "t/a/kid", page_source())

        with routed(root):
            clients = {kind: client_as(kind) for kind in ("anonymous", "visitor")}
            answered = {
                kind: [each.get(url).status_code for url in _LIFT_URLS]
                for kind, each in clients.items()
            }
            below = page_access(root / "t" / "a" / "kid" / "page.py").rule

        assert answered == {"anonymous": [302, 302], "visitor": [403, 403]}
        assert below.public is False
        assert below.requirements[0].perms == ("auth.add_group",)

    def test_a_second_public_keeps_the_first_as_the_source(self, root) -> None:
        write_guard(root, _LOGIN_INHERIT)
        write_guard(root / "a", "page.public(inherit=True)\n")
        page_path = write_page(root, "a/b", page_source("page.public()\n"))

        with routed(root):
            rule = page_access(page_path).rule

        assert rule.sources == (root / "a" / "page.py",)

    def test_the_view_carries_the_public_flag(self, root) -> None:
        write_page(root, "open", page_source("page.public()\n"))
        write_page(root, "shut", page_source())

        with routed(root):
            client = Client()
            opened = client.get("/open/").resolver_match.func
            shut = client.get("/shut/").resolver_match.func

        assert (opened.login_required, shut.login_required) == (False, True)


_GROUP_PAGE = """
from django.contrib.auth.models import Group

from next.deps import DObject
from next.pages import page
from tests.support.access import note_call

page.permission_required("auth.edit_group", obj=DObject[Group])


@page.context("shown")
def shown(group: DObject[Group]) -> str:
    return note_call(group.name)

template = "{{ shown }}"
"""


@pytest.fixture()
def group_scope(scoped_models) -> Generator[None, None, None]:
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "t"):
        yield


def _group_queries(queries: CaptureQueriesContext) -> list[str]:
    return [
        item["sql"]
        for item in queries.captured_queries
        if item["sql"].startswith('SELECT "auth_group"')
    ]


@pytest.mark.usefixtures(
    "group_scope", "isolated_permissions", "object_permission_backend"
)
class TestObjectRequirements:
    """`obj=DObject[Model]` loads the row by the URL kwarg named after the model."""

    @pytest.fixture()
    def owned(self, root) -> Path:
        add_perm("auth.edit_group", lambda user, obj: obj.name == f"t:{user.username}")
        return write_page(root, "edit/[int:group]", _GROUP_PAGE)

    def test_the_owner_passes_and_the_row_loads_once(self, owned, client) -> None:
        group = Group.objects.create(name="t:ada")
        sign_in(client, "ada")

        with routed(owned.parents[2]), CaptureQueriesContext(connection) as queries:
            response = client.get(f"/edit/{group.pk}/")

        assert response.content == b"t:ada"
        assert len(_group_queries(queries)) == 1

    def test_another_user_is_refused(self, owned, client) -> None:
        group = Group.objects.create(name="t:ada")
        sign_in(client, "bob")

        with routed(owned.parents[2]):
            response = client.get(f"/edit/{group.pk}/")

        assert response.status_code == 403

    @pytest.mark.parametrize(
        "name", [pytest.param("other:ada", id="hidden"), pytest.param(None, id="gone")]
    )
    def test_a_row_the_scope_does_not_see_is_a_404(self, owned, client, name) -> None:
        pk = 999 if name is None else Group.objects.create(name=name).pk
        sign_in(client, "ada")

        with routed(owned.parents[2]):
            response = client.get(f"/edit/{pk}/")

        assert response.status_code == 404

    @pytest.mark.parametrize(
        ("username", "status"), [("ada", 200), ("bob", 403)], ids=["owner", "other"]
    )
    def test_an_inherited_object_rule_binds_the_pages_under_the_kwarg(
        self, root, client, username, status
    ) -> None:
        add_perm("auth.edit_group", lambda user, obj: obj.name == f"t:{user.username}")
        write_guard(
            root / "edit" / "[int:group]",
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n\n"
            'page.permission_required("auth.edit_group", obj=DObject[Group], '
            "inherit=True)\n",
        )
        write_page(root, "edit/[int:group]/members", page_source())
        group = Group.objects.create(name="t:ada")
        sign_in(client, username)

        with routed(root), CaptureQueriesContext(connection) as queries:
            response = client.get(f"/edit/{group.pk}/members/")

        assert response.status_code == status
        assert len(_group_queries(queries)) == 1

    def test_a_permission_without_an_object_is_checked_first(
        self, root, client
    ) -> None:
        write_guard(root, _VIEW_INHERIT)
        write_page(root, "edit/[int:group]", _GROUP_PAGE)
        group = Group.objects.create(name="t:ada")
        sign_in(client, "ada")

        with routed(root), CaptureQueriesContext(connection) as queries:
            response = client.get(f"/edit/{group.pk}/")

        assert response.status_code == 403
        assert _group_queries(queries) == []

    def test_a_route_without_the_kwarg_raises(self, root, client) -> None:
        write_page(root, "edit", _GROUP_PAGE)
        sign_in(client, "ada")

        with routed(root), pytest.raises(UnboundObjectError, match='"group"'):
            client.get("/edit/")

    def test_a_render_view_checks_the_row(self, root, client) -> None:
        add_perm("auth.edit_group", lambda _user, obj: obj.name == "t:one")
        write_page(
            root,
            "r/[int:group]",
            "from django.contrib.auth.models import Group\n"
            "from django.http import HttpResponse\n\n"
            "from next.deps import DObject\n"
            "from next.pages import page\n\n"
            'page.permission_required("auth.edit_group", obj=DObject[Group])\n\n'
            "def render(request):\n"
            '    return HttpResponse("rendered")\n',
        )
        one = Group.objects.create(name="t:one")
        two = Group.objects.create(name="t:two")
        sign_in(client, "ada")

        with routed(root):
            passed = client.get(f"/r/{one.pk}/")
            refused = client.get(f"/r/{two.pk}/")

        assert (passed.content, refused.status_code) == (b"rendered", 403)

    def test_a_render_view_refuses_before_the_body(self, root, client) -> None:
        write_page(
            root,
            "r",
            "from django.http import HttpResponse\n"
            "from next.pages import page\n\n"
            "page.login_required()\n\n"
            "def render(request):\n"
            '    raise AssertionError("never")\n',
        )

        with routed(root):
            response = client.get("/r/")

        assert response.status_code == 302


class TestObjectDeclarations:
    """A `page.py` names its object as `DObject[Model]`, nothing else."""

    @pytest.mark.parametrize(
        ("obj", "match"),
        [
            pytest.param("group", r"takes DObject\[Model\]", id="name"),
            pytest.param(Group, r"takes DObject\[Model\]", id="model"),
            pytest.param(DObject[Group, "owner"], "takes no parent", id="parent"),
        ],
    )
    def test_another_object_is_refused(self, obj, match) -> None:
        with pytest.raises(TypeError, match=match):
            page.permission_required("auth.edit_group", obj=obj, inherit=True)

    def test_a_context_name_on_a_page_rule_is_refused_at_the_lookup(self) -> None:
        lookup = url_lookup(None, {}, {})

        with pytest.raises(TypeError, match="only a component names"):
            lookup("group")


class _RecordingGate:
    """A gate that records every lookup it builds and admits every row."""

    def __init__(self) -> None:
        self.built: list[tuple[object, ...]] = []
        self.gate = Gate(self._build, self._back)

    def _build(self, *args: object):
        self.built.append(args)
        return self._row

    def _row(self, target: object) -> None:
        return None

    def _back(self, request: object) -> str:
        return "/back/"


class TestEnforce:
    """`enforce()` builds the lookup of its gate only for a rule asking something."""

    def test_an_open_rule_builds_no_lookup(self) -> None:
        recording = _RecordingGate()
        request = RequestFactory().get("/a/")
        request.user = PermissionHolder()

        denial = enforce(
            OPEN, None, request, {}, {}, undeclared=True, gate=recording.gate
        )

        assert denial is None
        assert recording.built == []

    def test_a_rule_with_requirements_builds_one_lookup(self, tmp_path) -> None:
        recording = _RecordingGate()
        request = RequestFactory().get("/a/")
        request.user = PermissionHolder()
        url_kwargs = {"pk": 1}
        dep_cache: dict[str, object] = {}
        rule = AccessRule((Requirement("login", tmp_path / "page.py"),))

        denial = enforce(
            rule,
            None,
            request,
            url_kwargs,
            dep_cache,
            undeclared=False,
            gate=recording.gate,
        )

        assert denial is None
        assert recording.built == [(request, url_kwargs, dep_cache)]


class TestRefusal:
    """`refusal()` redirects to login, raises a 403, or raises for a broken chain."""

    def test_a_denied_permission_raises_403(self, root) -> None:
        page_path = write_page(
            root, "a", page_source('page.permission_required("auth.change_group")\n')
        )
        request = RequestFactory().get("/a/")
        request.user = PermissionHolder()

        with routed(root), pytest.raises(PermissionDenied):
            refusal(page_path, request, {}, {})

    def test_a_broken_page_above_is_a_404(self, root, client) -> None:
        write_guard(root, "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")

        assert response.status_code == 404

    def test_a_broken_page_above_raises_when_failing_loudly(self, root) -> None:
        write_guard(root, "raise RuntimeError('boom')\n")
        page_path = write_page(root, "a", page_source())
        request = RequestFactory().get("/a/")

        with routed(root, STRICT_LOADING=True), pytest.raises(PageModuleImportError):
            refusal(page_path, request, {}, {})

    def test_a_fixed_page_above_applies_on_the_next_request(self, root, client) -> None:
        declaring = write_guard(root, "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            broken = client.get("/a/")
            write_guard(root, _LOGIN_INHERIT)
            touch_later(declaring)
            fixed = client.get("/a/")

        assert (broken.status_code, fixed.status_code) == (404, 302)

    def test_refuse_broken_without_a_recorded_error_is_a_404(self, tmp_path) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(Http404),
        ):
            refuse_broken(tmp_path / "page.py")


class TestAccessMemo:
    """The rule of a chain is read once and read again once a file of it moves."""

    def test_a_reload_reads_the_earlier_rule_while_it_runs(self, root) -> None:
        source = page_source(
            "from pathlib import Path\n"
            "from next.permissions.access import declared\n\n"
            "SEEN = declared(Path(__file__)).requirements\n"
            "page.login_required()\n"
        )
        page_path = write_page(root, "a", source)

        with routed(root):
            load_python_module_memo(page_path)
            touch_later(page_path)
            module = load_python_module_memo(page_path)

        assert module is not None
        assert [item.kind for item in module.SEEN] == ["login"]
        assert len(declarations.declared(page_path).requirements) == 1

    def test_a_second_read_returns_the_memo(self, root) -> None:
        page_path = write_page(root, "a", page_source())

        with routed(root):
            assert page_access(page_path) is page_access(page_path)

    def test_a_declaration_elsewhere_keeps_the_memo(self, root, tmp_path) -> None:
        page_path = write_page(root, "a", page_source())

        with routed(root):
            held = page_access(page_path)
            declarations.declare_public(tmp_path / "elsewhere.py")
            again = page_access(page_path)

        assert again.rule is held.rule
        assert again.version == declarations.version

    def test_a_redeclared_file_of_the_chain_drops_the_memo(self, root) -> None:
        page_path = write_page(root, "a", page_source())

        with routed(root):
            held = page_access(page_path)
            declarations.declare_public(page_path)
            again = page_access(page_path)

        assert (held.rule.public, again.rule.public) == (False, True)

    def test_forgetting_drops_every_memo(self, root) -> None:
        page_path = write_page(root, "a", page_source())

        with routed(root):
            held = page_access(page_path)
            forget_page_access()

            assert page_access(page_path) is not held


class TestOwnerAccess:
    """A file of a tree answers with the rule of the page in its directory."""

    def test_a_page_module_answers_with_its_page_rule(self, root) -> None:
        page_path = write_page(root, "a", page_source("page.public()\n"))

        with routed(root):
            held = owner_access(page_path)

        assert held is not None
        assert held.rule.public is True

    def test_another_file_answers_with_the_page_beside_it(self, root) -> None:
        write_guard(root, _LOGIN_INHERIT)
        page_path = write_page(
            root, "a", page_source('page.permission_required("x.y")\n')
        )
        other = root / "a" / "forms.py"
        other.write_text("")

        with routed(root):
            load_python_module_memo(page_path)
            held = owner_access(other)

        assert held is not None
        assert [item.kind for item in held.rule.requirements] == ["login", "permission"]

    def test_a_directory_without_a_page_answers_with_its_chain(self, root) -> None:
        write_guard(root, _LOGIN_INHERIT)
        other = root / "a" / "forms.py"
        other.parent.mkdir()
        other.write_text("")

        with routed(root):
            load_python_module_memo(root / "page.py")
            held = owner_access(other)

        assert held is not None
        assert (held.rule.public, len(held.rule.requirements)) == (False, 1)

    def test_a_file_outside_every_tree_has_no_owner(self, root, tmp_path) -> None:
        with routed(root):
            assert owner_access(tmp_path / "elsewhere" / "forms.py") is None
