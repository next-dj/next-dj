"""The subject a request proves, shared by realtime tickets and boundary tokens."""

import functools
from abc import ABC, abstractmethod
from collections.abc import Mapping
from importlib import import_module
from typing import ClassVar, NamedTuple, cast, override

from django.conf import settings
from django.contrib import auth
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.db import SessionStore as DatabaseStore
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.crypto import salted_hmac


class LiveIdentity(NamedTuple):
    """The user a credential proves now and the seconds its session has left."""

    user: object
    seconds_left: int | None


class SubjectBinding(ABC):
    """Decide what a credential is bound to, and the subject a later request proves.

    `needs_sessions` marks a binding that reads the session, which needs its app.
    """

    needs_sessions: ClassVar[bool] = False

    @abstractmethod
    def issue(self, request: HttpRequest) -> str:
        """Return the subject a credential issued for `request` binds, or raise."""

    @abstractmethod
    def bound(self, request: HttpRequest) -> str | None:
        """Return the subject `request` proves now, None when it proves none."""


class TicketBinding(SubjectBinding):
    """Decide what a ticket is bound to, how a handshake proves it and stays live.

    A credential is what the handshake cookies carry, a bearer no handler ever sees.
    """

    needs_sessions = True

    @abstractmethod
    @override
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

    @override
    def bound(self, request: HttpRequest) -> str | None:
        """Return the subject the cookies of `request` prove, as a handshake does."""
        credential = self.credential(request.COOKIES)
        return None if credential is None else self.subject(credential)


def subject_digest(subject: str, salt: str) -> str:
    """Return a keyed digest of `subject` under `salt`, which never yields it back."""
    return salted_hmac(salt, subject, algorithm="sha256").hexdigest()


def request_subject(request: HttpRequest, binding: SubjectBinding, salt: str) -> str:
    """Return the digest of the subject `request` proves to `binding`, empty for none.

    It stays stable across requests and never yields the subject back.
    """
    subject = binding.bound(request)
    return "" if subject is None else subject_digest(subject, salt)


def account_subject(auth_hash: str, user_id: str) -> str:
    """Return the subject of a signed-in account, the same for every backend."""
    return f"user:{auth_hash}:{user_id}"


def account_parts(credential: str | None) -> tuple[str, str, str] | None:
    """Split an account credential into its auth hash, backend and user id."""
    match None if credential is None else credential.split(":", 3):
        case ["user", auth_hash, backend, user_id]:
            return auth_hash, backend, user_id
        case _:
            return None


def session_account(session: SessionBase) -> str | None:
    """Return the signed-in credential a session holds, without loading the user."""
    user_id = session.get(SESSION_KEY)
    if user_id is None:
        return None
    auth_hash = session.get(HASH_SESSION_KEY, "")
    backend = session.get(BACKEND_SESSION_KEY, "")
    return f"user:{auth_hash}:{backend}:{user_id}"


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
def read_only_store(engine: type[SessionBase]) -> type[SessionBase]:
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


def session_store_class() -> type[SessionBase]:
    """Return the `SessionStore` `SESSION_ENGINE` names, as `SessionMiddleware` does."""
    store: type[SessionBase] = import_module(settings.SESSION_ENGINE).SessionStore
    return store


def live_store(
    session_key: str, *, deadline: bool = True
) -> tuple[SessionBase, int | None] | None:
    """Return a stored session and its seconds left, or None once it is gone.

    A failed load drops the key, and a database load already skips an expired row.
    """
    store = read_only_store(session_store_class())(session_key)
    expiry = store.get("_session_expiry")
    if store.session_key != session_key:
        return None
    left = _seconds_left(store, expiry, deadline=deadline)
    if left is not None and left <= 0:
        return None
    return store, left


def session_alive(session_key: str) -> bool:
    """Whether a handshake would find the stored session live, without its user."""
    return live_store(session_key) is not None


def stored_session(session_key: str) -> LiveIdentity | None:
    """Return a stored session's user and seconds left, or None once it is gone."""
    live = live_store(session_key)
    if live is None:
        return None
    store, left = live
    return LiveIdentity(session_user(store), left)


__all__ = [
    "LiveIdentity",
    "SubjectBinding",
    "TicketBinding",
    "account_parts",
    "account_subject",
    "live_store",
    "read_only_store",
    "request_subject",
    "session_account",
    "session_alive",
    "session_store_class",
    "session_user",
    "stored_session",
    "subject_digest",
]
