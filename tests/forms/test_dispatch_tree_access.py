from collections.abc import Generator
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from next.deps.objects import SCOPE_DEPENDENCY
from next.forms.dispatch.permissions import _tree
from next.forms.signals import form_access_denied
from next.pages.errors import PageModuleImportError
from next.pages.loaders import _load_python_module
from next.permissions import add_perm
from next.permissions.markers import OPEN
from next.testing import capture_signals
from tests.support import bound_dependency, routed, write_page
from tests.support.access import client_as, login_required_middleware, write_proxy


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


def _action(name: str, *declarations: str) -> str:
    return _ACTION.format(name=name, declarations="\n".join(declarations))


def _plain(*declarations: str) -> str:
    return "from next.pages import page\n\n" + "\n".join(declarations) + "\n"


@dataclass(frozen=True, slots=True)
class _Tree:
    """The `page.py` sources by URL trail and the `proxy.py` calls by directory."""

    pages: dict[str, str]
    proxy: str | None = None
    beside: dict[str, str] = field(default_factory=dict)
    proxies: dict[str, str] = field(default_factory=dict)

    def write(self, root: Path) -> Path:
        root.mkdir()
        if self.proxy is not None:
            write_proxy(root, f"proxy.{self.proxy}\n")
        for trail, call in self.proxies.items():
            write_proxy(root / trail, f"proxy.{call}\n")
        for trail, source in self.pages.items():
            _load_python_module(write_page(root, trail, source, body="<p>x</p>"))
        for trail, source in self.beside.items():
            module = root / trail / "forms.py"
            module.write_text(source)
            _load_python_module(module)
        return root


def _post(root: Path, kind: str, action: str, origin: str, **framework: object):
    with routed(root, **framework):
        client = client_as(kind)
        return client.post_action(action, {"name": "x"}, origin=origin)


def _assert_status(response, status: int, origin: str) -> None:
    assert response.status_code == status
    if status == 302:
        assert response["Location"] == f"{_LOGIN_URL}{origin}"
    if status == 200:
        assert response.content == b"saved"


_OWNED = {
    "page": _Tree({"notes": _action("note", _REQUIRES_PERM)}),
    "proxy": _Tree({"notes": _action("note")}, proxy=f'permission_required("{_PERM}")'),
    "beside": _Tree(
        {"notes": "x = 1\n"},
        proxy=f'permission_required("{_PERM}")',
        beside={"notes": _action("note")},
    ),
    "beside-page": _Tree(
        {"notes": _plain(_REQUIRES_PERM)}, beside={"notes": _action("note")}
    ),
}


@pytest.mark.django_db()
class TestOwnerRule:
    """An action answers the access rule of the file it is declared in."""

    @pytest.mark.parametrize("where", list(_OWNED))
    @pytest.mark.parametrize(
        ("kind", "status"), [("anonymous", 302), ("visitor", 403), ("holder", 200)]
    )
    def test_status(self, tmp_path: Path, where: str, kind: str, status: int) -> None:
        root = _OWNED[where].write(tmp_path / "pages")
        response = _post(root, kind, "note", "/notes/")
        _assert_status(response, status, "/notes/")

    @pytest.mark.parametrize(("kind", "status"), [("anonymous", 302), ("visitor", 200)])
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

    def test_a_proxy_written_after_a_post_rules_the_next_one(
        self, tmp_path: Path
    ) -> None:
        root = _Tree({"notes": _action("note")}).write(tmp_path / "pages")
        with override_settings(DEBUG=True):
            _assert_status(_post(root, "anonymous", "note", "/notes/"), 200, "/notes/")
            write_proxy(root, "proxy.login_required()\n")
            _assert_status(_post(root, "anonymous", "note", "/notes/"), 302, "/notes/")

    @pytest.mark.parametrize("origin", ["/open/", None])
    def test_a_file_beside_a_page_answers_its_rule_from_any_origin(
        self, tmp_path: Path, origin: str | None
    ) -> None:
        tree = _Tree(
            {"open": _plain(), "notes": _plain(_LOGIN)},
            beside={"notes": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        with routed(root):
            response = client_as("anonymous").post_action(
                "note", {"name": "x"}, origin=origin
            )
        _assert_status(response, 302, origin or "/")

    @pytest.mark.parametrize(("kind", "status"), [("anonymous", 302), ("visitor", 200)])
    def test_a_proxy_in_a_skipped_folder_binds_its_files(
        self, tmp_path: Path, kind: str, status: int
    ) -> None:
        tree = _Tree(
            {"notes": _plain()},
            proxies={"_components": "login_required()"},
            beside={"_components": _action("note")},
        )
        root = tree.write(tmp_path / "pages")
        _assert_status(_post(root, kind, "note", "/notes/"), status, "/notes/")


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
    @pytest.mark.parametrize(
        ("kind", "status"), [("anonymous", 302), ("visitor", 403), ("holder", 200)]
    )
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


_DEFAULTS = _Tree(
    {
        "open": _action("open_note"),
        "public": _action("public_note", _PUBLIC),
        "shown": _plain(_PUBLIC),
        "hidden": _plain(),
    },
    beside={"shown": _action("beside_note"), "hidden": _action("hidden_note")},
)


@pytest.mark.django_db()
class TestLoginRequiredMiddleware:
    """Under `LoginRequiredMiddleware` only an action of a public page stays open."""

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
    def test_status(
        self, tmp_path: Path, action: str, origin: str, kind: str, status: int
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        _outside(tmp_path)
        with login_required_middleware():
            response = _post(root, kind, action, origin)
        _assert_status(response, status, origin)

    @pytest.mark.parametrize(
        ("action", "status"),
        [("public_note", 200), ("beside_note", 200), ("hidden_note", 302)],
    )
    def test_a_post_without_an_origin(
        self, tmp_path: Path, action: str, status: int
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        with login_required_middleware(), routed(root):
            response = client_as("anonymous").post_action(action, {"name": "x"})
        _assert_status(response, status, "/")

    def test_without_the_middleware_an_open_action_stays_open(
        self, tmp_path: Path
    ) -> None:
        root = _DEFAULTS.write(tmp_path / "pages")
        _assert_status(_post(root, "anonymous", "open_note", "/open/"), 200, "/open/")


_LIFTED = _Tree(
    {"receipt": _action("receipt_note"), "receipt/admin": _action("admin_note")},
    proxy="login_required()",
    proxies={"receipt": "public()", "receipt/admin": "login_required()"},
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

    @pytest.mark.parametrize("origin", ["/bare/", None], ids=["bare", "missing"])
    def test_an_origin_without_the_kwarg_is_a_400(
        self, tmp_path: Path, origin: str | None
    ) -> None:
        add_perm(_READ, lambda _user, _obj: True)
        root = _OBJECTS.write(tmp_path / "pages")
        with routed(root):
            response = client_as("visitor").post_action(
                "owner_note", {"name": "x"}, origin=origin
            )
        assert response.status_code == 400

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
class TestBrokenProxy:
    """A `proxy.py` that fails to import refuses every action below it."""

    @pytest.mark.parametrize("kind", ["anonymous", "holder"])
    def test_status(self, tmp_path: Path, kind: str) -> None:
        root = tmp_path / "pages"
        _Tree({"notes": _action("note")}).write(root)
        write_proxy(root, 'raise RuntimeError("broken proxy")\n')
        assert _post(root, kind, "note", "/notes/").status_code == 404

    def test_failing_loudly_raises_the_import_error(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        _Tree({"notes": _action("note")}).write(root)
        write_proxy(root, 'raise RuntimeError("broken proxy")\n')
        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match=r"proxy\.py failed to import"),
        ):
            _post(root, "anonymous", "note", "/notes/")
