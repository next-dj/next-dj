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
from next.pages import proxy
from next.pages.access import (
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
    login_required_middleware,
    page_source,
    sign_in,
    write_proxy,
)


pytestmark = pytest.mark.django_db


@pytest.fixture()
def root(tmp_path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _three_levels(root: Path) -> Path:
    write_proxy(root)
    write_proxy(root / "team", "proxy.login_required()\n")
    return write_page(root, "team/notes", page_source())


class TestProxyModule:
    """A `proxy.py` declares access and nothing else, and reloads by its mtime."""

    def test_the_proxy_carries_no_context(self) -> None:
        assert not hasattr(proxy, "context")

    @pytest.mark.usefixtures("watched_template_edits")
    def test_a_rewritten_proxy_applies_before_the_next_request(
        self, root, client
    ) -> None:
        declaring = write_proxy(root)
        write_page(root, "", page_source())

        with routed(root):
            first = client.get("/")
            declaring.write_text(f"{declaring.read_text()}proxy.login_required()\n")
            touch_later(declaring)
            second = client.get("/")

        assert (first.status_code, second.status_code) == (200, 302)


class TestInheritedAccess:
    """Each `proxy.py` from the tree root down binds every page below it."""

    def test_a_requirement_at_the_middle_level_binds_the_leaf(self, root) -> None:
        page_path = _three_levels(root)

        with routed(root):
            rule = page_access(page_path).rule

        assert [(item.kind, item.source) for item in rule.requirements] == [
            ("login", root / "team" / "proxy.py")
        ]

    def test_three_levels_join_root_first(
        self, root, client, isolated_permissions
    ) -> None:
        write_proxy(root, "proxy.login_required()\n")
        write_proxy(root / "a", 'proxy.permission_required("auth.view_group")\n')
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
    def test_the_proxy_permission_decides(
        self, root, client, holder, status, location
    ) -> None:
        write_proxy(root, 'proxy.permission_required("auth.view_group")\n')
        write_page(root, "a", page_source())
        if holder:
            sign_in(client, "ada", "view_group")

        with routed(root):
            response = client.get("/a/?x=1")

        assert (response.status_code, response.get("Location")) == (status, location)

    def test_a_sibling_directory_stays_open(self, root, client) -> None:
        write_proxy(root / "closed", "proxy.login_required()\n")
        write_page(root, "closed", page_source())
        write_page(root, "open", page_source())

        with routed(root):
            closed = client.get("/closed/")
            opened = client.get("/open/")

        assert (closed.status_code, opened.status_code) == (302, 200)

    def test_a_proxy_above_the_tree_root_binds_nothing(self, tmp_path, client) -> None:
        write_proxy(tmp_path, "proxy.login_required()\n")
        root = tmp_path / "pages"
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")
            rule = page_access(root / "a" / "page.py").rule

        assert response.status_code == 200
        assert rule.requirements == ()


class TestMountedTrees:
    """A `pages()` tree starts its chain at its own root, never at the host."""

    @pytest.fixture()
    def mounted(self, tmp_path) -> Path:
        app = write_app(tmp_path, "wallets", {"pages": ()})
        write_proxy(app, "proxy.login_required()\n")
        write_proxy(app / "pages")
        write_page(app / "pages", "ops", page_source(template="w"))
        write_proxy(tmp_path / "site", "proxy.login_required()\n")
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

    def test_the_mounted_page_reads_its_own_proxy_alone(self, mounted, client) -> None:
        response = client.get("/w/1/ops/")

        assert (response.status_code, response.content) == (200, b"w")

    def test_the_host_tree_keeps_its_own_guard(self, mounted, client) -> None:
        assert client.get("/contact/").status_code == 302


class TestLiftedRequirements:
    """`public()` lifts the requirements above it, and a deeper proxy binds again."""

    @pytest.fixture()
    def booking(self, root) -> Path:
        booking = root / "booking"
        write_proxy(booking, "proxy.login_required()\n")
        write_page(root, "booking/[int:booking]", page_source())
        receipt = booking / "[int:booking]" / "receipt"
        write_proxy(receipt, "proxy.public()\n")
        write_page(root, "booking/[int:booking]/receipt", page_source())
        write_proxy(receipt / "admin", "proxy.login_required()\n")
        write_page(root, "booking/[int:booking]/receipt/admin", page_source())
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
        assert lifted.sources == (receipt / "proxy.py",)
        assert again.public is False
        assert [item.source for item in again.requirements] == [
            receipt / "admin" / "proxy.py"
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
        write_proxy(root, "proxy.login_required()\n")
        page_path = write_page(root, "a", page_source("page.public()\n"))
        write_page(root, "a/b", page_source())

        with routed(root):
            opened = client.get("/a/")
            below = client.get("/a/b/")
            rule = page_access(page_path).rule

        assert (opened.status_code, below.status_code) == (200, 302)
        assert rule.sources == (page_path,)

    def test_a_requirement_beside_public_still_binds(self, root, client) -> None:
        write_proxy(root, "proxy.public()\nproxy.login_required()\n")
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")
            rule = page_access(root / "a" / "page.py").rule

        assert response.status_code == 302
        assert rule.public is False

    def test_a_second_public_keeps_the_first_as_the_source(self, root) -> None:
        write_proxy(root, "proxy.login_required()\n")
        write_proxy(root / "a", "proxy.public()\n")
        page_path = write_page(root, "a", page_source("page.public()\n"))

        with routed(root):
            rule = page_access(page_path).rule

        assert rule.sources == (root / "a" / "proxy.py",)

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

    def test_a_permission_without_an_object_is_checked_first(
        self, root, client
    ) -> None:
        write_proxy(root, 'proxy.permission_required("auth.view_group")\n')
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
    """A page or a `proxy.py` names its object as `DObject[Model]`, nothing else."""

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
            proxy.permission_required("auth.edit_group", obj=obj)

    def test_a_context_name_on_a_page_rule_is_refused_at_the_lookup(self) -> None:
        lookup = url_lookup(None, {}, {})

        with pytest.raises(TypeError, match="only a component names"):
            lookup("group")


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

    def test_a_broken_proxy_is_a_404(self, root, client) -> None:
        write_proxy(root, "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            response = client.get("/a/")

        assert response.status_code == 404

    def test_a_broken_proxy_raises_when_failing_loudly(self, root) -> None:
        write_proxy(root, "raise RuntimeError('boom')\n")
        page_path = write_page(root, "a", page_source())
        request = RequestFactory().get("/a/")

        with routed(root, STRICT_LOADING=True), pytest.raises(PageModuleImportError):
            refusal(page_path, request, {}, {})

    def test_a_fixed_proxy_applies_on_the_next_request(self, root, client) -> None:
        declaring = write_proxy(root, "raise RuntimeError('boom')\n")
        write_page(root, "a", page_source())

        with routed(root):
            broken = client.get("/a/")
            write_proxy(root, "proxy.login_required()\n")
            touch_later(declaring)
            fixed = client.get("/a/")

        assert (broken.status_code, fixed.status_code) == (404, 302)

    def test_refuse_broken_without_a_recorded_error_is_a_404(self, tmp_path) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(Http404),
        ):
            refuse_broken(tmp_path / "proxy.py")


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
        declarations.forget(tmp_path / "elsewhere.py")

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
        write_proxy(root, "proxy.login_required()\n")
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
        write_proxy(root, "proxy.login_required()\n")
        other = root / "a" / "forms.py"
        other.parent.mkdir()
        other.write_text("")

        with routed(root):
            load_python_module_memo(root / "proxy.py")
            held = owner_access(other)

        assert held is not None
        assert (held.rule.public, len(held.rule.requirements)) == (False, 1)

    def test_a_file_outside_every_tree_has_no_owner(self, root, tmp_path) -> None:
        with routed(root):
            assert owner_access(tmp_path / "elsewhere" / "forms.py") is None
