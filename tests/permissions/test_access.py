from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from next.components import component
from next.permissions.access import (
    _listed_middleware,
    answer,
    check,
    declared,
    declared_files,
    forget_login_default,
    holds,
    login_by_default,
    login_middleware,
    login_redirect,
    require_permission,
    signed_in,
)
from next.permissions.markers import OPEN, AccessRule, Denial, Requirement
from tests.support import PermissionHolder
from tests.support.access import LOGIN_REQUIRED_MIDDLEWARE


class LoginRequiredSubclass(LoginRequiredMiddleware):
    """A project middleware that inherits the login requirement."""


class SignInMiddleware(LoginRequiredMiddleware):
    """A project middleware with its own login page and field."""

    redirect_field_name = "to"

    def get_login_url(self, view_func) -> str:
        return getattr(view_func, "login_url", "/sign-in/")


class NoNextMiddleware(LoginRequiredMiddleware):
    """A project middleware that sends to the login page without a return path."""

    redirect_field_name = None


_SUBCLASS = f"{__name__}.LoginRequiredSubclass"

_NO_NEXT = f"{__name__}.NoNextMiddleware"

_SIGN_IN = f"{__name__}.SignInMiddleware"

_SOURCE = Path(__file__)

_LOGIN = Requirement("login", _SOURCE)

_VIEW = Requirement("permission", _SOURCE, ("auth.view_group",))

_EDIT_NOTE = Requirement("permission", _SOURCE, ("auth.edit_note",), "note")


class _SignedInWithoutPerms:
    is_authenticated = True


class TestDeclarations:
    """Every declaration binds to the file that calls it."""

    def test_login_and_permission_bind_to_the_caller(self) -> None:
        component.login_required()
        component.permission_required(("auth.view_group", "auth.add_group"), obj="x")

        assert declared(_SOURCE).requirements == (
            Requirement("login", _SOURCE),
            Requirement(
                "permission", _SOURCE, ("auth.view_group", "auth.add_group"), "x"
            ),
        )
        assert declared_files() == (_SOURCE,)

    def test_a_list_of_names_is_kept_as_a_tuple(self) -> None:
        require_permission(_SOURCE, ["auth.view_group"], None)

        assert declared(_SOURCE).requirements[0].perms == ("auth.view_group",)

    @pytest.mark.parametrize(
        ("perm", "message"),
        [
            ((), "a permission name or a tuple"),
            (42, "a permission name or a tuple"),
            (("auth.view_group", ""), "only non-empty permission names"),
            (("auth.view_group", 1), "only non-empty permission names"),
            ("", "only non-empty permission names"),
        ],
        ids=["empty_tuple", "int", "empty_name", "non_str", "empty_str"],
    )
    def test_a_malformed_permission_raises(self, perm, message) -> None:
        with pytest.raises(TypeError, match=message):
            require_permission(_SOURCE, perm, None)
        assert declared(_SOURCE) is OPEN

    @pytest.mark.parametrize("obj", ["", 1], ids=["empty", "int"])
    def test_a_malformed_object_name_raises(self, obj) -> None:
        with pytest.raises(TypeError, match="names a context"):
            component.permission_required("auth.view_group", obj=obj)
        assert declared(_SOURCE) is OPEN


class TestCheck:
    """The login comes first, then the plain permissions, then those on an object."""

    def test_an_open_rule_passes_anyone(self) -> None:
        assert check(None, AccessRule(), pytest.fail) is None

    def test_an_anonymous_user_is_denied_on_the_first_requirement(self) -> None:
        rule = AccessRule((_VIEW, _LOGIN))

        assert check(None, rule, pytest.fail) == Denial("login", _VIEW)

    def test_a_missing_plain_permission_denies_before_any_lookup(self) -> None:
        rule = AccessRule((_EDIT_NOTE, _VIEW))
        user = PermissionHolder("auth.edit_note")

        denial = check(user, rule, pytest.fail)

        assert denial == Denial("permission", _VIEW)
        assert user.asked == [("auth.view_group",)]

    def test_the_object_permission_gets_the_looked_up_value(self) -> None:
        rule = AccessRule((_LOGIN, _EDIT_NOTE))
        user = PermissionHolder("auth.edit_note")

        assert check(user, rule, {"note": "the note"}.__getitem__) is None
        assert user.asked == [("auth.edit_note", "the note")]

    def test_a_refused_object_permission_names_its_requirement(self) -> None:
        rule = AccessRule((_EDIT_NOTE,))

        denial = check(PermissionHolder(), rule, lambda name: name)

        assert denial == Denial("permission", _EDIT_NOTE)

    @pytest.mark.parametrize(
        "requirement", [_VIEW, _EDIT_NOTE], ids=["plain", "object"]
    )
    def test_a_user_without_has_perm_is_refused(self, requirement) -> None:
        rule = AccessRule((requirement,))

        denial = check(_SignedInWithoutPerms(), rule, lambda name: name)

        assert denial == Denial("permission", requirement)

    def test_a_login_alone_passes_a_signed_in_user(self) -> None:
        assert check(_SignedInWithoutPerms(), AccessRule((_LOGIN,)), str) is None

    def test_without_a_lookup_an_object_permission_refuses(self) -> None:
        user = PermissionHolder("auth.edit_note", "auth.view_group")

        assert check(user, AccessRule((_VIEW,))) is None
        assert check(user, AccessRule((_VIEW, _EDIT_NOTE))) == Denial(
            "permission", _EDIT_NOTE
        )
        assert user.asked == [("auth.view_group",), ("auth.view_group",)]

    def test_an_object_looked_up_as_none_refuses_without_asking(self) -> None:
        rule = AccessRule((_LOGIN, _EDIT_NOTE))
        user = PermissionHolder("auth.edit_note")

        denial = check(user, rule, {"note": None}.__getitem__)

        assert denial == Denial("permission", _EDIT_NOTE)
        assert user.asked == []


class TestTagAndCheckAgree:
    """`{% has_perm %}` and `check()` give one answer, a `None` object refusing both."""

    @pytest.mark.parametrize(
        ("obj", "granted"), [("the note", True), (None, False)], ids=["row", "none"]
    )
    def test_one_answer(self, obj, granted) -> None:
        user = PermissionHolder("auth.edit_note")
        tag = Template(
            "{% has_perm 'auth.edit_note' note %}yes{% else %}no{% endhas_perm %}"
        )

        rendered = tag.render(Context({"user": user, "note": obj}))
        denial = check(user, AccessRule((_EDIT_NOTE,)), {"note": obj}.__getitem__)

        assert (rendered == "yes", denial is None) == (granted, granted)
        assert holds(user, ("auth.edit_note",), obj) is granted


class TestSignedIn:
    """Only an authenticated user counts as signed in, no user at all does not."""

    @pytest.mark.parametrize(
        ("user", "expected"),
        [
            (PermissionHolder(), True),
            (AnonymousUser(), False),
            (None, False),
            (object(), False),
        ],
        ids=["authenticated", "anonymous", "no-user", "no-flag"],
    )
    def test_the_flag_decides(self, user, expected) -> None:
        assert signed_in(user) is expected


_REQUEST = RequestFactory().get("/notes/")


class TestAnswer:
    """A login denial answers with the login redirect, any other with a 403."""

    def test_a_login_denial_redirects_with_next(self) -> None:
        response = answer(Denial("login", _LOGIN), _REQUEST, "/notes/")

        assert response["Location"] == "/accounts/login/?next=/notes/"

    def test_a_permission_denial_raises_403(self) -> None:
        with pytest.raises(PermissionDenied):
            answer(Denial("permission", _VIEW), _REQUEST, "/notes/")


class TestLoginRedirect:
    """The redirect carries `next` the way `LoginRequiredMiddleware` builds it."""

    def test_the_next_url_joins_the_login_url(self) -> None:
        response = login_redirect(_REQUEST, "/notes/?a=1")

        assert response["Location"] == "/accounts/login/?next=/notes/%3Fa%3D1"

    @override_settings(LOGIN_URL="/in/?from=site")
    def test_the_login_query_is_kept(self) -> None:
        response = login_redirect(_REQUEST, "/notes/")

        assert response["Location"] == "/in/?from=site&next=/notes/"

    @pytest.mark.usefixtures("cap_redirects")
    def test_a_next_url_past_the_location_cap_is_dropped(self) -> None:
        response = login_redirect(_REQUEST, "/" + "x" * 20000)

        assert response["Location"] == "/accounts/login/"

    @pytest.mark.parametrize(
        ("login_url", "location"),
        [
            pytest.param(
                "https://auth.example.com/in/",
                "https://auth.example.com/in/?next=http%3A//testserver/notes/",
                id="another-host",
            ),
            pytest.param(
                "http://auth.example.com/in/",
                "http://auth.example.com/in/?next=http%3A//testserver/notes/",
                id="another-host-same-scheme",
            ),
            pytest.param(
                "http://testserver/in/",
                "http://testserver/in/?next=/notes/",
                id="same-host",
            ),
            pytest.param(
                "https://testserver/in/",
                "https://testserver/in/?next=http%3A//testserver/notes/",
                id="another-scheme",
            ),
        ],
    )
    def test_a_login_elsewhere_gets_an_absolute_next(self, login_url, location) -> None:
        with override_settings(LOGIN_URL=login_url):
            response = login_redirect(_REQUEST, "/notes/")

        assert response["Location"] == location

    def test_a_listed_subclass_names_the_page_and_the_field(self) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _SIGN_IN]):
            response = login_redirect(_REQUEST, "/notes/")

        assert response["Location"] == "/sign-in/?to=/notes/"

    @override_settings(LOGIN_URL="/in/?from=a%20b")
    def test_a_subclass_without_a_field_keeps_the_login_url(self) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _NO_NEXT]):
            response = login_redirect(_REQUEST, "/notes/")

        expected = redirect_to_login("/notes/", settings.LOGIN_URL, None)
        assert response["Location"] == expected["Location"] == "/in/?from=a%20b"

    def test_the_resolved_view_reaches_the_subclass(self) -> None:
        def view(request):
            return None

        view.login_url = "/staff/"
        request = RequestFactory().get("/notes/")
        request.resolver_match = type("Match", (), {"func": view})()

        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _SIGN_IN]):
            response = login_redirect(request, "/notes/")

        assert response["Location"] == "/staff/?to=/notes/"

    def test_the_built_middleware_answers_nothing_itself(self) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _SIGN_IN]):
            middleware = _listed_middleware()

        assert middleware is not None
        with pytest.raises(Http404):
            middleware.get_response(_REQUEST)


class TestLoginMiddleware:
    """The entry that lists the login middleware, the one a table names as a source."""

    def test_the_first_matching_entry(self) -> None:
        with override_settings(
            MIDDLEWARE=["a.B", _SUBCLASS, LOGIN_REQUIRED_MIDDLEWARE]
        ):
            assert login_middleware() == _SUBCLASS

    def test_none_without_the_middleware(self) -> None:
        assert login_middleware() is None

    def test_none_without_contrib_auth(self) -> None:
        installed = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        with override_settings(
            INSTALLED_APPS=installed, MIDDLEWARE=[LOGIN_REQUIRED_MIDDLEWARE]
        ):
            assert login_middleware() is None


class TestLoginByDefault:
    """The setting or the middleware turns the default login on, nothing else does."""

    def test_the_suite_runs_without_it(self) -> None:
        assert login_by_default() is False

    @pytest.mark.parametrize("path", [LOGIN_REQUIRED_MIDDLEWARE, _SUBCLASS])
    def test_the_class_or_a_subclass_counts(self, path) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, path]):
            assert login_by_default() is True
        assert login_by_default() is False

    @pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False)])
    def test_the_setting_counts(self, value, expected) -> None:
        with override_settings(NEXT_FRAMEWORK={"LOGIN_REQUIRED": value}):
            assert login_by_default() is expected
        assert login_by_default() is False

    def test_the_setting_and_the_middleware_agree(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"LOGIN_REQUIRED": True},
            MIDDLEWARE=[*settings.MIDDLEWARE, LOGIN_REQUIRED_MIDDLEWARE],
        ):
            assert login_by_default() is True

    def test_an_unimportable_entry_is_skipped(self) -> None:
        with override_settings(MIDDLEWARE=["no.such.Middleware", _SUBCLASS]):
            assert login_by_default() is True

    def test_a_function_entry_does_not_count(self) -> None:
        with override_settings(MIDDLEWARE=[f"{__name__}.login_redirect"]):
            assert login_by_default() is False

    def test_without_contrib_auth_it_is_inactive(self) -> None:
        installed = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        with override_settings(
            INSTALLED_APPS=installed,
            MIDDLEWARE=[*settings.MIDDLEWARE, LOGIN_REQUIRED_MIDDLEWARE],
        ):
            assert login_by_default() is False

    def test_another_setting_keeps_the_memo(self) -> None:
        with override_settings(MIDDLEWARE=[LOGIN_REQUIRED_MIDDLEWARE]):
            assert login_by_default() is True
            settings.MIDDLEWARE = []
            forget_login_default(setting="DEBUG")
            assert login_by_default() is True
            forget_login_default(setting="MIDDLEWARE")
            assert login_by_default() is False

    def test_installed_apps_drop_the_memo(self) -> None:
        installed = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.auth"
        ]
        with override_settings(MIDDLEWARE=[LOGIN_REQUIRED_MIDDLEWARE]):
            assert login_by_default() is True
            with override_settings(INSTALLED_APPS=installed):
                assert login_by_default() is False
            assert login_by_default() is True
