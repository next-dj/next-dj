from collections.abc import Iterable
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    QueryDict,
)

from next.deps import Depends, resolver
from next.forms import (
    ActionGuard,
    ActionRegistration,
    BaseForm,
    Form,
    FormWizard,
    ModelForm,
    PermissionOutcome,
    RegistryFormActionBackend,
    action,
)
from next.forms.backends import build_action_guard
from next.forms.base import _hook_func
from next.forms.checks import check_action_guard_permissions
from next.forms.dispatch import (
    FormActionDispatch,
    _call_check_permissions,
    _call_has_object_permission,
    _check_access,
    _emit_form_access_denied,
    _normalize_permission,
    _redirect_to_login,
)
from next.forms.manager import form_action_manager
from next.forms.signals import action_dispatched, form_access_denied
from tests.support.cases import (
    PERMISSION_HOOK_BAD_TYPE,
    PERMISSION_HOOK_RAISE,
    PERMISSION_OUTCOME_CASES,
    PermissionHookCase,
)


User = get_user_model()


class GuardedLoginForm(Form):
    """Form requiring an authenticated user."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Declarative login guard."""

        login_required = True


class GuardedPermissionForm(Form):
    """Form requiring a specific permission."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Declarative permission guard."""

        permission_required = "auth.add_user"


class GuardedBase(Form):
    """Abstract base whose guard must protect concrete subclasses."""

    class Meta:
        """Guarded but not registered itself."""

        abstract = True
        login_required = True


class InheritedGuardForm(GuardedBase):
    """Concrete form inheriting the base guard without its own Meta."""

    name = django_forms.CharField(max_length=50)


class GuardedWizardStep(Form):
    """Wizard step form, never a standalone action."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Step form is abstract."""

        abstract = True


class GuardedWizard(FormWizard):
    """Wizard requiring an authenticated user on every step POST."""

    class Meta:
        """One guarded step."""

        steps: ClassVar = [("identity", GuardedWizardStep)]
        login_required = True

    def done(
        self, request: HttpRequest, cleaned_data: dict[str, Any]
    ) -> HttpResponseRedirect:
        """Redirect once the only step validates."""
        return HttpResponseRedirect("/thanks/")


@action("guarded_handler_action", login_required=True)
def guarded_handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("guarded ok")


@action(
    "permission_handler_action",
    permission_required=["auth.add_user", "auth.change_user"],
)
def permission_handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("perm ok")


class _PermUser:
    """Authenticated stub with a fixed has_perms answer."""

    is_authenticated = True

    def __init__(self, *, allowed: bool) -> None:
        self._allowed = allowed
        self.checked: tuple[str, ...] | None = None

    def has_perms(self, perms: Iterable[str]) -> bool:
        self.checked = tuple(perms)
        return self._allowed


class TestBuildActionGuard:
    """build_action_guard normalisation rules."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param({}, None, id="unset"),
            pytest.param(
                {"login_required": False, "permission_required": None},
                None,
                id="explicit-defaults",
            ),
            pytest.param(
                {"permission_required": "auth.add_user"},
                ActionGuard(permissions=("auth.add_user",)),
                id="string-permission",
            ),
            pytest.param(
                {"permission_required": ["a.x", "a.y"]},
                ActionGuard(permissions=("a.x", "a.y")),
                id="iterable-permissions",
            ),
            pytest.param(
                {"login_required": True},
                ActionGuard(login_required=True),
                id="login-required-alone",
            ),
        ],
    )
    def test_normalises_declared_requirements(
        self,
        kwargs: dict[str, bool | str | list[str] | None],
        expected: ActionGuard | None,
    ) -> None:
        assert build_action_guard(**kwargs) == expected


class TestGuardRegistration:
    """Guard config declared on Meta or @action lands in ActionMeta."""

    @pytest.mark.parametrize(
        ("action_name", "expected"),
        [
            pytest.param(
                "guarded_login_form",
                ActionGuard(login_required=True),
                id="meta-login-required",
            ),
            pytest.param(
                "guarded_permission_form",
                ActionGuard(permissions=("auth.add_user",)),
                id="meta-permission-required",
            ),
            pytest.param(
                "inherited_guard_form",
                ActionGuard(login_required=True),
                id="guard-inherited-unlike-abstract",
            ),
            pytest.param(
                "guarded_wizard",
                ActionGuard(login_required=True),
                id="wizard-meta-guard",
            ),
            pytest.param(
                "guarded_handler_action",
                ActionGuard(login_required=True),
                id="action-login-kwarg",
            ),
            pytest.param(
                "permission_handler_action",
                ActionGuard(permissions=("auth.add_user", "auth.change_user")),
                id="action-permission-kwarg",
            ),
            pytest.param("simple_form", None, id="unguarded-action"),
        ],
    )
    def test_guard_lands_in_registry(
        self, action_name: str, expected: ActionGuard | None
    ) -> None:
        meta = form_action_manager.default_backend.get_meta(action_name)
        assert meta is not None
        assert meta.get("guard") == expected


class TestRedirectToLogin:
    """_redirect_to_login mirrors django.contrib.auth.views.redirect_to_login."""

    def test_appends_next_to_login_url(self) -> None:
        resp = _redirect_to_login("/boards/1/")
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/boards/1/"

    def test_preserves_existing_login_url_query(self, settings) -> None:
        settings.LOGIN_URL = "/login/?theme=dark"
        resp = _redirect_to_login("/x/")
        assert "theme=dark" in resp.url
        assert "next=/x/" in resp.url


class TestCheckAccess:
    """_check_access enforces AccessMixin semantics on the dispatch request."""

    def test_request_without_user_redirects_to_login(self, rf) -> None:
        request = rf.post("/_next/form/x/", {"_next_form_origin": "/boards/2/"})
        denial = _check_access(request, ActionGuard(login_required=True))
        assert denial is not None
        assert denial.url == "/accounts/login/?next=/boards/2/"

    def test_anonymous_user_redirects_with_root_fallback(self, rf) -> None:
        request = rf.post("/_next/form/x/")
        request.user = AnonymousUser()
        denial = _check_access(request, ActionGuard(login_required=True))
        assert denial is not None
        assert denial.url == "/accounts/login/?next=/"

    def test_offsite_origin_falls_back_to_root(self, rf) -> None:
        request = rf.post(
            "/_next/form/x/", {"_next_form_origin": "https://evil.example/"}
        )
        denial = _check_access(request, ActionGuard(login_required=True))
        assert denial is not None
        assert denial.url == "/accounts/login/?next=/"

    def test_authenticated_without_permission_raises(self, rf) -> None:
        request = rf.post("/_next/form/x/")
        request.user = _PermUser(allowed=False)
        with pytest.raises(PermissionDenied):
            _check_access(request, ActionGuard(permissions=("auth.add_user",)))

    def test_authenticated_with_permission_passes(self, rf) -> None:
        request = rf.post("/_next/form/x/")
        user = _PermUser(allowed=True)
        request.user = user
        guard = ActionGuard(permissions=("auth.add_user",))
        assert _check_access(request, guard) is None
        assert user.checked == ("auth.add_user",)

    def test_login_only_guard_passes_authenticated_user(self) -> None:
        request = HttpRequest()
        request.user = _PermUser(allowed=False)
        assert _check_access(request, ActionGuard(login_required=True)) is None


@pytest.mark.django_db()
class TestGuardDispatchViaClient:
    """Guard enforcement runs before any form binding in dispatch."""

    def _post(
        self, client, action_name: str, data: dict[str, str] | None = None
    ) -> HttpResponse:
        url = form_action_manager.get_action_url(action_name)
        payload = {"_next_form_origin": "/", **(data or {})}
        return client.post(url, data=payload, follow=False)

    def test_anonymous_post_redirects_to_login(self, client_no_csrf) -> None:
        resp = self._post(client_no_csrf, "guarded_login_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/"

    def test_authenticated_post_dispatches(self, client_no_csrf) -> None:
        client_no_csrf.force_login(User.objects.create_user("ada", password="pw"))
        resp = self._post(client_no_csrf, "guarded_login_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/"

    def test_anonymous_permission_post_redirects_to_login(self, client_no_csrf) -> None:
        resp = self._post(client_no_csrf, "guarded_permission_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/"

    def test_authenticated_without_permission_gets_403(self, client_no_csrf) -> None:
        client_no_csrf.force_login(User.objects.create_user("bob", password="pw"))
        resp = self._post(client_no_csrf, "guarded_permission_form", {"name": "Ada"})
        assert resp.status_code == 403

    def test_superuser_passes_permission_guard(self, client_no_csrf) -> None:
        client_no_csrf.force_login(
            User.objects.create_superuser("root", "root@example.com", "pw")
        )
        resp = self._post(client_no_csrf, "guarded_permission_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/"

    def test_inherited_guard_protects_subclass(self, client_no_csrf) -> None:
        resp = self._post(client_no_csrf, "inherited_guard_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/"

    def test_wizard_step_post_is_guarded(self, client_no_csrf) -> None:
        url = form_action_manager.get_action_url("guarded_wizard")
        resp = client_no_csrf.post(
            url,
            data={"_next_form_origin": "/request/identity/", "name": "Ada"},
            follow=False,
        )
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/request/identity/"

    def test_wizard_step_post_dispatches_for_authenticated_user(
        self, client_no_csrf
    ) -> None:
        client_no_csrf.force_login(User.objects.create_user("eve", password="pw"))
        url = form_action_manager.get_action_url("guarded_wizard")
        resp = client_no_csrf.post(
            url,
            data={"_next_form_origin": "/request/identity/", "name": "Ada"},
            follow=False,
        )
        assert resp.status_code == 302
        assert resp.url == "/thanks/"

    def test_handler_action_is_guarded(self, client_no_csrf) -> None:
        resp = self._post(client_no_csrf, "guarded_handler_action")
        assert resp.status_code == 302
        assert resp.url == "/accounts/login/?next=/"

    def test_handler_action_runs_for_authenticated_user(self, client_no_csrf) -> None:
        client_no_csrf.force_login(User.objects.create_user("kim", password="pw"))
        resp = self._post(client_no_csrf, "guarded_handler_action")
        assert resp.status_code == 200
        assert resp.content == b"guarded ok"

    def test_method_check_still_precedes_the_guard(self, client_no_csrf) -> None:
        url = form_action_manager.get_action_url("guarded_login_form")
        assert client_no_csrf.get(url).status_code == 405


class TestPermissionGuardCheck:
    """next.W060 warns on permission_required without django.contrib.auth."""

    def test_auth_installed_is_clean(self) -> None:
        assert check_action_guard_permissions() == []

    def test_permission_without_auth_app_warns(self, settings) -> None:
        settings.INSTALLED_APPS = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        messages = check_action_guard_permissions()
        assert messages
        assert all(m.id == "next.W060" for m in messages)
        flagged = " ".join(m.msg for m in messages)
        assert "guarded_permission_form" in flagged
        assert "guarded_login_form" not in flagged


_FAKE_FILE = "/fake/myapp/forms.py"
_PAYWALL = "/paywall/"


def _hook_value(case: PermissionHookCase) -> object:
    """Map a case's symbolic return into a real value the hook should yield."""
    if case.hook_return == "redirect":
        return HttpResponseRedirect(_PAYWALL)
    if case.hook_return == "response_403":
        return HttpResponse("nope", status=403)
    return case.hook_return


def _register_matrix_form(backend: RegistryFormActionBackend, form_class: type) -> None:
    backend.register_action(
        ActionRegistration(
            name="matrix_action",
            file_path=_FAKE_FILE,
            scope="shared",
            form_class=form_class,
        )
    )


def _matrix_request(mock_http_request) -> HttpRequest:
    post = QueryDict(mutable=True)
    post["name"] = "Ada"
    post["_next_form_origin"] = "/"
    return mock_http_request(method="POST", POST=post, FILES=None)


class _ViewHookForm(Form):
    """Bench-style form whose check_permissions is patched per matrix row."""

    name = django_forms.CharField(max_length=50)


class _ObjectHookForm(Form):
    """Form whose has_object_permission is patched per matrix row."""

    name = django_forms.CharField(max_length=50)


class TestNormalizePermission:
    """_normalize_permission maps a hook return to a denial response or None."""

    def test_none_allows(self) -> None:
        assert _normalize_permission(None) is None

    def test_true_allows(self) -> None:
        assert _normalize_permission(True) is None

    def test_false_raises_permission_denied(self) -> None:
        with pytest.raises(PermissionDenied):
            _normalize_permission(False)

    def test_http_response_short_circuits(self) -> None:
        response = HttpResponse("body", status=403)
        assert _normalize_permission(response) is response

    def test_redirect_short_circuits(self) -> None:
        redirect = HttpResponseRedirect(_PAYWALL)
        assert _normalize_permission(redirect) is redirect

    def test_bad_type_raises_type_error_with_message(self) -> None:
        with pytest.raises(TypeError, match="permission hook returned unsupported"):
            _normalize_permission({"not": "allowed"})


class TestCallCheckPermissions:
    """_call_check_permissions resolves and invokes the view-level classmethod."""

    def test_resolves_without_var_keyword(self, mock_http_request) -> None:
        """A hook taking only request resolves and returns its outcome."""

        class _Form(BaseForm):
            @classmethod
            def check_permissions(cls, request: HttpRequest) -> PermissionOutcome:
                assert request is sentinel
                return False

        sentinel = mock_http_request(method="POST")
        result = _call_check_permissions(_Form, sentinel, {"id": 5}, deps=({}, []))
        assert result is False

    def test_spreads_url_kwargs_into_var_keyword(self, mock_http_request) -> None:
        """A hook declaring **url_kwargs receives the url kwargs by the spread branch."""
        seen: dict[str, object] = {}

        class _Form(BaseForm):
            @classmethod
            def check_permissions(cls, **url_kwargs: object) -> PermissionOutcome:
                seen.update(url_kwargs)
                return None

        request = mock_http_request(method="POST")
        result = _call_check_permissions(_Form, request, {"board_id": 7}, deps=({}, []))
        assert result is None
        assert seen == {"board_id": 7}


class TestCallHasObjectPermission:
    """_call_has_object_permission resolves and invokes the bound instance hook."""

    def test_resolves_without_var_keyword(self, mock_http_request) -> None:
        """A hook taking only request resolves and returns its outcome."""

        class _Form(Form):
            name = django_forms.CharField(max_length=10, required=False)

            def has_object_permission(self, request: HttpRequest) -> PermissionOutcome:
                assert request is sentinel
                return False

        sentinel = mock_http_request(method="POST")
        form = _Form()
        result = _call_has_object_permission(form, sentinel, {"id": 5}, deps=({}, []))
        assert result is False

    def test_spreads_url_kwargs_into_var_keyword(self, mock_http_request) -> None:
        """A hook declaring **url_kwargs receives the url kwargs by the spread branch."""
        seen: dict[str, object] = {}

        class _Form(Form):
            name = django_forms.CharField(max_length=10, required=False)

            def has_object_permission(self, **url_kwargs: object) -> PermissionOutcome:
                seen.update(url_kwargs)
                return None

        request = mock_http_request(method="POST")
        form = _Form()
        result = _call_has_object_permission(
            form, request, {"board_id": 7}, deps=({}, [])
        )
        assert result is None
        assert seen == {"board_id": 7}


class TestEmitFormAccessDenied:
    """_emit_form_access_denied fires only when a receiver is connected."""

    def test_emits_when_receiver_present(self, mock_http_request) -> None:
        seen: list[dict[str, object]] = []

        def receiver(**kwargs: object) -> None:
            seen.append(kwargs)

        request = mock_http_request(method="POST")
        form_access_denied.connect(receiver)
        try:
            _emit_form_access_denied(
                request, "act", "uid-1", layer="view", reason="denied"
            )
        finally:
            form_access_denied.disconnect(receiver)
        assert len(seen) == 1
        assert seen[0]["layer"] == "view"
        assert seen[0]["reason"] == "denied"
        assert seen[0]["action_name"] == "act"
        assert seen[0]["uid"] == "uid-1"
        assert seen[0]["request"] is request

    def test_silent_when_no_receiver(self, mock_http_request) -> None:
        """With no receiver connected the helper makes no send and raises nothing."""
        request = mock_http_request(method="POST")
        assert form_access_denied.receivers == []
        _emit_form_access_denied(request, "act", None, layer="object", reason="raised")


class TestBaseSentinelHooks:
    """The base no-op hook bodies cover their `return None` lines directly."""

    def test_base_form_check_permissions_returns_none(self) -> None:
        assert BaseForm.check_permissions() is None

    def test_base_form_has_object_permission_returns_none(self) -> None:
        # A concrete Form that never overrides the hook calls the base body.
        class _Plain(Form):
            pass

        assert _Plain().has_object_permission() is None

    def test_wizard_check_permissions_returns_none(self) -> None:
        assert FormWizard.check_permissions() is None


class TestDetectionFlags:
    """__init_subclass__ stamps the hook flags via __func__ identity."""

    def test_unguarded_form_has_both_flags_false(self) -> None:
        class _Plain(Form):
            pass

        assert _Plain._has_check_permissions is False
        assert _Plain._has_object_permission is False

    def test_view_hook_sets_only_view_flag(self) -> None:
        class _ViewOnly(Form):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return None

        assert _ViewOnly._has_check_permissions is True
        assert _ViewOnly._has_object_permission is False

    def test_object_hook_sets_only_object_flag(self) -> None:
        class _ObjOnly(Form):
            def has_object_permission(self) -> PermissionOutcome:
                return None

        assert _ObjOnly._has_check_permissions is False
        assert _ObjOnly._has_object_permission is True

    def test_guarded_subclass_of_unguarded_flips_own_flag(self) -> None:
        class _Base(Form):
            pass

        class _Guarded(_Base):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return None

        assert _Base._has_check_permissions is False
        assert _Guarded._has_check_permissions is True

    def test_unguarded_subclass_of_guarded_inherits_the_hook(self) -> None:
        """A concrete subclass of a guarded abstract base inherits the True flag.

        The subclass does not re-declare the method, so __func__ identity still
        resolves to the base override, keeping the flag True.
        """

        class _GuardedBase(Form):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return None

            class Meta:
                abstract = True

        class _Concrete(_GuardedBase):
            name = django_forms.CharField(max_length=10)

        assert _Concrete._has_check_permissions is True

    def test_subclass_redeclaring_hook_sets_own_flag(self) -> None:
        """A subclass re-declaring the hook stamps its own flag True even when the body delegates to the base sentinel, because detection keys on __func__ identity."""

        class _GuardedBase(Form):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return False

            class Meta:
                abstract = True

        class _Reset(_GuardedBase):
            name = django_forms.CharField(max_length=10)

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return _hook_func(BaseForm.check_permissions)(cls)

        assert _GuardedBase._has_check_permissions is True
        assert _Reset._has_check_permissions is True


@pytest.mark.django_db()
class TestViewHookReturnContract:
    """The view-level check_permissions return contract, full dispatch."""

    @pytest.mark.parametrize(
        "case",
        PERMISSION_OUTCOME_CASES,
        ids=[c.id for c in PERMISSION_OUTCOME_CASES],
    )
    def test_matrix_via_dispatch(
        self, case: PermissionHookCase, mock_http_request, monkeypatch
    ) -> None:
        value = _hook_value(case)

        def hook(cls, request=None):
            if value is PERMISSION_HOOK_RAISE:
                raise PermissionDenied
            if value is PERMISSION_HOOK_BAD_TYPE:
                return ["bad"]
            return value

        monkeypatch.setattr(_ViewHookForm, "check_permissions", classmethod(hook))
        monkeypatch.setattr(_ViewHookForm, "_has_check_permissions", True)

        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, _ViewHookForm)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)

        if case.raises_type_error:
            with pytest.raises(TypeError):
                FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
            return
        if case.raises_permission_denied:
            with pytest.raises(PermissionDenied):
                FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
            return

        response = FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
        assert response.status_code == case.expected_status
        if case.expected_redirect is not None:
            assert response.url == case.expected_redirect


@pytest.mark.django_db()
class TestObjectHookReturnContract:
    """The object-level has_object_permission return contract, full dispatch."""

    @pytest.mark.parametrize(
        "case",
        PERMISSION_OUTCOME_CASES,
        ids=[c.id for c in PERMISSION_OUTCOME_CASES],
    )
    def test_matrix_via_dispatch(
        self, case: PermissionHookCase, mock_http_request, monkeypatch
    ) -> None:
        value = _hook_value(case)

        def hook(self, request=None):
            if value is PERMISSION_HOOK_RAISE:
                raise PermissionDenied
            if value is PERMISSION_HOOK_BAD_TYPE:
                return ["bad"]
            return value

        monkeypatch.setattr(_ObjectHookForm, "has_object_permission", hook)
        monkeypatch.setattr(_ObjectHookForm, "_has_object_permission", True)

        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, _ObjectHookForm)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)

        if case.raises_type_error:
            with pytest.raises(TypeError):
                FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
            return
        if case.raises_permission_denied:
            with pytest.raises(PermissionDenied):
                FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
            return

        response = FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
        assert response.status_code == case.expected_status
        if case.expected_redirect is not None:
            assert response.url == case.expected_redirect


class _SpyDBViewForm(Form):
    """View-hook form reading the DB and recording every call."""

    name = django_forms.CharField(max_length=50)
    calls: ClassVar[list[bool]] = []

    @classmethod
    def check_permissions(cls, request: HttpRequest) -> PermissionOutcome:
        cls.calls.append(True)
        return Group.objects.filter(name="gate").exists()


class _UnguardedControlForm(Form):
    """Unguarded control whose dispatch never touches a permission hook."""

    name = django_forms.CharField(max_length=50)


@pytest.mark.django_db()
class TestGuardedViewFormDispatch:
    """A DB-reading view hook gates the dispatch and runs only when flagged."""

    def setup_method(self) -> None:
        _SpyDBViewForm.calls.clear()

    def _dispatch(self, form_class: type, mock_http_request) -> HttpResponse:
        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, form_class)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)
        return FormActionDispatch.dispatch(backend, request, "matrix_action", meta)

    def test_db_hook_denies_when_row_absent(self, mock_http_request) -> None:
        with pytest.raises(PermissionDenied):
            self._dispatch(_SpyDBViewForm, mock_http_request)
        assert _SpyDBViewForm.calls == [True]

    def test_db_hook_allows_when_row_present(self, mock_http_request) -> None:
        Group.objects.create(name="gate")
        response = self._dispatch(_SpyDBViewForm, mock_http_request)
        assert response.status_code == 302
        assert _SpyDBViewForm.calls == [True]

    def test_unguarded_control_never_calls_a_hook(self, mock_http_request) -> None:
        response = self._dispatch(_UnguardedControlForm, mock_http_request)
        assert response.status_code == 302


class _OrderingForm(Form):
    """Form with both a static login guard and a view hook spy."""

    name = django_forms.CharField(max_length=50)
    check_calls: ClassVar[list[bool]] = []

    class Meta:
        """Static login guard fires before the dynamic hook."""

        login_required = True

    @classmethod
    def check_permissions(cls, request: HttpRequest) -> PermissionOutcome:
        cls.check_calls.append(True)
        return None


@pytest.mark.django_db()
class TestStaticGuardPrecedesDynamicHook:
    """The static ActionGuard runs before the dynamic view hook."""

    def test_anonymous_post_redirects_without_reaching_check_permissions(
        self, mock_http_request
    ) -> None:
        _OrderingForm.check_calls.clear()
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="ordering_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=_OrderingForm,
                guard=ActionGuard(login_required=True),
            )
        )
        meta = backend.get_meta("ordering_action")
        assert meta is not None
        post = QueryDict(mutable=True)
        post["name"] = "Ada"
        post["_next_form_origin"] = "/"
        request = mock_http_request(method="POST", POST=post, FILES=None)
        request.user = AnonymousUser()

        response = FormActionDispatch.dispatch(
            backend, request, "ordering_action", meta
        )
        assert response.status_code == 302
        assert response.url == "/accounts/login/?next=/"
        # The static guard short-circuited before the hook could run.
        assert _OrderingForm.check_calls == []


def _board_factory(**_kwargs: object) -> type:
    return _SpyDBViewForm


def _board_tuple_factory(**_kwargs: object) -> tuple[type, dict[str, object]]:
    return _ObjectOwnerModelForm, {}


class _ObjectOwnerModelForm(ModelForm):
    """ModelForm whose object hook reads self.instance loaded from the URL."""

    seen_names: ClassVar[list[str]] = []

    class Meta:
        """Bind to Group and load by name from the origin URL."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url = "name"

    def has_object_permission(self, request: HttpRequest) -> PermissionOutcome:
        type(self).seen_names.append(self.instance.name)
        return self.instance.name == "editors"


@pytest.mark.django_db()
class TestFactoryFormClassRunsHooks:
    """View and object hooks fire on the resolved class from a factory."""

    def setup_method(self) -> None:
        _SpyDBViewForm.calls.clear()
        _ObjectOwnerModelForm.seen_names.clear()

    def test_callable_factory_runs_view_hook_on_resolved_class(
        self, mock_http_request
    ) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="factory_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=_board_factory,
            )
        )
        meta = backend.get_meta("factory_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)
        with pytest.raises(PermissionDenied):
            FormActionDispatch.dispatch(backend, request, "factory_action", meta)
        assert _SpyDBViewForm.calls == [True]

    def test_tuple_factory_runs_object_hook_on_resolved_class(
        self, mock_http_request
    ) -> None:
        Group.objects.create(name="editors")
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="tuple_factory_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=_board_tuple_factory,
            )
        )
        meta = backend.get_meta("tuple_factory_action")
        assert meta is not None
        post = QueryDict(mutable=True)
        post["name"] = "editors"
        post["_next_form_origin"] = "/groups/editors/"
        request = mock_http_request(method="POST", POST=post, FILES=None)
        response = FormActionDispatch.dispatch(
            backend, request, "tuple_factory_action", meta
        )
        assert response.status_code == 302
        assert _ObjectOwnerModelForm.seen_names == ["editors"]


class _OwnerOnlyModelForm(ModelForm):
    """Owner-only edit gate over a Group loaded from the URL."""

    class Meta:
        """Load the Group by its name from the origin URL kwargs."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url = "name"

    def has_object_permission(self, request: HttpRequest) -> PermissionOutcome:
        return self.instance.name == "owned"


class _AnonInspectingModelForm(ModelForm):
    """Object hook that inspects an AnonymousUser passed through the framework."""

    saw_anonymous: ClassVar[list[bool]] = []

    class Meta:
        """Load the Group by name."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        instance_from_url = "name"

    def has_object_permission(self, request: HttpRequest) -> PermissionOutcome:
        type(self).saw_anonymous.append(request.user.is_anonymous)
        return not request.user.is_anonymous


@pytest.mark.django_db()
class TestObjectLevelOwnerEdit:
    """has_object_permission gates a ModelForm edit on the bound instance."""

    def _dispatch(
        self, form_class: type, name: str, mock_http_request, *, user=None
    ) -> HttpResponse:
        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, form_class)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        post = QueryDict(mutable=True)
        post["name"] = name
        post["_next_form_origin"] = f"/groups/{name}/"
        request = mock_http_request(method="POST", POST=post, FILES=None)
        if user is not None:
            request.user = user
        return FormActionDispatch.dispatch(backend, request, "matrix_action", meta)

    def test_owner_passes(self, mock_http_request) -> None:
        Group.objects.create(name="owned")
        response = self._dispatch(_OwnerOnlyModelForm, "owned", mock_http_request)
        assert response.status_code == 302

    def test_non_owner_denied_with_bare_403(self, mock_http_request) -> None:
        """A denied object hook raises PermissionDenied, never an origin re-render."""
        Group.objects.create(name="stranger")
        with pytest.raises(PermissionDenied):
            self._dispatch(_OwnerOnlyModelForm, "stranger", mock_http_request)

    def test_http404_precedes_the_hook(self, mock_http_request) -> None:
        """A missing instance raises Http404 before the object hook runs."""
        _AnonInspectingModelForm.saw_anonymous.clear()
        with pytest.raises(Http404):
            self._dispatch(_AnonInspectingModelForm, "ghost", mock_http_request)
        assert _AnonInspectingModelForm.saw_anonymous == []

    def test_anonymous_user_passes_through_to_hook(self, mock_http_request) -> None:
        Group.objects.create(name="owned")
        _AnonInspectingModelForm.saw_anonymous.clear()
        with pytest.raises(PermissionDenied):
            self._dispatch(
                _AnonInspectingModelForm,
                "owned",
                mock_http_request,
                user=AnonymousUser(),
            )
        assert _AnonInspectingModelForm.saw_anonymous == [True]


class _DepCacheReuseForm(Form):
    """View and object hooks plus get_initial share one Depends provider."""

    name = django_forms.CharField(max_length=50)
    resolutions: ClassVar[list[str]] = []

    @classmethod
    def get_initial(cls, tenant: str = Depends("tenant")) -> dict[str, Any]:
        assert tenant == "acme"
        return {}

    @classmethod
    def check_permissions(
        cls, request: HttpRequest, tenant: str = Depends("tenant")
    ) -> PermissionOutcome:
        assert tenant == "acme"
        return None

    def on_valid(
        self, request: HttpRequest, tenant: str = Depends("tenant")
    ) -> HttpResponseRedirect:
        assert tenant == "acme"
        return HttpResponseRedirect("/")


@pytest.mark.django_db()
class TestDepCacheReuse:
    """A Depends provider resolved in the hook is reused by later phases."""

    def test_provider_resolves_once_across_hook_and_on_valid(
        self, mock_http_request
    ) -> None:
        _DepCacheReuseForm.resolutions.clear()
        seen: dict[str, object] = {}

        def receiver(**kwargs: object) -> None:
            seen.update(kwargs)

        def tenant_provider() -> str:
            _DepCacheReuseForm.resolutions.append("tenant")
            return "acme"

        resolver.register_dependency("tenant", tenant_provider)
        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, _DepCacheReuseForm)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)

        action_dispatched.connect(receiver)
        try:
            response = FormActionDispatch.dispatch(
                backend, request, "matrix_action", meta
            )
        finally:
            action_dispatched.disconnect(receiver)
            resolver._dependency_callables.pop("tenant", None)

        assert response.status_code == 302
        # The named provider ran exactly once for the whole dispatch.
        assert _DepCacheReuseForm.resolutions == ["tenant"]
        assert seen["dep_cache"]["tenant"] == "acme"


class _DeniedForm(Form):
    """Form whose hooks deny by the parametrized reason for signal assertions."""

    name = django_forms.CharField(max_length=50)


@pytest.mark.django_db()
class TestFormAccessDeniedPayload:
    """form_access_denied fires only on dynamic denials with the right payload."""

    @pytest.fixture()
    def captured(self):
        events: list[dict[str, object]] = []

        def receiver(**kwargs: object) -> None:
            events.append(kwargs)

        form_access_denied.connect(receiver)
        try:
            yield events
        finally:
            form_access_denied.disconnect(receiver)

    @pytest.mark.parametrize(
        ("layer", "value", "reason", "raises"),
        [
            pytest.param("view", False, "denied", True, id="view-denied"),
            pytest.param(
                "view", PERMISSION_HOOK_RAISE, "raised", True, id="view-raised"
            ),
            pytest.param("view", "response", "response", False, id="view-response"),
            pytest.param("object", False, "denied", True, id="object-denied"),
            pytest.param(
                "object", PERMISSION_HOOK_RAISE, "raised", True, id="object-raised"
            ),
            pytest.param("object", "response", "response", False, id="object-response"),
        ],
    )
    def test_payload_layer_and_reason(
        self, layer, value, reason, raises, captured, mock_http_request, monkeypatch
    ) -> None:
        def make_outcome():
            if value is PERMISSION_HOOK_RAISE:
                raise PermissionDenied
            if value == "response":
                return HttpResponse("nope", status=403)
            return value

        if layer == "view":

            def view_hook(cls, request=None):
                return make_outcome()

            monkeypatch.setattr(
                _DeniedForm, "check_permissions", classmethod(view_hook)
            )
            monkeypatch.setattr(_DeniedForm, "_has_check_permissions", True)
        else:

            def object_hook(self, request=None):
                return make_outcome()

            monkeypatch.setattr(_DeniedForm, "has_object_permission", object_hook)
            monkeypatch.setattr(_DeniedForm, "_has_object_permission", True)

        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, _DeniedForm)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)

        if raises:
            with pytest.raises(PermissionDenied):
                FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
        else:
            FormActionDispatch.dispatch(backend, request, "matrix_action", meta)

        assert len(captured) == 1
        assert captured[0]["layer"] == layer
        assert captured[0]["reason"] == reason

    def test_bad_type_emits_no_signal(
        self, captured, mock_http_request, monkeypatch
    ) -> None:
        def view_hook(cls, request=None):
            return ["bad"]

        monkeypatch.setattr(_DeniedForm, "check_permissions", classmethod(view_hook))
        monkeypatch.setattr(_DeniedForm, "_has_check_permissions", True)
        backend = RegistryFormActionBackend()
        _register_matrix_form(backend, _DeniedForm)
        meta = backend.get_meta("matrix_action")
        assert meta is not None
        request = _matrix_request(mock_http_request)
        with pytest.raises(TypeError):
            FormActionDispatch.dispatch(backend, request, "matrix_action", meta)
        assert captured == []

    def test_static_guard_denial_emits_no_signal(
        self, captured, mock_http_request
    ) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="static_only_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=_DeniedForm,
                guard=ActionGuard(login_required=True),
            )
        )
        meta = backend.get_meta("static_only_action")
        assert meta is not None
        post = QueryDict(mutable=True)
        post["name"] = "Ada"
        post["_next_form_origin"] = "/"
        request = mock_http_request(method="POST", POST=post, FILES=None)
        request.user = AnonymousUser()
        response = FormActionDispatch.dispatch(
            backend, request, "static_only_action", meta
        )
        assert response.status_code == 302
        assert captured == []
