"""The configured consent backend and the consent each request carries."""

import functools
from typing import Final

from django.conf import settings
from django.http import HttpRequest

from next.backends import SingleBackendManager
from next.conf.defaults import DEFAULTS, USER_SETTING
from next.conf.scopes import scope_value
from next.conf.signals import settings_reloaded
from next.pages.responses import shared_render

from .backends import ConsentBackend, CookieConsentBackend
from .markers import NECESSARY, UNDECIDED, Consent
from .signals import consent_backend_loaded


CONSENT_ATTR: Final = "_next_consent"
"""The request attribute the consent of one request is held under."""

CONSENT_KEYS: Final = frozenset(DEFAULTS["CONSENT"])
"""The keys a `NEXT_FRAMEWORK["CONSENT"]` mapping may carry."""

consent_backend_manager: SingleBackendManager[ConsentBackend] = SingleBackendManager(
    "CONSENT",
    base=ConsentBackend,
    default=str(DEFAULTS["CONSENT"]["BACKEND"]),
    signal=consent_backend_loaded,
)


@functools.cache
def consent_configured() -> bool:
    """Whether the project sets `CONSENT`, so every page tells the runtime of it."""
    raw = getattr(settings, USER_SETTING, None)
    return isinstance(raw, dict) and "CONSENT" in raw


@functools.cache
def consent_categories() -> tuple[str, ...]:
    """Return the configured categories, `necessary` first whether listed or not."""
    raw = scope_value("CONSENT", "CATEGORIES")
    names = (
        [name for name in raw if isinstance(name, str) and name]
        if isinstance(raw, list | tuple)
        else []
    )
    return tuple(dict.fromkeys((NECESSARY, *names)))


@functools.cache
def server_render() -> bool | None:
    """Return the `SERVER_RENDER` mode, `None` for `"auto"` and anything unusable."""
    value = scope_value("CONSENT", "SERVER_RENDER")
    return value if isinstance(value, bool) else None


def forget_consent_settings(**kwargs) -> None:
    """Drop the memoised backend and options, so a settings reload takes effect."""
    consent_backend_manager.reset()
    consent_configured.cache_clear()
    consent_categories.cache_clear()
    server_render.cache_clear()


settings_reloaded.connect(forget_consent_settings)


def get_consent(request: HttpRequest | None) -> Consent:
    """Return the consent `request` carries, read once and kept to the categories.

    Anything but a real request is an undecided visitor.
    """
    if not isinstance(request, HttpRequest):
        return UNDECIDED
    held = getattr(request, CONSENT_ATTR, None)
    if isinstance(held, Consent):
        return held
    read = consent_backend_manager.get().read(request)
    categories = consent_categories()
    consent = Consent(
        frozenset(name for name in read.granted if name in categories) | {NECESSARY},
        decided=read.decided,
    )
    setattr(request, CONSENT_ATTR, consent)
    return consent


def server_mode(request: HttpRequest | None) -> bool:
    """Whether this render follows the consent cookie rather than the runtime.

    `"auto"` keeps a page a shared cache holds independent of the cookie.
    """
    mode = server_render()
    if mode is not None:
        return mode
    return not shared_render(request)


def consent_payload(consent: Consent) -> dict[str, object]:
    """Return the `$consent` entry for the runtime, the cookie it writes included."""
    categories = consent_categories()
    payload: dict[str, object] = {
        "categories": list(categories),
        "decided": consent.decided,
        "granted": [name for name in categories if consent.allows(name)],
    }
    backend = consent_backend_manager.get()
    if isinstance(backend, CookieConsentBackend):
        payload["cookie"] = backend.cookie()
    return payload


__all__ = [
    "CONSENT_ATTR",
    "CONSENT_KEYS",
    "consent_backend_manager",
    "consent_categories",
    "consent_configured",
    "consent_payload",
    "forget_consent_settings",
    "get_consent",
    "server_mode",
    "server_render",
]
