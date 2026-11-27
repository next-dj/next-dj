import time
from collections.abc import Callable

from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest
from django.http.response import HttpResponseBase

from next.defer.render import current_frame


DOWN = "down"
NONCE_VALUE = "Nonce123"

NOTES: list[tuple[str, str]] = []
RUNS: list[tuple[str, int]] = []


def pause() -> None:
    """Let a job through at once, unless a test slows it down or makes jobs meet."""


def note(name: str) -> None:
    """Record that the callable `name` ran, in a job or in the shell."""
    NOTES.append((name, "job" if current_frame() is not None else "shell"))


def stall() -> None:
    """Hold a gated callable past the page timeout of a test."""
    time.sleep(0.5)


class Boom:
    """Fail every request after the view returned its response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        """Keep the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        self.get_response(request)
        raise RuntimeError(DOWN)


class OwnNonce:
    """Set the nonce the way a project's own CSP middleware does."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        """Keep the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        request.csp_nonce = NONCE_VALUE
        return self.get_response(request)


class ObjectBackend(ModelBackend):
    """Grant a permission on any object to a user who holds it on the model."""

    def has_perm(self, user_obj, perm, obj=None) -> bool:
        return super().has_perm(user_obj, perm)


class OwnerBackend(ModelBackend):
    """Grant a permission on a row only to the user its `last_name` names."""

    def has_perm(self, user_obj, perm, obj=None) -> bool:
        if obj is None:
            return super().has_perm(user_obj, perm)
        return user_obj.is_active and obj.last_name == user_obj.username
