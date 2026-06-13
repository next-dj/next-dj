from collections.abc import Iterable
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import ActionGuard, Form, FormWizard, action
from next.forms.backends import build_action_guard
from next.forms.checks import check_action_guard_permissions
from next.forms.dispatch import _check_access, _redirect_to_login
from next.forms.manager import form_action_manager


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
