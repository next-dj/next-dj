"""What else must hold when a boundary token is redeemed, on the shared binding ABC."""

import inspect
from collections.abc import Callable
from typing import Final, override

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.defer.errors import BoundaryBindingError
from next.identity import SubjectBinding, account_subject


type Subject = Callable[[HttpRequest], object]

_ANONYMOUS: Final = "anonymous"
_NONE: Final = "none"
_VALUE: Final = "value:"
_SESSION: Final = "session:"


class NoBinding(SubjectBinding):
    """Bind nothing, so one token serves every visitor and a shell stays cacheable."""

    binds = False
    shared = frozenset({""})

    @override
    def issue(self, request: HttpRequest) -> str:
        return ""

    @override
    def bound(self, request: HttpRequest) -> str | None:
        return ""


def _session_key(request: HttpRequest) -> str | None:
    session = getattr(request, "session", None)
    key = None if session is None else session.session_key
    return key if isinstance(key, str) and key else None


class SessionBinding(SubjectBinding):
    """Bind the Django session key, so the token fails in any other session."""

    needs_sessions = True

    @override
    def issue(self, request: HttpRequest) -> str:
        """Bind the session, refusing a visitor who has none yet.

        After the first byte no cookie can be set, so the caller drops retry instead.
        """
        key = _session_key(request)
        if key is None:
            msg = (
                "the visitor has no session, so a session-bound token cannot be minted"
            )
            raise BoundaryBindingError(msg)
        return f"{_SESSION}{key}"

    @override
    def bound(self, request: HttpRequest) -> str | None:
        key = _session_key(request)
        return None if key is None else f"{_SESSION}{key}"


def _user_subject(request: HttpRequest) -> str | None:
    """Return the account subject of the user, its auth hash read from the user now."""
    user = getattr(request, "user", None)
    if user is None:
        return None
    if not getattr(user, "is_authenticated", False):
        return _ANONYMOUS
    auth_hash = getattr(user, "get_session_auth_hash", None)
    return account_subject(auth_hash() if callable(auth_hash) else "", str(user.pk))


class UserBinding(SubjectBinding):
    """Bind the user by pk and auth hash, so a password change or a logout revokes.

    Every anonymous visitor proves one subject, so their render stays shareable.
    """

    shared = frozenset({_ANONYMOUS})

    @override
    def issue(self, request: HttpRequest) -> str:
        subject = _user_subject(request)
        if subject is None:
            msg = "the request carries no user, so a user-bound token cannot be minted"
            raise BoundaryBindingError(msg)
        return subject

    @override
    def bound(self, request: HttpRequest) -> str | None:
        return _user_subject(request)


class AutoBinding(SubjectBinding):
    """Bind the user `AuthenticationMiddleware` set on the request, nothing without one.

    It is the default, so a project with accounts never mints a token anyone can use.
    """

    shared = frozenset({_ANONYMOUS, ""})

    @override
    def issue(self, request: HttpRequest) -> str:
        return self.bound(request) or ""

    @override
    def bound(self, request: HttpRequest) -> str:
        subject = _user_subject(request)
        return "" if subject is None else subject


class CallableBinding(SubjectBinding):
    """Bind what a callable returns for the request, `None` bound as a literal.

    The callable may answer anything, and an answer other than text or `None` raises.
    """

    def __init__(self, subject: Subject) -> None:
        """Keep the callable that names the subject of a request."""
        self.subject = subject

    def _subject(self, request: HttpRequest) -> str:
        value = self.subject(request)
        if value is None:
            return _NONE
        if not isinstance(value, str):
            msg = (
                f"the binding callable {self.subject!r} returned a "
                f"{type(value).__name__}, not a str or None"
            )
            raise TypeError(msg)
        return f"{_VALUE}{value}"

    @override
    def issue(self, request: HttpRequest) -> str:
        return self._subject(request)

    @override
    def bound(self, request: HttpRequest) -> str | None:
        return self._subject(request)


BINDINGS: Final[dict[str, type[SubjectBinding]]] = {
    "auto": AutoBinding,
    "none": NoBinding,
    "session": SessionBinding,
    "user": UserBinding,
}
"""The short names a per-component `tokens={"binding": ...}` may use."""


def binding_problem(target: object) -> str | None:
    """Return why an imported `BINDING` target builds no binding, `None` when it does.

    A class is built bare, so one that takes arguments, as `CallableBinding`, fails.
    """
    if not isinstance(target, type):
        return None if callable(target) else "names no callable"
    if not issubclass(target, SubjectBinding):
        return "is no subclass of SubjectBinding and no callable"
    if inspect.isabstract(target):
        return "is an abstract class"
    try:
        inspect.signature(target).bind()
    except TypeError:
        return "takes arguments, so no setting can build it"
    return None


def build_binding(value: str) -> SubjectBinding:
    """Build the binding a short name or a dotted path names, a callable wrapped.

    A dotted path names a `SubjectBinding` subclass built bare or a request callable.
    """
    if value in BINDINGS:
        return BINDINGS[value]()
    try:
        target = import_string(value)
    except ImportError as exc:
        msg = f"the token binding {value!r} does not import"
        raise ImproperlyConfigured(msg) from exc
    problem = binding_problem(target)
    if problem is not None:
        msg = f"the token binding {value!r} {problem}"
        raise ImproperlyConfigured(msg)
    if isinstance(target, type) and issubclass(target, SubjectBinding):
        return target()
    return CallableBinding(target)


__all__ = ["BINDINGS", "binding_problem", "build_binding"]
