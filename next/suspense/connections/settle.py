"""Settle lazy request state before parallel jobs share it, then spot their writes."""

import functools
from dataclasses import dataclass
from typing import Final

from django.contrib.auth import get_user
from django.contrib.messages.api import get_messages
from django.contrib.messages.storage.base import BaseStorage
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.utils.functional import SimpleLazyObject

from next.suspense.markers import Effects
from next.transactions import SAFE_METHODS
from next.utils import middleware_installed


AUTHENTICATION: Final = "django.contrib.auth.middleware.AuthenticationMiddleware"
SESSIONS: Final = "django.contrib.sessions.middleware.SessionMiddleware"
MESSAGES: Final = "django.contrib.messages.middleware.MessageMiddleware"
CSRF: Final = "django.middleware.csrf.CsrfViewMiddleware"
_CSRF_COOKIE: Final = "CSRF_COOKIE"
_CSRF_NEEDS_UPDATE: Final = "CSRF_COOKIE_NEEDS_UPDATE"


@dataclass(frozen=True, slots=True)
class Settled:
    """The request state a side-effect check compares a finished job against."""

    session_key: str | None = None
    session_modified: bool = False
    messages_used: bool = False
    csrf_secret: str | None = None


def _session(request: HttpRequest) -> SessionBase | None:
    if not middleware_installed(SESSIONS):
        return None
    session = getattr(request, "session", None)
    return session if isinstance(session, SessionBase) else None


def _messages(request: HttpRequest) -> BaseStorage | None:
    if not middleware_installed(MESSAGES):
        return None
    storage = get_messages(request)
    return storage if isinstance(storage, BaseStorage) else None


def request_state(request: HttpRequest) -> Settled:
    """Return the session key, the flags and the CSRF secret, read without loading.

    The secret is the `CSRF_COOKIE` that `CsrfViewMiddleware` sends back as the cookie.
    """
    session = _session(request)
    storage = _messages(request)
    secret = request.META.get(_CSRF_COOKIE)
    return Settled(
        session_key=None if session is None else session.session_key,
        session_modified=session is not None and session.modified,
        messages_used=storage is not None and storage.used,
        csrf_secret=secret if isinstance(secret, str) else None,
    )


def _read_user(session: SessionBase, user: object) -> object:
    """Mark the session read, as resolving the user would have, and return the user."""
    session.accessed = True
    return user


def _settle_quietly(request: HttpRequest, session: SessionBase | None) -> None:
    """Load the session and resolve the user without marking the session read.

    A job that reads either marks it read itself, so `Vary: Cookie` follows a real read.
    """
    if session is None:
        return
    was = session.accessed
    session.keys()
    if middleware_installed(AUTHENTICATION) and not was:
        user = get_user(request)
        lazy = SimpleLazyObject(functools.partial(_read_user, session, user))
        vars(request)["user"] = lazy
    elif middleware_installed(AUTHENTICATION):
        getattr(getattr(request, "user", None), "is_authenticated", None)
    session.accessed = was


def _seed_csrf(request: HttpRequest) -> None:
    """Mint the CSRF secret jobs share without asking the response to send it.

    A job reading the token raises the flag, so a page whose jobs never do stays shared.
    """
    if _CSRF_COOKIE not in request.META:
        get_token(request)
        del request.META[_CSRF_NEEDS_UPDATE]


def settle_request(
    request: HttpRequest, *, stream: bool, shared: bool = False
) -> Settled:
    """Force the lazy request state jobs share, on the request thread before the first.

    A `shared` page loads its session unmarked, and only a stream marks the CSRF cookie.
    """
    session = _session(request)
    if shared:
        _settle_quietly(request, session)
    else:
        if middleware_installed(AUTHENTICATION):
            getattr(getattr(request, "user", None), "is_authenticated", None)
        if session is not None:
            session.keys()
        storage = _messages(request)
        if storage is not None:
            len(storage)
    if request.method not in SAFE_METHODS:
        getattr(request, "POST", None)
        getattr(request, "FILES", None)
    if stream and middleware_installed(CSRF):
        get_token(request)
    elif middleware_installed(CSRF):
        _seed_csrf(request)
    return request_state(request)


def effects(request: HttpRequest, before: Settled) -> Effects:
    """Return the request-state writes a job made since `before`, by attribute reads.

    A CSRF secret first minted by the job is no write, a rotated one is.
    """
    after = request_state(request)
    return Effects(
        session_modified=after.session_modified and not before.session_modified,
        session_key_changed=after.session_key != before.session_key,
        messages_used=after.messages_used and not before.messages_used,
        csrf_changed=before.csrf_secret is not None
        and after.csrf_secret != before.csrf_secret,
    )


__all__ = ["Settled", "effects", "request_state", "settle_request"]
