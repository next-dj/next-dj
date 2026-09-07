import pytest
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from e2e_support.settings import CONTEXT_ARGS


def pytest_ignore_collect(config):
    """Skip the browser suite unless its plugin was asked for.

    A bare `pytest tests/` carries neither the plugin nor, usually, playwright, and
    skipping quietly beats a collection error in every other workflow.
    """
    if not config.pluginmanager.has_plugin("e2e_support.browser"):
        return True
    return None


@pytest.fixture(scope="session")
def base_url(live_server) -> str:
    """Point playwright at the live server instead of pytest-base-url's default."""
    return live_server.url


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Pin everything that would otherwise drift between machines."""
    return {**browser_context_args, **CONTEXT_ARGS}


@pytest.fixture()
def signed_in(context, admin_user) -> None:
    """Hand the browser a session cookie for the superuser.

    A session built server-side lands in the same transactional database the live
    server reads, so the browser starts authenticated without walking the login
    form. A session-scoped `storage_state` cannot do the job here because the
    database is flushed between tests and the session row would go with it.
    """
    session = SessionStore()
    session[SESSION_KEY] = str(admin_user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = admin_user.get_session_auth_hash()
    session.save()
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session.session_key,
                "domain": "localhost",
                "path": "/",
            }
        ]
    )
