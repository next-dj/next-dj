import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import HttpRequest, HttpResponse
from django.template import Context
from django.test import override_settings

from next.forms import Form, action


pytest.importorskip("allauth")

allauth_core_context = pytest.importorskip("allauth.core.context")

_ALLAUTH_APPS = ["allauth", "allauth.account"]
_ALLAUTH_OVERRIDES: dict[str, object] = {
    "ROOT_URLCONF": "tests.compat.urls_allauth",
    "AUTHENTICATION_BACKENDS": [
        "django.contrib.auth.backends.ModelBackend",
        "allauth.account.auth_backends.AuthenticationBackend",
    ],
    "ACCOUNT_LOGIN_METHODS": {"username"},
    "ACCOUNT_SIGNUP_FIELDS": ["username*", "email*", "password1*", "password2*"],
    "LOGIN_REDIRECT_URL": "/welcome/",
}


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Migrate the allauth tables into the session test database."""
    with (
        django_db_blocker.unblock(),
        override_settings(
            MIDDLEWARE=[
                *settings.MIDDLEWARE,
                "allauth.account.middleware.AccountMiddleware",
            ],
        ),
        override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, *_ALLAUTH_APPS]),
    ):
        call_command("migrate", verbosity=0, interactive=False)


@pytest.fixture()
def allauth_env(_isolate_form_registries, db):
    """Run one test with allauth installed, routed, and middleware-wrapped.

    Yields the lazily imported `allauth.account.forms` module, which can
    only load while allauth sits in INSTALLED_APPS.
    """
    with (
        override_settings(
            MIDDLEWARE=[
                *settings.MIDDLEWARE,
                "allauth.account.middleware.AccountMiddleware",
            ],
            **_ALLAUTH_OVERRIDES,
        ),
        override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, *_ALLAUTH_APPS]),
    ):
        yield pytest.importorskip("allauth.account.forms")


@pytest.fixture()
def factory_actions(allauth_env):
    """Register login and signup actions backed by allauth form factories."""
    account_forms = allauth_env
    account_utils = pytest.importorskip("allauth.account.utils")
    account_settings = pytest.importorskip("allauth.account.app_settings")

    def login_form_factory(
        request: HttpRequest,
    ) -> tuple[type, dict[str, HttpRequest]]:
        return account_forms.LoginForm, {"request": request}

    @action("compat_allauth_login", form_class=login_form_factory)
    def login_handler(
        request: HttpRequest, form: account_forms.LoginForm
    ) -> HttpResponse:
        return form.login(request, redirect_url="/welcome/")

    def signup_form_factory() -> tuple[type, dict[str, dict[str, object]]]:
        return account_forms.SignupForm, {"initial": {}}

    @action("compat_allauth_signup", form_class=signup_form_factory)
    def signup_handler(
        request: HttpRequest, form: account_forms.SignupForm
    ) -> HttpResponse:
        user, response = form.try_save(request)
        if response is not None:
            return response
        return account_utils.complete_signup(
            request, user, account_settings.EMAIL_VERIFICATION, None
        )


@pytest.fixture()
def subclass_action(allauth_env):
    """Register a thin next subclass of the allauth LoginForm."""
    account_forms = allauth_env

    class CompatAllauthLoginSub(Form, account_forms.LoginForm):
        """Login subclass that recovers the request from the allauth context."""

        def __init__(
            self,
            *args: object,
            request: HttpRequest | None = None,
            **kwargs: object,
        ) -> None:
            if request is None:
                request = allauth_core_context.request
            super().__init__(*args, request=request, **kwargs)

        def on_valid(self, request: HttpRequest) -> HttpResponse:
            """Defer to the allauth login flow."""
            return self.login(request, redirect_url=None)

    return CompatAllauthLoginSub


@pytest.fixture()
def user(allauth_env):
    """Create a persisted account for the login scenarios."""
    return get_user_model().objects.create_user(
        username="ada", email="ada@example.com", password="correct-horse-staple"
    )


class TestFactoryLogin:
    """(FormClass, init_kwargs) factory wiring the allauth LoginForm."""

    def test_invalid_credentials_rerender(self, factory_actions, user, next_client):
        resp = next_client.post_action(
            "compat_allauth_login", {"login": "ada", "password": "wrong"}, origin="/"
        )
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        content = resp.content.decode()
        assert "not correct" in content
        assert 'value="ada"' in content

    def test_valid_credentials_log_in(self, factory_actions, user, next_client):
        resp = next_client.post_action(
            "compat_allauth_login",
            {"login": "ada", "password": "correct-horse-staple"},
            origin="/",
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/welcome/"
        assert next_client.session.get("_auth_user_id") == str(user.pk)


class TestFactorySignup:
    """Factory signup with neutral init kwargs and the allauth completion flow."""

    def test_password_mismatch_rerenders(self, factory_actions, next_client):
        resp = next_client.post_action(
            "compat_allauth_signup",
            {
                "username": "grace",
                "email": "grace@example.com",
                "password1": "first-pass-1234",
                "password2": "second-pass-5678",
            },
            origin="/",
        )
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        assert 'value="grace"' in resp.content.decode()

    def test_valid_signup_creates_user_and_logs_in(
        self, factory_actions, next_client, mailoutbox
    ):
        resp = next_client.post_action(
            "compat_allauth_signup",
            {
                "username": "grace",
                "email": "grace@example.com",
                "password1": "correct-horse-staple",
                "password2": "correct-horse-staple",
            },
            origin="/",
        )
        assert resp.status_code == 302
        created = get_user_model().objects.get(username="grace")
        assert next_client.session.get("_auth_user_id") == str(created.pk)
        assert len(mailoutbox) == 1


class TestThinSubclassLogin:
    """next.forms subclass of the allauth LoginForm with on_valid."""

    def test_renders_dynamic_login_field(
        self, subclass_action, form_engine, csrf_request
    ):
        source = '{% form "compat_allauth_login_sub" %}{{ form.as_div }}{% endform %}'
        html = form_engine.from_string(source).render(
            Context({"request": csrf_request})
        )
        assert 'name="login"' in html
        assert 'name="password"' in html

    def test_invalid_credentials_rerender(self, subclass_action, user, next_client):
        resp = next_client.post_action(
            "compat_allauth_login_sub",
            {"login": "ada", "password": "wrong"},
            origin="/",
        )
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        assert "not correct" in resp.content.decode()

    def test_valid_credentials_redirect_to_login_redirect_url(
        self, subclass_action, user, next_client
    ):
        resp = next_client.post_action(
            "compat_allauth_login_sub",
            {"login": "ada", "password": "correct-horse-staple"},
            origin="/",
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/welcome/"
        assert next_client.session.get("_auth_user_id") == str(user.pk)
