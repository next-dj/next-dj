"""Swappable binding of a realtime ticket to the browser that negotiated it."""

import functools
import inspect
import secrets
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import NamedTuple, cast, override

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.db import SessionStore as DatabaseStore
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.crypto import salted_hmac
from django.utils.module_loading import import_string

from next.conf.scopes import scope_value

from .errors import InvalidTicketError


class LiveIdentity(NamedTuple):
    """The user a credential proves now and the seconds its session has left."""

    user: object
    seconds_left: int | None


class TicketBinding(ABC):
    """Decide what a ticket is bound to, how a handshake proves it and stays live.

    A credential is what the handshake cookies carry, a bearer no handler ever sees.
    """

    @abstractmethod
    def issue(self, request: HttpRequest) -> str:
        """Return the subject a ticket negotiated by `request` binds, or raise.

        `InvalidTicketError` answers the negotiation with `503 session_unavailable`.
        """

    def respond(self, request: HttpRequest, response: HttpResponseBase) -> None:
        """Set the cookies a negotiation reply carries, nothing by default.

        A custom view calls it on its reply, `issue` leaves the cookie on the request.
        """
        del request, response

    @abstractmethod
    def credential(self, cookies: Mapping[str, str]) -> str | None:
        """Return the credential the handshake cookies carry, None without one.

        It runs off the event loop, so it may read the session store.
        """

    @abstractmethod
    def subject(self, credential: str) -> str | None:
        """Return the subject a credential proves, None when it proves none.

        It runs on the event loop of a socket, so it must not block.
        """

    @abstractmethod
    def live(self, credential: str) -> LiveIdentity | None:
        """Return the credential's user and seconds left, or None once it is gone."""

    def live_session(self, session_key: str) -> LiveIdentity | None:
        """Return the user of an SSE request's session key, or None once it is gone."""
        return stored_session(session_key)


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
            live = _live_store(session_key, deadline=False)
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
        parts = _account_parts(credential)
        return None if parts is None else _account_subject(parts[0], parts[2])

    @override
    def live(self, credential: str) -> LiveIdentity | None:
        """Reload an account by pk and auth hash, a visitor stays anonymous."""
        if credential.startswith(_VISITOR):
            return LiveIdentity(session_user(SessionBase()), None)
        parts = _account_parts(credential)
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
        account = None if session is None else _account(session)
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
_SUBJECT_SALT = "next.realtime.subject.v1"


def _account_subject(auth_hash: str, user_id: str) -> str:
    """Return the subject of a signed-in account, the same for every backend."""
    return f"user:{auth_hash}:{user_id}"


def _account_parts(credential: str | None) -> tuple[str, str, str] | None:
    """Split an account credential into its auth hash, backend and user id."""
    match None if credential is None else credential.split(":", 3):
        case ["user", auth_hash, backend, user_id]:
            return auth_hash, backend, user_id
        case _:
            return None


def _account(session: SessionBase) -> str | None:
    """Return the signed-in credential a session holds, without loading the user."""
    user_id = session.get(SESSION_KEY)
    if user_id is None:
        return None
    auth_hash = session.get(HASH_SESSION_KEY, "")
    backend = session.get(BACKEND_SESSION_KEY, "")
    return f"user:{auth_hash}:{backend}:{user_id}"


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
    if subject is None:
        return ""
    return salted_hmac(_SUBJECT_SALT, subject, algorithm="sha256").hexdigest()


def request_subject(request: HttpRequest) -> str:
    """Return the opaque subject an SSE request's cookies prove, empty for none.

    It equals the `Handshake.subject` a WebSocket of the same browser derives.
    """
    credential = ticket_binding().credential(request.COOKIES)
    return "" if credential is None else opaque_subject(credential)


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


class _SessionSnapshot(SessionBase):
    """An in-memory copy of a stored session that ignores key rotation and flush."""

    def __init__(self, store: SessionBase) -> None:
        """Copy the stored data under the same key, loading it once."""
        super().__init__(store.session_key)
        self._session_cache = dict(store.items())

    @override
    def cycle_key(self) -> None:
        return

    @override
    def flush(self) -> None:
        return


def session_user(store: SessionBase) -> object:
    """Load a session's user the way Django's `get_user` does, without writing to it.

    A socket has no response to carry a rotated cookie, so rotation stays with HTTP.
    """
    request = HttpRequest()
    request.session = _SessionSnapshot(store)
    return auth.get_user(request)


class _ReadOnlyStore(SessionBase):
    """Turn the writes a session engine makes while it loads into no-ops.

    The file engine replaces an expired session on load with a new one under a new key.
    """

    @override
    def create(self) -> None:
        self._session_key = None

    @override
    def save(self, must_create: bool = False) -> None:
        return

    @override
    def delete(self, session_key: str | None = None) -> None:
        return


@functools.cache
def _read_only(engine: type[SessionBase]) -> type[SessionBase]:
    """Return the engine's store class with every write turned into a no-op.

    It keeps the engine's qualified name, the salt Django signs the session data with.
    """
    bases = (_ReadOnlyStore, engine)
    namespace = {"__qualname__": engine.__qualname__}
    return cast("type[SessionBase]", type(engine.__name__, bases, namespace))


def _seconds_left(store: SessionBase, expiry: object, *, deadline: bool) -> int | None:
    """Return the seconds a loaded session has left, or None when the store cannot tell.

    A database row keeps the deadline of every session, a cache keeps it out of reach.
    """
    if deadline and isinstance(store, DatabaseStore):
        rows = store.get_model_class().objects.filter(session_key=store.session_key)
        stored = rows.values_list("expire_date", flat=True).first()
        return 0 if stored is None else store.get_expiry_age(expiry=stored)
    if isinstance(expiry, str):
        return store.get_expiry_age(expiry=expiry)
    return None


def _live_store(
    session_key: str, *, deadline: bool = True
) -> tuple[SessionBase, int | None] | None:
    """Return a stored session and its seconds left, or None once it is gone.

    A failed load drops the key, and a database load already skips an expired row.
    """
    engine = import_string(f"{settings.SESSION_ENGINE}.SessionStore")
    store = _read_only(engine)(session_key)
    expiry = store.get("_session_expiry")
    if store.session_key != session_key:
        return None
    left = _seconds_left(store, expiry, deadline=deadline)
    if left is not None and left <= 0:
        return None
    return store, left


def session_alive(session_key: str) -> bool:
    """Whether a handshake would find the stored session live, without its user."""
    return _live_store(session_key) is not None


def stored_session(session_key: str) -> LiveIdentity | None:
    """Return a stored session's user and seconds left, or None once it is gone."""
    live = _live_store(session_key)
    if live is None:
        return None
    store, left = live
    return LiveIdentity(session_user(store), left)


__all__ = [
    "LiveIdentity",
    "SessionBinding",
    "SignedCookieBinding",
    "TicketBinding",
    "binding_class",
    "opaque_subject",
    "request_subject",
    "session_alive",
    "session_user",
    "stored_session",
    "ticket_binding",
]
