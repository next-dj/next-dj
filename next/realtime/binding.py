"""Swappable binding of a realtime ticket to the browser that negotiated it."""

import functools
import inspect
import secrets
from collections.abc import Mapping
from typing import Final, override

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.base import SessionBase
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.module_loading import import_string

from next.conf.scopes import scope_value
from next.identity import (
    LiveIdentity,
    TicketBinding,
    account_parts,
    account_subject,
    live_store,
    session_account,
    session_alive,
    session_user,
    stored_session,
    subject_digest,
)

from .errors import InvalidTicketError


class SessionBinding(TicketBinding):
    """Bind a ticket to the Django session key, which every stored engine keeps."""

    @override
    def issue(self, request: HttpRequest) -> str:
        """Bind the session, creating one when the cookie would fail the handshake."""
        session = getattr(request, "session", None)
        if session is None:
            msg = "WebSocket ticket negotiation requires Django SessionMiddleware."
            raise InvalidTicketError(msg)
        session_key = session.session_key
        if session_key is None or not session_alive(session_key):
            session.create()
            session.modified = True
            fresh = session.session_key
            session_key = None if fresh == session_key else fresh
        if not isinstance(session_key, str) or not session_key:
            msg = "A persistent Django session is required for WebSocket tickets."
            raise InvalidTicketError(msg)
        return session_key

    @override
    def credential(self, cookies: Mapping[str, str]) -> str | None:
        return cookies.get(settings.SESSION_COOKIE_NAME) or None

    @override
    def subject(self, credential: str) -> str | None:
        return credential

    @override
    def live(self, credential: str) -> LiveIdentity | None:
        return stored_session(credential)


class SignedCookieBinding(TicketBinding):
    """Bind a signed-in user by pk and auth hash, an anonymous visitor by a cookie.

    Neither changes when the signed-cookie engine re-signs the session on a save.
    """

    cookie_name = "next_visitor"
    salt = "next.realtime.visitor.v1"

    @override
    def issue(self, request: HttpRequest) -> str:
        """Bind an account whose user still loads, or the visitor the reply sets.

        A signed-in negotiation drops the visitor, so a planted one ends at login.
        """
        account = self._signed_in(getattr(request, "session", None))
        subject = None if account is None else self.subject(account)
        if subject is not None:
            request.META[_VISITOR_META] = ""
            return subject
        visitor = self._visitor(request.COOKIES.get(self.cookie_name, ""))
        if visitor is None:
            visitor = secrets.token_urlsafe(16)
        request.META[_VISITOR_META] = visitor
        return f"{_VISITOR}{visitor}"

    @override
    def respond(self, request: HttpRequest, response: HttpResponseBase) -> None:
        """Set the visitor cookie under the session cookie's scope, or delete it."""
        visitor = request.META.get(_VISITOR_META)
        if not isinstance(visitor, str):
            return
        if not visitor:
            if self.cookie_name in request.COOKIES:
                response.delete_cookie(
                    self.cookie_name,
                    path=settings.SESSION_COOKIE_PATH,
                    domain=settings.SESSION_COOKIE_DOMAIN,
                    samesite=settings.SESSION_COOKIE_SAMESITE,
                )
            return
        response.set_cookie(
            self.cookie_name,
            self._signer().sign(visitor),
            max_age=(
                None
                if settings.SESSION_EXPIRE_AT_BROWSER_CLOSE
                else settings.SESSION_COOKIE_AGE
            ),
            path=settings.SESSION_COOKIE_PATH,
            domain=settings.SESSION_COOKIE_DOMAIN,
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite=settings.SESSION_COOKIE_SAMESITE,
        )

    @override
    def credential(self, cookies: Mapping[str, str]) -> str | None:
        """Read the account of a session whose user still loads, else the visitor."""
        session_key = cookies.get(settings.SESSION_COOKIE_NAME)
        if session_key:
            live = live_store(session_key, deadline=False)
            account = None if live is None else self._signed_in(live[0])
            if account is not None:
                return account
        visitor = self._visitor(cookies.get(self.cookie_name, ""))
        return None if visitor is None else f"{_VISITOR}{visitor}"

    @override
    def subject(self, credential: str) -> str | None:
        """Drop the backend from an account, keep a visitor, refuse anything else."""
        if credential.startswith(_VISITOR):
            return credential
        parts = account_parts(credential)
        return None if parts is None else account_subject(parts[0], parts[2])

    @override
    def live(self, credential: str) -> LiveIdentity | None:
        """Reload an account by pk and auth hash, a visitor stays anonymous."""
        if credential.startswith(_VISITOR):
            return LiveIdentity(session_user(SessionBase()), None)
        parts = account_parts(credential)
        if parts is None:
            return None
        account = SessionBase()
        account.update(
            {
                HASH_SESSION_KEY: parts[0],
                BACKEND_SESSION_KEY: parts[1],
                SESSION_KEY: parts[2],
            }
        )
        user = session_user(account)
        if getattr(user, "is_authenticated", False):
            return LiveIdentity(user, None)
        return None

    def _signed_in(self, session: SessionBase | None) -> str | None:
        """Return the account a session holds while its user still loads, else None."""
        account = None if session is None else session_account(session)
        if account is None or self.live(account) is None:
            return None
        return account

    def _signer(self) -> signing.TimestampSigner:
        return signing.TimestampSigner(salt=self.salt)

    def _visitor(self, value: str) -> str | None:
        """Return the visitor id a signed cookie value carries, None once invalid."""
        if not value:
            return None
        try:
            return self._signer().unsign(value, max_age=settings.SESSION_COOKIE_AGE)
        except signing.BadSignature:
            return None


_VISITOR = "visitor:"
_VISITOR_META = "NEXT_REALTIME_VISITOR"
SUBJECT_SALT: Final = "next.realtime.subject.v1"
"""The salt of the opaque subject a realtime stream and its socket agree on."""


def ticket_binding() -> TicketBinding:
    """Return the binding `REALTIME['TICKET_BINDING']` names, built once per path."""
    path = scope_value("REALTIME", "TICKET_BINDING")
    if not isinstance(path, str):
        msg = "NEXT_FRAMEWORK['REALTIME']['TICKET_BINDING'] must be a dotted path."
        raise ImproperlyConfigured(msg)
    return _binding(path)


def opaque_subject(credential: str) -> str:
    """Return a keyed digest of the subject a credential proves, empty for none.

    It stays stable across reconnects and never yields the credential back.
    """
    subject = ticket_binding().subject(credential)
    return "" if subject is None else subject_digest(subject, SUBJECT_SALT)


def binding_class(path: object) -> type[TicketBinding] | None:
    """Return the concrete `TicketBinding` subclass a dotted path names, or None."""
    if not isinstance(path, str):
        return None
    try:
        klass = import_string(path)
    except ImportError:
        return None
    if (
        isinstance(klass, type)
        and issubclass(klass, TicketBinding)
        and not inspect.isabstract(klass)
    ):
        return klass
    return None


@functools.cache
def _binding(path: str) -> TicketBinding:
    """Build the binding a dotted path names, refusing any other value."""
    klass = binding_class(path)
    if klass is None:
        msg = (
            f"NEXT_FRAMEWORK['REALTIME']['TICKET_BINDING'] {path!r} does not name "
            "a concrete TicketBinding subclass."
        )
        raise ImproperlyConfigured(msg)
    return klass()


__all__ = [
    "SUBJECT_SALT",
    "SessionBinding",
    "SignedCookieBinding",
    "binding_class",
    "opaque_subject",
    "ticket_binding",
]
