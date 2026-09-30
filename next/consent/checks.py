"""System checks for `NEXT_FRAMEWORK["CONSENT"]`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys
from next.conf import import_class_cached
from next.conf.defaults import AUTO, USER_SETTING

from .backends import ConsentBackend
from .manager import CONSENT_KEYS


_PREFIX: Final = "NEXT_FRAMEWORK['CONSENT']"


def _raw_scope() -> Mapping[str, object] | None:
    """Return the raw `CONSENT` scope, or `None` where `next.E076` reports it."""
    raw = getattr(settings, USER_SETTING, None)
    if not isinstance(raw, dict):
        return None
    scope = raw.get("CONSENT")
    return scope if isinstance(scope, dict) else None


def _backend_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    path = scope.get("BACKEND")
    if path is None:
        return []
    problem: str | None = None
    if not isinstance(path, str) or not path:
        problem = "is no dotted path"
    else:
        try:
            backend = import_class_cached(path)
        except ImportError as exc:
            problem = f"does not import: {exc}"
        else:
            if not (isinstance(backend, type) and issubclass(backend, ConsentBackend)):
                problem = "is no next.consent.ConsentBackend subclass"
    if problem is None:
        return []
    return [
        Error(
            f"{_PREFIX}['BACKEND'] {path!r} {problem}, so no page can read consent.",
            obj=settings,
            id="next.E137",
        )
    ]


def _server_render_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    value = scope.get("SERVER_RENDER", AUTO)
    if isinstance(value, bool) or value == AUTO:
        return []
    return [
        Error(
            f"{_PREFIX}['SERVER_RENDER'] is {value!r}. Write 'auto', True or False.",
            obj=settings,
            id="next.E145",
        )
    ]


@register(NEXT)
def check_consent_settings(*args, **kwargs) -> list[CheckMessage]:
    """Validate the keys (E035), the render mode (E145) and the backend (E137)."""
    scope = _raw_scope()
    if scope is None:
        return []
    return [
        *errors_for_unknown_keys(dict(scope), allowed=CONSENT_KEYS, prefix=_PREFIX),
        *_server_render_errors(scope),
        *_backend_errors(scope),
    ]


@register(NEXT, deploy=True)
def check_consent_cookie_secure(*args, **kwargs) -> list[CheckMessage]:
    """Warn when the session cookie is Secure and the consent cookie is not (W129)."""
    if not getattr(settings, "SESSION_COOKIE_SECURE", False):
        return []
    scope = _raw_scope() or {}
    options = scope.get("OPTIONS")
    if not isinstance(options, Mapping) or options.get("secure") is not False:
        return []
    return [
        DjangoWarning(
            f"{_PREFIX}['OPTIONS']['secure'] is False while SESSION_COOKIE_SECURE is "
            "True, so the consent cookie also travels over plain HTTP. Leave it None "
            "to follow the scheme, or set it True.",
            obj=settings,
            id="next.W129",
        )
    ]


__all__ = ["check_consent_cookie_secure", "check_consent_settings"]
