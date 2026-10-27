import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import resolve

from next.deps.objects import SCOPE_DEPENDENCY
from next.diagnostics import reset_failure_logs
from next.forms.uid import ORIGIN_FIELD_NAME
from next.intercept import Intercept, intercepts
from next.pages.errors import PageModuleImportError
from next.partial import (
    ForeignPageNotAuthorizedError,
    Patches,
    intercept as partial_intercept,
    patches as patches_module,
)
from next.partial.errors import UnknownZoneError
from next.partial.headers import CONTENT_TYPE
from next.permissions import add_perm
from next.testing import NextClient, envelope_of
from tests.support import bound_dependency, partial_meta, routed, write_page
from tests.support.access import CONTEXT_CALLS, PAGE_HEAD, sign_in
from tests.support.cases.intercepts import (
    OBJECT_CASES,
    ORDER_CASES,
    ObjectCase,
    OrderCase,
)
from tests.urls import urls_namespaced


pytestmark = pytest.mark.django_db

_TARGET_BODY = "<p>photo {{ pk }}</p>"
_PEEK_BODY = (
    '{% zone "peek" %}<h2>peek {{ pk }}</h2>'
    '{% zone "peek-entries" %}<ul>entries</ul>{% endzone %}{% endzone %}'
)
_RENDER = 'def render(request, **kwargs):\n    return "dynamic"\n'
_RULES = {
    "open": "",
    "view": 'page.permission_required("auth.view_user")\n',
    "add": 'page.permission_required("auth.add_user")\n',
    "login": "page.login_required()\n",
}
_USERS: dict[str, tuple[str, ...] | None] = {
    "anonymous": None,
    "visitor": (),
    "viewer": ("view_user",),
    "adder": ("add_user",),
    "both": ("view_user", "add_user"),
}
_NO_STORE = {"private", "no-store"}
_CONDITIONAL_GET = [
    *settings.MIDDLEWARE,
    "django.middleware.http.ConditionalGetMiddleware",
]


def _shared_denial(request, exception=None) -> HttpResponse:
    del request, exception
    response = HttpResponse("denied", status=403)
    response["Cache-Control"] = "public, max-age=600"
    return response


def _shared_missing(request, exception=None) -> HttpResponse:
    del request, exception
    response = HttpResponse("missing", status=404)
    response["Cache-Control"] = "public, max-age=600"
    return response


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _site(
    root: Path,
    *,
    target: str = "open",
    intercept: str = "open",
    dynamic: bool = False,
    target_source: str = "",
    intercept_source: str = "",
    target_body: str | None = _TARGET_BODY,
    peek: str = _PEEK_BODY,
) -> None:
    """Write a target at `photo/[int:pk]` and the intercept `(.)photo/[int:pk]`."""
    write_page(
        root,
        "photo/[int:pk]",
        f"{PAGE_HEAD}{_RULES[target]}{target_source}{_RENDER if dynamic else ''}",
        body=None if dynamic else target_body,
    )
    write_page(
        root,
        "(.)photo/[int:pk]",
        f"{PAGE_HEAD}{_RULES[intercept]}{intercept_source}",
        body=peek,
    )


@contextmanager
def _mounted(root: Path) -> Generator[None, None, None]:
    with routed(root):
        resolve("/photo/3/")
        yield


def _client(kind: str = "anonymous", **extra: object) -> NextClient:
    client = NextClient(**extra)
    codenames = _USERS[kind]
    if codenames is not None:
        sign_in(client, kind, *codenames)
    return client


def _peek(client: NextClient, zone: str = "peek", **extra: object):
    return client.get_zones("/photo/3/", zone, **extra)


def _directives(response) -> set[str]:
    return set(response["Cache-Control"].replace(",", " ").split())


def _ops(response, op: str) -> list[dict]:
    return [item for item in envelope_of(response).ops if item["op"] == op]


class TestOrder:
    """Both refusals run before any status check, the target's first."""

    @pytest.mark.parametrize("case", ORDER_CASES, ids=lambda case: case.id)
    def test_the_status_a_request_earns(self, root: Path, case: OrderCase) -> None:
        _site(root, target=case.target, intercept=case.intercept, dynamic=case.dynamic)

        with routed(root):
            response = _peek(_client(case.user), case.zone, version=case.version)

        assert response.status_code == case.status

    def test_a_dynamic_target_names_its_body_for_an_unknown_zone(
        self, root: Path
    ) -> None:
        _site(root, dynamic=True)

        with routed(root):
            response = _peek(_client("both"), "ghost")

        assert response.content == b"zone in dynamic body"

    def test_an_unknown_zone_is_a_400_of_the_target(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client("both"), "ghost")

        assert response.content == b"unknown zone"


class TestAdmission:
    """A request must pass the chain of the target and the chain of the intercept."""

    def test_both_chains_admit(self, root: Path) -> None:
        _site(root, target="view", intercept="add")

        with routed(root):
            response = _peek(_client("both"))

        assert envelope_of(response).html_for_zone("peek") == (
            '<div data-next-zone="peek"><h2>peek 3</h2>'
            '<div data-next-zone="peek-entries"><ul>entries</ul></div></div>'
        )

    def test_a_public_target_with_a_login_intercept_redirects_to_login(
        self, root: Path
    ) -> None:
        _site(root, intercept="login")

        with routed(root):
            response = _peek(_client())

        assert response.status_code == 302
        assert "next=/photo/3/" in response["Location"]

    def test_an_intercept_refusal_carries_no_store_and_the_partial_vary(
        self, root: Path
    ) -> None:
        _site(root, intercept="login")

        with routed(root):
            response = _peek(_client())

        assert _directives(response) >= _NO_STORE
        assert "X-Next-Zone" in response["Vary"]

    def test_an_intercept_403_carries_no_store_and_the_partial_vary(
        self, root: Path
    ) -> None:
        _site(root, intercept="add", target_source="cache = 60\n")

        with routed(root):
            response = _peek(_client("viewer"))

        assert response.status_code == 403
        assert _directives(response) >= _NO_STORE
        assert "X-Next-Zone" in response["Vary"]

    def test_an_intercept_403_gets_no_etag_under_conditional_get(
        self, root: Path
    ) -> None:
        _site(root, intercept="add")

        with routed(root), override_settings(MIDDLEWARE=_CONDITIONAL_GET):
            response = _peek(_client("viewer"))

        assert response.status_code == 403
        assert not response.has_header("ETag")

    def test_a_target_403_on_a_zone_get_stays_bare(self, root: Path) -> None:
        _site(root, target="view")

        with routed(root):
            response = _peek(_client("visitor"))

        assert response.status_code == 403
        assert "no-store" not in response.get("Cache-Control", "")
        assert "X-Next-Zone" not in response.get("Vary", "")

    def test_the_denial_of_an_intercept_leaks_none_of_its_zone(
        self, root: Path
    ) -> None:
        _site(root, intercept="add")

        with routed(root):
            response = _peek(_client("viewer"))

        assert b"peek" not in response.content


class TestBrokenIntercept:
    """A broken intercept chain is refused as a broken routed page is."""

    _BROKEN = "raise RuntimeError('boom')\n"

    def test_a_broken_intercept_is_a_404_in_production(self, root: Path) -> None:
        _site(root, intercept_source=self._BROKEN)

        with routed(root):
            response = _peek(_client("visitor"))

        assert response.status_code == 404
        assert _directives(response) >= _NO_STORE

    def test_a_broken_intercept_raises_its_import_failure_under_debug(
        self, root: Path
    ) -> None:
        _site(root, intercept_source=self._BROKEN)

        with routed(root), override_settings(DEBUG=True):
            client = _client("visitor")
            with pytest.raises(PageModuleImportError):
                _peek(client)


class TestSelection:
    """Only a safe request naming a zone the target lacks reaches an intercept."""

    def test_a_zone_of_the_target_stays_with_the_target(self, root: Path) -> None:
        _site(
            root,
            intercept="add",
            target_body='{% zone "side" %}<i>side {{ pk }}</i>{% endzone %}',
        )

        with routed(root):
            response = _peek(_client(), "side")

        assert envelope_of(response).html_for_zone("side") == (
            '<div data-next-zone="side"><i>side 3</i></div>'
        )

    def test_a_plain_get_renders_the_target(self, root: Path) -> None:
        _site(root, intercept="add")

        with routed(root):
            response = _client().get("/photo/3/")

        assert response.content == b"<p>photo 3</p>"

    def test_the_nested_zone_of_the_intercept_serves_alone(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client(), "peek-entries")

        assert envelope_of(response).html_for_zone("peek-entries") == (
            '<div data-next-zone="peek-entries"><ul>entries</ul></div>'
        )

    def test_a_head_request_selects_the_intercept(self, root: Path) -> None:
        _site(root, intercept="add")
        meta = partial_meta(zones="peek")

        with routed(root):
            denied = _client("viewer").head("/photo/3/", **meta)
            served = _client("adder").head("/photo/3/", **meta)

        assert (denied.status_code, served.status_code) == (403, 200)

    @pytest.mark.parametrize(
        ("zone", "status"), [("peek", 400), ("side", 200)], ids=["intercept", "target"]
    )
    def test_a_post_keeps_the_target(self, root: Path, zone: str, status: int) -> None:
        _site(
            root,
            intercept="add",
            target_body='{% zone "side" %}<i>side</i>{% endzone %}',
        )

        with routed(root):
            response = _client("adder").post(
                "/photo/3/", headers={"X-Next-Request": "1", "X-Next-Zone": zone}
            )

        assert response.status_code == status


class TestHeaders:
    """An intercept answer is a patch envelope no cache may keep."""

    def test_the_answer_is_private_and_never_stored(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client())

        assert response["Content-Type"] == CONTENT_TYPE
        assert _directives(response) >= _NO_STORE

    def test_the_answer_varies_on_the_zone_header(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client())

        assert "X-Next-Zone" in response["Vary"]

    def test_the_answer_has_no_etag(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client())

        assert not response.has_header("ETag")

    def test_a_public_cache_of_the_target_does_not_reach_the_zone(
        self, root: Path
    ) -> None:
        _site(root, target_source='cache = {"public": True, "max_age": 60}\n')

        with routed(root):
            page_response = _client().get("/photo/3/")
            zone_response = _peek(_client())

        assert "public" in page_response["Cache-Control"]
        assert "public" not in zone_response["Cache-Control"]
        assert "no-store" in zone_response["Cache-Control"]


class TestMeta:
    """An intercept zone carries the head of the page that serves it."""

    _SELF = (
        'metadata = {"title": "Peek", "description": "A peek", '
        '"canonical": True, "robots": {"index": False}}\n'
    )

    def test_one_meta_op_carries_every_head_key(self, root: Path) -> None:
        _site(root, intercept_source=self._SELF)

        with routed(root):
            response = _peek(_client())

        assert _ops(response, "meta") == [
            {
                "op": "meta",
                "title": "Peek",
                "description": "A peek",
                "canonical": "http://testserver/photo/3/",
                "robots": "noindex",
            }
        ]

    def test_a_meta_op_closes_the_envelope_after_the_zone(self, root: Path) -> None:
        _site(root, intercept_source=self._SELF)

        with routed(root):
            response = _peek(_client())

        assert envelope_of(response).op_verbs() == ["morph", "meta"]

    def test_a_page_declaring_no_head_nulls_every_key(self, root: Path) -> None:
        _site(root)

        with routed(root):
            response = _peek(_client())

        assert _ops(response, "meta") == [
            {
                "op": "meta",
                "title": None,
                "description": None,
                "canonical": None,
                "robots": None,
            }
        ]

    def test_markup_in_a_title_stays_plain_text(self, root: Path) -> None:
        title = "<script>alert(1)</script> &amp; <b>x</b>"
        _site(root, intercept_source=f"metadata = {{'title': {title!r}}}\n")

        with routed(root):
            response = _peek(_client())

        assert json.loads(response.content)["ops"][1]["title"] == title

    def test_a_denial_carries_no_meta_op(self, root: Path) -> None:
        _site(root, intercept="login", intercept_source=self._SELF)

        with routed(root):
            response = _peek(_client())

        assert b"Peek" not in response.content

    def test_a_target_zone_keeps_its_envelope_without_a_meta_op(
        self, root: Path
    ) -> None:
        _site(
            root,
            intercept_source=self._SELF,
            target_source="metadata = {'title': 'Target'}\n",
            target_body='{% zone "side" %}<i>side</i>{% endzone %}',
        )

        with routed(root):
            response = _peek(_client(), "side")

        assert envelope_of(response).op_verbs() == ["morph"]

    def test_a_failing_callable_serves_the_zone_without_the_op(
        self, root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        _site(
            root,
            intercept_source=(
                "@page.metadata\ndef head():\n    raise LookupError('no head')\n"
            ),
        )

        with routed(root), caplog.at_level(logging.ERROR, "next.partial.patches"):
            response = _peek(_client())

        assert response.status_code == 200
        assert envelope_of(response).op_verbs() == ["morph"]
        assert "raised LookupError" in caplog.text

    def test_a_failing_callable_is_loud_under_debug(self, root: Path) -> None:
        _site(
            root,
            intercept_source=(
                "@page.metadata\ndef head():\n    raise LookupError('no head')\n"
            ),
        )

        with (
            routed(root),
            override_settings(DEBUG=True),
            pytest.raises(LookupError) as caught,
        ):
            _peek(_client())

        assert "zone response it serves" in caught.value.__notes__[0]


class TestPolls:
    """Every request from inside the modal passes both refusals again."""

    def test_a_poll_after_the_grant_is_revoked_is_a_403(self, root: Path) -> None:
        _site(root, intercept="add")
        client = NextClient()
        user = sign_in(client, "adder", "add_user")

        with routed(root):
            first = _peek(client, "peek-entries")
            user.user_permissions.remove(Permission.objects.get(codename="add_user"))
            second = _peek(client, "peek-entries")

        assert (first.status_code, second.status_code) == (200, 403)

    def test_a_poll_of_a_target_revoked_between_ticks_is_a_403(
        self, root: Path
    ) -> None:
        _site(root, target="view")
        client = NextClient()
        user = sign_in(client, "viewer", "view_user")

        with routed(root):
            first = _peek(client)
            user.user_permissions.remove(Permission.objects.get(codename="view_user"))
            second = _peek(client)

        assert (first.status_code, second.status_code) == (200, 403)

    def test_a_foreign_morph_of_an_intercept_zone_is_unknown(self, root: Path) -> None:
        _site(root)
        request = _client().get("/photo/3/").wsgi_request
        request.META.update(partial_meta("/photo/3/", zones="peek"))

        with routed(root), pytest.raises(UnknownZoneError):
            Patches(request).morph_foreign_zone("peek", "/photo/3/")


class TestRenderTarget:
    """A target with a `render()` body keeps its answer before any intercept."""

    @staticmethod
    def _target(root: Path, source: str) -> None:
        _site(root, target_source=source, target_body=None)

    def test_a_denying_render_is_a_403(self, root: Path) -> None:
        self._target(
            root,
            "from django.core.exceptions import PermissionDenied\n\n\n"
            "def render(request, **kwargs):\n    raise PermissionDenied\n",
        )

        with routed(root):
            response = _peek(_client())

        assert response.status_code == 403

    def test_a_redirecting_render_answers_as_is(self, root: Path) -> None:
        self._target(
            root,
            "from django.http import HttpResponseRedirect\n\n\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponseRedirect('/elsewhere/')\n",
        )

        with routed(root):
            response = _peek(_client())

        assert (response.status_code, response["Location"]) == (302, "/elsewhere/")

    def test_a_string_render_leaves_the_intercept_to_serve(self, root: Path) -> None:
        self._target(root, _RENDER)

        with routed(root):
            response = _peek(_client())

        assert envelope_of(response).zone_targets() == ["peek"]

    def test_render_runs_once_for_a_zone_get(self, root: Path) -> None:
        self._target(
            root,
            "from tests.support.access import note_call\n\n\n"
            "def render(request, **kwargs):\n    return note_call('render')\n",
        )
        CONTEXT_CALLS.clear()

        with routed(root):
            _peek(_client())

        assert CONTEXT_CALLS == ["render"]


class TestObjectPermissions:
    """An object permission binds the page that declares it, on either side."""

    @pytest.fixture(autouse=True)
    def _objects(self, scoped_models, isolated_permissions, object_permission_backend):
        add_perm("auth.edit_group", lambda user, obj: obj.name == f"t:{user.username}")
        add_perm("auth.view_group", lambda user, _obj: user.username != "eve")
        with bound_dependency(SCOPE_DEPENDENCY, lambda: "t"):
            yield

    @staticmethod
    def _rule(permission: str | None) -> str:
        if permission is None:
            return ""
        return (
            "from django.contrib.auth.models import Group\n\n"
            "from next.deps import DObject\n\n"
            f'page.permission_required("{permission}", obj=DObject[Group])\n'
        )

    @pytest.mark.parametrize("case", OBJECT_CASES, ids=lambda case: case.id)
    def test_the_status_a_row_earns(self, root: Path, case: ObjectCase) -> None:
        write_page(
            root,
            "grp/[int:group]",
            f"{PAGE_HEAD}{self._rule(case.target)}",
            body="<p>grp</p>",
        )
        write_page(
            root,
            "(.)grp/[int:group]",
            f"{PAGE_HEAD}{self._rule(case.intercept)}",
            body='{% zone "peek" %}<b>peek</b>{% endzone %}',
        )
        group = Group.objects.create(name=case.group)
        client = NextClient()
        sign_in(client, case.user)

        with routed(root):
            response = client.get_zones(f"/grp/{group.pk}/", "peek")

        assert response.status_code == case.status

    @pytest.mark.parametrize(
        ("user", "group", "status"),
        [("bob", "t:ada", 403), ("ada", "x:ada", 404)],
        ids=["refused", "hidden"],
    )
    def test_an_intercept_refusal_carries_no_store_and_the_partial_vary(
        self, root: Path, user: str, group: str, status: int
    ) -> None:
        write_page(root, "grp/[int:group]", PAGE_HEAD, body="<p>grp</p>")
        write_page(
            root,
            "(.)grp/[int:group]",
            f"{PAGE_HEAD}{self._rule('auth.edit_group')}",
            body='{% zone "peek" %}<b>peek</b>{% endzone %}',
        )
        row = Group.objects.create(name=group)
        client = NextClient()
        sign_in(client, user)

        with routed(root), override_settings(MIDDLEWARE=_CONDITIONAL_GET):
            response = client.get_zones(f"/grp/{row.pk}/", "peek")

        assert response.status_code == status
        assert _directives(response) >= _NO_STORE
        assert "X-Next-Zone" in response["Vary"]
        assert not response.has_header("ETag")

    @pytest.mark.parametrize(
        ("user", "group", "handler"),
        [("bob", "t:ada", "handler403"), ("ada", "x:ada", "handler404")],
        ids=["refused", "hidden"],
    )
    def test_a_shared_cache_from_the_error_handler_is_dropped(
        self, root: Path, monkeypatch, user: str, group: str, handler: str
    ) -> None:
        handlers = {"handler403": _shared_denial, "handler404": _shared_missing}
        monkeypatch.setattr(urls_namespaced, handler, handlers[handler], raising=False)
        write_page(root, "grp/[int:group]", PAGE_HEAD, body="<p>grp</p>")
        write_page(
            root,
            "(.)grp/[int:group]",
            f"{PAGE_HEAD}{self._rule('auth.edit_group')}",
            body='{% zone "peek" %}<b>peek</b>{% endzone %}',
        )
        row = Group.objects.create(name=group)
        client = NextClient()
        sign_in(client, user)

        with routed(root):
            response = client.get_zones(f"/grp/{row.pk}/", "peek")

        assert response.content in {b"denied", b"missing"}
        assert response["Cache-Control"] == "private, no-store"
        assert "X-Next-Zone" in response["Vary"]

    def test_a_hidden_row_under_debug_shows_the_technical_404(self, root: Path) -> None:
        write_page(root, "grp/[int:group]", PAGE_HEAD, body="<p>grp</p>")
        write_page(
            root,
            "(.)grp/[int:group]",
            f"{PAGE_HEAD}{self._rule('auth.edit_group')}",
            body='{% zone "peek" %}<b>peek</b>{% endzone %}',
        )
        row = Group.objects.create(name="x:ada")
        client = NextClient()
        sign_in(client, "ada")

        with routed(root), override_settings(DEBUG=True):
            response = client.get_zones(f"/grp/{row.pk}/", "peek")

        assert response.status_code == 404
        assert b"Page not found" in response.content
        assert response["Cache-Control"] == "private, no-store"


_NOTE_FORM = """
from django import forms

from next.forms import Form


class PeekNote(Form):
    note = forms.CharField(max_length=20)

    def on_valid(self, request):
        return None
"""
_NOTE_BODY = (
    '{% zone "peek" %}<h2>peek {{ pk }}</h2>'
    '{% form "peek_note" zone="peek" %}{{ form.note }}{{ form.note.errors }}'
    "{% endform %}{% endzone %}"
)


class TestForms:
    """A form in the modal posts the zone it lives in over the page it was opened on."""

    @pytest.fixture(autouse=True)
    def _base_dir(self, root: Path) -> Generator[None, None, None]:
        with override_settings(BASE_DIR=root.parent):
            yield

    @staticmethod
    def _post(
        client: NextClient, note: str = "", origin: str = "/photo/3/", **extra: object
    ) -> HttpResponse:
        return client.post_action(
            "peek_note", {"note": note}, origin=origin, partial="peek", **extra
        )

    def _modal(self, root: Path, **kwargs: str) -> None:
        _site(root, intercept_source=_NOTE_FORM, peek=_NOTE_BODY, **kwargs)

    def test_an_invalid_post_morphs_the_intercept_zone(self, root: Path) -> None:
        self._modal(root, intercept="add")

        with _mounted(root):
            response = self._post(_client("adder"))

        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["peek"]
        assert "required" in envelope.html_for_zone("peek").lower()
        assert envelope.form_meta()["valid"] is False

    def test_a_valid_post_with_no_result_morphs_the_intercept_zone(
        self, root: Path
    ) -> None:
        self._modal(root, intercept="add")

        with _mounted(root):
            response = self._post(_client("adder"), "hello")

        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["peek"]
        assert "peek 3" in envelope.html_for_zone("peek")

    @pytest.mark.parametrize("note", ["", "hello"], ids=["invalid", "valid"])
    def test_the_intercept_rule_is_checked_once_per_post(
        self, root: Path, monkeypatch: pytest.MonkeyPatch, note: str
    ) -> None:
        self._modal(root, intercept="add")
        served = root / "(.)photo" / "[int:pk]" / "page.py"
        checked: list[Path] = []
        original = partial_intercept.refusal

        def spy(page_path, *args, **kwargs):
            checked.append(page_path)
            return original(page_path, *args, **kwargs)

        monkeypatch.setattr(partial_intercept, "refusal", spy)

        with _mounted(root):
            response = self._post(_client("adder"), note)

        assert envelope_of(response).zone_targets() == ["peek"]
        assert checked.count(served) == 1

    def test_a_visitor_without_the_intercept_rule_is_refused(self, root: Path) -> None:
        self._modal(root, intercept="add")

        with _mounted(root):
            response = self._post(_client("visitor"), "hello")

        assert response.status_code == 403

    def test_a_forged_origin_cannot_lift_the_owner_rule(self, root: Path) -> None:
        self._modal(root, intercept="add")
        write_page(root, "elsewhere", f"{PAGE_HEAD}page.public()\n", body="<p>e</p>")

        with _mounted(root):
            response = self._post(_client("visitor"), "hello", origin="/elsewhere/")

        assert response.status_code == 403

    def test_the_posted_origin_adds_its_own_rule(self, root: Path) -> None:
        self._modal(root, target="view", intercept="add")

        with _mounted(root):
            response = self._post(_client("adder"), "hello")

        assert response.status_code == 403

    def test_a_missing_csrf_token_is_a_403(self, root: Path) -> None:
        self._modal(root, intercept="add")
        client = _client("adder", enforce_csrf_checks=True)

        with _mounted(root):
            response = self._post(client, "hello")

        assert response.status_code == 403

    @pytest.mark.parametrize("note", ["", "hello"], ids=["invalid", "valid"])
    def test_an_action_of_the_target_meets_the_intercept_rule_on_its_outcome(
        self, root: Path, note: str
    ) -> None:
        _site(root, intercept="add", target_source=_NOTE_FORM)
        write_page(
            root, "(.)photo/[int:pk]", f"{PAGE_HEAD}{_RULES['add']}", body=_NOTE_BODY
        )

        with _mounted(root):
            response = self._post(_client("viewer"), note)

        assert response.status_code == 403


def _origin_post(user, zones: str | None = "peek"):
    request = RequestFactory().post(
        "/_next/form/x/",
        data={ORIGIN_FIELD_NAME: "/photo/3/"},
        **partial_meta(zones=zones),
    )
    request.user = user
    return request


class TestMorphZone:
    """`Patches.morph_zone` serves a zone of the intercept on a zone request."""

    def test_an_admitted_request_morphs_the_intercept_zone(self, root: Path) -> None:
        _site(root, intercept="add")
        user = get_user_model().objects.create_user("adder")
        user.user_permissions.add(Permission.objects.get(codename="add_user"))

        with _mounted(root):
            envelope = Patches(_origin_post(user)).morph_zone("peek").envelope()

        assert envelope.ops[0].html.startswith('<div data-next-zone="peek"><h2>peek 3')

    def test_two_morphs_authorize_and_build_the_intercept_once(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _site(root)
        served = root / "(.)photo" / "[int:pk]" / "page.py"
        built: list[Path] = []
        original = patches_module.page_manager.build_render_context

        def spy(page_path, *args, **kwargs):
            built.append(page_path)
            return original(page_path, *args, **kwargs)

        monkeypatch.setattr(patches_module.page_manager, "build_render_context", spy)

        with _mounted(root):
            envelope = (
                Patches(_origin_post(AnonymousUser(), "peek,peek-entries"))
                .morph_zone("peek")
                .morph_zone("peek-entries")
                .envelope()
            )

        assert [op.op for op in envelope.ops] == ["morph", "morph"]
        assert built.count(served) == 1

    def test_the_render_of_an_intercept_never_runs(self, root: Path) -> None:
        _site(
            root, intercept_source=_RENDER.replace('return "dynamic"', "raise OSError")
        )
        target = root / "photo" / "[int:pk]" / "page.py"
        served = root / "(.)photo" / "[int:pk]" / "page.py"

        with _mounted(root):
            intercepts.publish(root, [Intercept(target, served, "(.)")])
            envelope = (
                Patches(_origin_post(AnonymousUser())).morph_zone("peek").envelope()
            )

        assert envelope.ops[0].html.startswith('<div data-next-zone="peek"><h2>peek 3')

    def test_a_refused_intercept_raises_and_records_nothing(self, root: Path) -> None:
        _site(root, intercept="add")
        patches = Patches(_origin_post(AnonymousUser()))

        with _mounted(root), pytest.raises(ForeignPageNotAuthorizedError) as caught:
            patches.morph_zone("peek")

        assert caught.value.status_code == 302
        assert patches.envelope().ops == ()

    def test_a_request_naming_no_zone_never_consults_an_intercept(
        self, root: Path
    ) -> None:
        _site(root)

        with _mounted(root), pytest.raises(UnknownZoneError):
            Patches(_origin_post(AnonymousUser(), None)).morph_zone("peek")

    def test_a_zone_of_the_origin_stays_with_the_origin(self, root: Path) -> None:
        _site(root, target_body='{% zone "side" %}<i>side</i>{% endzone %}')

        with _mounted(root):
            envelope = (
                Patches(_origin_post(AnonymousUser(), "side"))
                .morph_zone("side")
                .envelope()
            )

        assert envelope.ops[0].html == '<div data-next-zone="side"><i>side</i></div>'
