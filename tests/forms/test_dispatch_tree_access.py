import re
from collections.abc import Generator
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from next.components.sources import import_component_modules
from next.deps.objects import SCOPE_DEPENDENCY
from next.forms.dispatch.permissions import (
    OwnerRule,
    _Tree as _DispatchTree,
    _tree,
    owner_rule,
)
from next.forms.signals import action_dispatched, form_access_denied
from next.pages.errors import PageModuleImportError
from next.pages.loaders import _load_python_module, load_page_module
from next.permissions import add_perm
from next.permissions.access import declarations
from next.permissions.markers import OPEN, AccessRule, Requirement
from next.testing import capture_signals
from tests.support import bound_dependency, routed, write_page
from tests.support.access import (
    COMPONENTS,
    client_as,
    login_required_middleware,
    write_guard,
)


_PERM = "auth.view_user"
_READ = "notes.read_account"
_LOGIN_URL = "/accounts/login/?next="

_ACTION = """
from django import forms
from django.http import HttpResponse

from next.forms import Form, action
from next.pages import page

{declarations}


class {name}Form(Form):
    name = forms.CharField()

    class Meta:
        abstract = True


@action("{name}", form_class={name}Form)
def save(form: {name}Form) -> HttpResponse:
    return HttpResponse("saved")
"""

_LOGIN = "page.login_required()"
_REQUIRES_PERM = f'page.permission_required("{_PERM}")'
_REQUIRES_OBJECT = f'page.permission_required("{_READ}", obj=DObject[Group])'
_PUBLIC = "page.public()"
_INHERITED_PERM = f'permission_required("{_PERM}", inherit=True)'

_PERM_STATUS = [("anonymous", 302), ("visitor", 403), ("holder", 200)]
_LOGIN_STATUS = [("anonymous", 302), ("visitor", 200)]


def _action(name: str, *declarations: str) -> str:
    return _ACTION.format(name=name, declarations="\n".join(declarations))


def _plain(*declarations: str) -> str:
    return "from next.pages import page\n\n" + "\n".join(declarations) + "\n"


@dataclass(frozen=True, slots=True)
class _Tree:
    """The `page.py` sources by URL trail and the guard calls by directory."""

    pages: dict[str, str]
    guard: str | None = None
    beside: dict[str, str] = field(default_factory=dict)
    guards: dict[str, str] = field(default_factory=dict)

    def write(self, root: Path) -> Path:
        root.mkdir()
        if self.guard is not None:
            write_guard(root, f"page.{self.guard}\n")
        for trail, call in self.guards.items():
            write_guard(root / trail, f"page.{call}\n")
        for trail, source in self.pages.items():
            load_page_module(write_page(root, trail, source, body="<p>x</p>"))
        for trail, source in self.beside.items():
            module = root / trail / "forms.py"
            module.parent.mkdir(parents=True, exist_ok=True)
            module.write_text(source)
            _load_python_module(module)
        return root


def _post(root: Path, kind: str, action: str, origin: str, **framework: object):
    with routed(root, **framework):
        client = client_as(kind)
        return client.post_action(action, {"name": "x"}, origin=origin)


def _post_without_origin(root: Path, kind: str, action: str, **framework: object):
    with routed(root, **framework):
        client = client_as(kind)
        with capture_signals(action_dispatched) as recorder:
            response = client.post(client.get_action_url(action), {"name": "x"})
    assert recorder.events == []
    return response


def _assert_status(response, status: int, origin: str) -> None:
    assert response.status_code == status
    if status == 302:
        assert response["Location"] == f"{_LOGIN_URL}{origin}"
    if status == 200:
        assert response.content == b"saved"


_OWNED = {
    "page": _Tree({"notes": _action("note", _REQUIRES_PERM)}),
    "guard": _Tree({"notes": _action("note")}, guard=_INHERITED_PERM),
    "beside": _Tree(
        {"notes": "x = 1\n"}, guard=_INHERITED_PERM, beside={"notes": _action("note")}
    ),
    "beside-page": _Tree(
        {"notes": _plain(_REQUIRES_PERM)}, beside={"notes": _action("note")}
    ),
    "private": _Tree(
        {"notes": "x = 1\n"},
        guard=_INHERITED_PERM,
        beside={"notes/_parts": _action("note")},
    ),
}


@pytest.mark.django_db()
class TestOwnerRule:
    """An action answers the access rule of the file it is declared in."""

    @pytest.mark.parametrize("where", list(_OWNED))
    @pytest.mark.parametrize(("kind", "status"), _PERM_STATUS)
    def test_status(self, tmp_path: Path, where: str, kind: str, status: int) -> None:
        root = _OWNED[where].write(tmp_path / "pages")
        response = _post(root, kind, "note", "/notes/")
        _assert_status(response, status, "/notes/")

    @pytest.mark.parametrize(("kind", "status"), _LOGIN_STATUS)
    def test_login_only(self, tmp_path: Path, kind: str, status: int) -> None:
        root = _Tree({"notes": _action("note", _LOGIN)}).write(tmp_path / "pages")
        _assert_status(_post(root, kind, "note", "/notes/"), status, "/notes/")

    def test_a_refusal_is_signalled_on_the_page_layer(self, tmp_path: Path) -> None:
        root = _OWNED["page"].write(tmp_path / "pages")
        with capture_signals(form_access_denied) as recorder:
            response = _post(root, "visitor", "note", "/notes/")
        assert response.status_code == 403
        events = recorder.events_for(form_access_denied)
        assert [(e.kwargs["layer"], e.kwargs["reason"]) for e in events] == [
            ("page", "denied")
        ]

    def test_an_open_page_lets_anyone_post(self, tmp_path: Path) -> None:
        root = _Tree({"notes": _action("note")}).write(tmp_path / "pages")
        _assert_status(_post(root, "anonymous", "note", "/notes/"), 200, "/notes/")

    def test_a_guard_written_after_a_post_rules_the_next_one(
        self, tmp_path: Path
    ) -> None:
        root = _Tree({"notes": _action("note")}).write(tmp_path / "pages")
        with override_settings(DEBUG=True):
            _assert_status(_post(root, "anonymous", "note", "/notes/"), 200, "/notes/")
            write_guard(root, "page.login_required(inherit=True)\n")
            _assert_status(_post(root, "anonymous", "note", "/notes/"), 302, "/notes/")

    @pytest.mark.parametrize("origin", ["/open/", "/notes/"])
    def test_a_file_beside_a_page_answers_its_rule_from_any_origin(
        self, tmp_path: Path, origin: str
    ) -> None:
        tree = _Tree(
            {"open": _plain(), "notes": _plain(_LOGIN)},
            beside={"notes": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        _assert_status(_post(root, "anonymous", "note", origin), 302, origin)

    def test_a_file_beside_a_page_refuses_a_post_without_an_origin(
        self, tmp_path: Path
    ) -> None:
        tree = _Tree(
            {"open": _plain(), "notes": _plain(_LOGIN)},
            beside={"notes": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        assert _post_without_origin(root, "anonymous", "note").status_code == 400

    @pytest.mark.parametrize(("kind", "status"), _LOGIN_STATUS)
    def test_a_guard_in_a_skipped_folder_binds_its_files(
        self, tmp_path: Path, kind: str, status: int
    ) -> None:
        tree = _Tree(
            {"notes": _plain()},
            guards={"_components": "login_required()"},
            beside={"_components": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        _assert_status(_post(root, kind, "note", "/notes/"), status, "/notes/")

    @pytest.mark.parametrize(
        ("lift", "status"),
        [("page.public(inherit=True)", 200), ("page.public()", 302)],
        ids=["subtree", "own-page"],
    )
    def test_a_lift_above_a_private_folder(
        self, tmp_path: Path, lift: str, status: int
    ) -> None:
        tree = _Tree(
            {"notes": _plain(lift)},
            guard=_INHERITED_PERM,
            beside={"notes/_parts": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        _assert_status(_post(root, "anonymous", "note", "/notes/"), status, "/notes/")


_FORGED = _Tree(
    {"open": _action("open_note"), "secret": _action("secret_note", _REQUIRES_PERM)}
)


@pytest.mark.django_db()
class TestForgedOrigin:
    """A posted origin adds its rule to the owner's and lifts none of it."""

    @pytest.mark.parametrize(
        ("action", "origin"),
        [
            pytest.param("open_note", "/secret/", id="open-action-secret-origin"),
            pytest.param("secret_note", "/open/", id="secret-action-open-origin"),
        ],
    )
    @pytest.mark.parametrize(("kind", "status"), _PERM_STATUS)
    def test_status(
        self, tmp_path: Path, action: str, origin: str, kind: str, status: int
    ) -> None:
        root = _FORGED.write(tmp_path / "pages")
        _assert_status(_post(root, kind, action, origin), status, origin)


def _outside(tmp_path: Path) -> Path:
    module = tmp_path / "elsewhere" / "forms.py"
    module.parent.mkdir()
    module.write_text(_action("loose_note"))
    _load_python_module(module)
    return module


@pytest.mark.django_db()
class TestActionOutsideTheTree:
    """An action declared outside every page tree answers the origin rule alone."""

    @pytest.mark.parametrize(
        ("origin", "kind", "status"),
        [
            ("/open/", "anonymous", 200),
            ("/secret/", "anonymous", 302),
            ("/secret/", "visitor", 403),
            ("/secret/", "holder", 200),
        ],
    )
    def test_status(self, tmp_path: Path, origin: str, kind: str, status: int) -> None:
        root = _FORGED.write(tmp_path / "pages")
        _outside(tmp_path)
        _assert_status(_post(root, kind, "loose_note", origin), status, origin)

    def test_an_action_without_a_file_answers_an_open_rule(self) -> None:
        held = _tree(None, None)

        assert (held.rule, held.broken, held.deferred) == (OPEN, None, True)


class TestTreeMemo:
    """The joined rule over an action is read once while no declaration changes."""

    def _page(self, tmp_path: Path) -> Path:
        root = _Tree({"notes": _action("note")}).write(tmp_path / "pages")
        return root / "notes" / "page.py"

    def _read(self, page_path: Path) -> _DispatchTree:
        return _tree(str(page_path), page_path)

    def test_a_second_read_returns_the_memo(self, tmp_path: Path) -> None:
        page_path = self._page(tmp_path)

        with routed(page_path.parent.parent):
            self._read(page_path)
            held = self._read(page_path)
            again = self._read(page_path)

        assert held.stamp is not None
        assert again is held

    def test_a_redeclared_file_of_the_chain_rebuilds_the_rule(
        self, tmp_path: Path
    ) -> None:
        page_path = self._page(tmp_path)
        login = Requirement("login", page_path)

        with routed(page_path.parent.parent):
            self._read(page_path)
            held = self._read(page_path)
            declarations.declare(page_path, login)
            again = self._read(page_path)

        assert (held.rule.requirements, again.rule.requirements) == ((), (login,))
        assert again.stamp is not None
        assert again.stamp != held.stamp


_DEFAULTS = _Tree(
    {
        "open": _action("open_note"),
        "public": _action("public_note", _PUBLIC),
        "shown": _plain(_PUBLIC),
        "hidden": _plain(),
    },
    beside={"shown": _action("beside_note"), "hidden": _action("hidden_note")},
)


def _closed(root: Path, kind: str, action: str, origin: str, by: str):
    if by == "middleware":
        with login_required_middleware():
            return _post(root, kind, action, origin)
    return _post(root, kind, action, origin, LOGIN_REQUIRED=True)


@pytest.mark.django_db()
class TestLoginByDefault:
    """Under the default login only an action of a public page stays open."""

    @pytest.mark.parametrize(
        ("action", "origin", "kind", "status"),
        [
            pytest.param("open_note", "/open/", "anonymous", 302, id="open"),
            pytest.param("open_note", "/open/", "visitor", 200, id="open-visitor"),
            pytest.param("public_note", "/public/", "anonymous", 200, id="public"),
            pytest.param(
                "public_note", "/open/", "anonymous", 302, id="public-open-origin"
            ),
            pytest.param("beside_note", "/shown/", "anonymous", 200, id="beside"),
            pytest.param(
                "beside_note", "/open/", "anonymous", 302, id="beside-open-origin"
            ),
            pytest.param(
                "hidden_note", "/shown/", "anonymous", 302, id="beside-public-origin"
            ),
            pytest.param("loose_note", "/public/", "anonymous", 302, id="outside"),
            pytest.param(
                "loose_note", "/public/", "visitor", 200, id="outside-visitor"
            ),
            pytest.param(
                "loose_note", "/open/", "anonymous", 302, id="outside-open-origin"
            ),
        ],
    )
    @pytest.mark.parametrize("by", ["middleware", "setting"])
    def test_status(
        self, tmp_path: Path, action: str, origin: str, kind: str, status: int, by
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        _outside(tmp_path)
        _assert_status(_closed(root, kind, action, origin, by), status, origin)

    @pytest.mark.parametrize("action", ["public_note", "beside_note", "hidden_note"])
    @pytest.mark.parametrize(
        ("framing", "framework"),
        [(login_required_middleware, {}), (nullcontext, {"LOGIN_REQUIRED": True})],
        ids=["middleware", "setting"],
    )
    def test_a_post_without_an_origin_is_a_400(
        self, tmp_path: Path, action: str, framing, framework
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        with framing():
            response = _post_without_origin(root, "anonymous", action, **framework)
        assert response.status_code == 400

    def test_without_the_middleware_an_open_action_stays_open(
        self, tmp_path: Path
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        _assert_status(_post(root, "anonymous", "open_note", "/open/"), 200, "/open/")


_LIFTED = _Tree(
    {
        "receipt": _action("receipt_note", "page.public(inherit=True)"),
        "receipt/admin": _action("admin_note", "page.login_required(inherit=True)"),
    },
    guard="login_required(inherit=True)",
)


@pytest.mark.django_db()
class TestLiftedSubtree:
    """An action in a lifted subtree is open like its page, and closed below a guard."""

    @pytest.mark.parametrize(
        ("action", "origin", "status"),
        [
            pytest.param("receipt_note", "/receipt/", 200, id="lifted"),
            pytest.param("admin_note", "/receipt/admin/", 302, id="guarded-again"),
        ],
    )
    @pytest.mark.parametrize(
        "framing", [nullcontext, login_required_middleware], ids=["plain", "middleware"]
    )
    def test_status(
        self, tmp_path: Path, action: str, origin: str, status: int, framing
    ) -> None:
        root = _LIFTED.write(tmp_path / "pages")
        with framing():
            response = _post(root, "anonymous", action, origin)
        _assert_status(response, status, origin)


_GROUP_HEAD = """
from django.contrib.auth.models import Group

from next.deps import DObject
"""

_SHOWS_GROUP = """

@action("shows_group")
def shows_group(group: DObject[Group]) -> HttpResponse:
    return HttpResponse(group.name)
"""


def _grouped(name: str, *declarations: str) -> str:
    return _GROUP_HEAD + _action(name, *declarations)


_OBJECTS = _Tree(
    {
        "owner/[int:group]": _grouped("owner_note", _REQUIRES_OBJECT) + _SHOWS_GROUP,
        "plain/[int:group]": _action("plain_note"),
        "viewer/[int:group]": _GROUP_HEAD + _plain(_REQUIRES_OBJECT),
        "bare": _action("bare_note"),
    }
)


@pytest.fixture()
def _group_scope(scoped_models) -> Generator[None, None, None]:
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "t"):
        yield


@pytest.mark.django_db()
@pytest.mark.usefixtures(
    "object_permission_backend", "isolated_permissions", "_group_scope"
)
class TestObjectRule:
    """An `obj=` loads the row the origin URL names, the one the handler reads."""

    @pytest.mark.parametrize(
        ("action", "trail"),
        [
            pytest.param("owner_note", "owner", id="owner-rule"),
            pytest.param("plain_note", "viewer", id="origin-rule"),
        ],
    )
    @pytest.mark.parametrize(("allowed", "status"), [("t:acme", 200), ("t:x", 403)])
    def test_status(
        self, tmp_path: Path, action: str, trail: str, allowed: str, status: int
    ) -> None:
        add_perm(_READ, lambda _user, obj: obj.name == allowed)
        group = Group.objects.create(name="t:acme")
        root = _OBJECTS.write(tmp_path / "pages")
        origin = f"/{trail}/{group.pk}/"
        _assert_status(_post(root, "visitor", action, origin), status, origin)

    def test_a_hidden_row_is_a_404(self, tmp_path: Path) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        group = Group.objects.create(name="other:acme")
        root = _OBJECTS.write(tmp_path / "pages")
        response = _post(root, "visitor", "owner_note", f"/owner/{group.pk}/")
        assert response.status_code == 404

    def test_an_origin_without_the_kwarg_is_a_400(self, tmp_path: Path) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        root = _OBJECTS.write(tmp_path / "pages")
        assert _post(root, "visitor", "owner_note", "/bare/").status_code == 400

    def test_a_missing_origin_is_a_400(self, tmp_path: Path) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        root = _OBJECTS.write(tmp_path / "pages")
        assert _post_without_origin(root, "visitor", "owner_note").status_code == 400

    def test_the_check_and_the_handler_read_one_row(self, tmp_path: Path) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        group = Group.objects.create(name="t:acme")
        root = _OBJECTS.write(tmp_path / "pages")
        with CaptureQueriesContext(connection) as queries:
            response = _post(root, "visitor", "shows_group", f"/owner/{group.pk}/")
        rows = [
            item
            for item in queries.captured_queries
            if item["sql"].startswith('SELECT "auth_group"')
        ]
        assert (response.content, len(rows)) == (b"t:acme", 1)


@pytest.mark.django_db()
class TestBrokenGuard:
    """A `page.py` above that fails to import refuses every action below it."""

    @pytest.mark.parametrize("kind", ["anonymous", "holder"])
    def test_status(self, tmp_path: Path, kind: str) -> None:
        root = tmp_path / "pages"
        _Tree({"notes": _action("note")}).write(root)
        write_guard(root, 'raise RuntimeError("broken guard")\n')
        assert _post(root, kind, "note", "/notes/").status_code == 404

    def test_failing_loudly_raises_the_import_error(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        _Tree({"notes": _action("note")}).write(root)
        guard = write_guard(root, 'raise RuntimeError("broken guard")\n')
        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match=re.escape(str(guard))),
        ):
            _post(root, "anonymous", "note", "/notes/")


_COMPONENT_ACTION = """
from django.http import HttpResponse

from next.components import component
from next.forms import action

{declaration}


@action("card_save")
def card_save() -> HttpResponse:
    return HttpResponse("saved")
"""

_COMPONENT_RULES = {
    "login": "component.login_required()",
    "permission": f'component.permission_required("{_PERM}")',
    "context": f'component.permission_required("{_PERM}", obj="account")',
}

_COMPONENT_STATUS = {
    "login": {"anonymous": 302, "visitor": 200, "holder": 200},
    "permission": {"anonymous": 302, "visitor": 403, "holder": 200},
    "context": {"anonymous": 302, "visitor": 403, "holder": 403, "superuser": 403},
}


def _component_tree(root: Path, declaration: str) -> Path:
    write_page(root, "", _plain(_PUBLIC), body='{% component "card" %}')
    folder = root / "_components" / "card"
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text("card")
    module = folder / "component.py"
    module.write_text(_COMPONENT_ACTION.format(declaration=declaration))
    return module


@pytest.mark.django_db()
@pytest.mark.usefixtures("form_registries")
class TestComponentAction:
    """An action of a `component.py` answers to what the component declares."""

    @pytest.mark.parametrize(
        ("rule", "kind", "status"),
        [
            pytest.param(rule, kind, status, id=f"{rule}-{kind}")
            for rule, statuses in _COMPONENT_STATUS.items()
            for kind, status in statuses.items()
        ],
    )
    @pytest.mark.parametrize("mode", ["none", "setting", "middleware"])
    def test_status(
        self, tmp_path: Path, rule: str, kind: str, status: int, mode: str
    ) -> None:
        root = tmp_path / "pages"
        _component_tree(root, _COMPONENT_RULES[rule])
        framing = login_required_middleware() if mode == "middleware" else nullcontext()
        framework = {"LOGIN_REQUIRED": True} if mode == "setting" else {}
        with framing, routed(root, COMPONENT_BACKENDS=COMPONENTS, **framework):
            import_component_modules()
            response = client_as(kind).post_action("card_save", {}, origin="/")
        _assert_status(response, status, "/")

    def test_a_component_outside_every_tree_answers_to_itself(
        self, tmp_path: Path
    ) -> None:
        module = tmp_path / "elsewhere" / "component.py"
        login = Requirement("login", module)
        declarations.declare(module, login)

        held = owner_rule(module)

        assert held == OwnerRule(AccessRule((login,), sources=(module,)), None)
        assert owner_rule(tmp_path / "elsewhere" / "forms.py") is None
