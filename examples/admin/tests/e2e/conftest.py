from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from e2e_support.suite import base_url, browser_context_args, ignore_browser_suite


__all__ = ["base_url", "browser_context_args"]


def pytest_ignore_collect(config: pytest.Config) -> bool | None:
    return ignore_browser_suite(config)


@pytest.fixture()
def signed_in(context, live_server, admin_user) -> None:
    """Hand the browser a session cookie for the superuser."""
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
                "domain": urlparse(live_server.url).hostname,
                "path": "/",
            }
        ]
    )
