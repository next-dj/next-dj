"""The configured consent backend and the consent each request carries."""

import functools
import logging
from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.http import HttpRequest

from next.backends import SingleBackendManager
from next.conf.defaults import DEFAULTS, USER_SETTING
from next.conf.scopes import scope_value
from next.conf.settings import fail_loudly
from next.diagnostics import FailureLog, isolated_build, mark_degraded
from next.pages.responses import shared_render

from .backends import ConsentBackend
from .markers import NECESSARY, UNDECIDED, Consent
from .signals import consent_backend_loaded


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)

_BACKEND: Final = "NEXT_FRAMEWORK['CONSENT']['BACKEND']"
_LOAD_KEY: Final = ("load",)

_FALLBACK: Final = (
    "every visitor reads as undecided and every category but necessary stays denied "
    "until it is fixed"
)

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
    """Whether the project sets `CONSENT`, so every page sends `$consent`."""
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
    _built_backend.cache_clear()
    _built_config.cache_clear()
    consent_configured.cache_clear()
    consent_categories.cache_clear()
    server_render.cache_clear()


def get_consent(request: HttpRequest | None) -> Consent:
    """Return the consent `request` carries, read once and limited to the categories.

    Anything other than an `HttpRequest` reads as an undecided visitor.
    """
    if not isinstance(request, HttpRequest):
        return UNDECIDED
    held = getattr(request, CONSENT_ATTR, None)
    if isinstance(held, Consent):
        return held
    backend = _backend()
    read = UNDECIDED if backend is None else _read(backend, request)
    categories = consent_categories()
    consent = Consent(
        frozenset(name for name in read.granted if name in categories) | {NECESSARY},
        decided=read.decided,
    )
    setattr(request, CONSENT_ATTR, consent)
    return consent


def _backend() -> ConsentBackend | None:
    """Return the configured backend, `None` where it cannot be built.

    A failure is memoised as well, so a broken entry is not imported again on every
    render. Every render that reads the memoised failure is degraded and counts as a
    recurrence, logged at the `FailureLog` rate. `forget_consent_settings` drops the
    answer on a settings reload.
    """
    backend, failed = _built_backend()
    if failed:
        mark_degraded()
        _failures.warn(_LOAD_KEY, "%s still fails to load, so %s.", _BACKEND, _FALLBACK)
    return backend


@functools.cache
def _built_backend() -> tuple[ConsentBackend | None, bool]:
    """Build the configured backend once, with whether the build contained a failure."""
    return isolated_build(_build_backend)


def _build_backend() -> ConsentBackend | None:
    """Return the configured backend, `None` when it cannot be built."""
    try:
        return consent_backend_manager.get()
    except Exception as exc:  # noqa: BLE001 - a backend may raise anything it likes
        _failures.contain(
            exc,
            _LOAD_KEY,
            "%s failed to load, so %s. Name a next.consent.ConsentBackend subclass "
            "that imports and builds from its CONSENT entry.",
            _BACKEND,
            _FALLBACK,
        )
        return None


def _read(backend: ConsentBackend, request: HttpRequest) -> Consent:
    """Return what `backend` reads from `request`, undecided when the read fails."""
    source = type(backend).__qualname__
    try:
        read: object = backend.read(request)
    except Exception as exc:  # noqa: BLE001 - a backend may raise anything it likes
        _failures.contain(
            exc,
            ("read", source),
            "%s.read() of %s raised, so %s.",
            source,
            _BACKEND,
            _FALLBACK,
        )
        return UNDECIDED
    if isinstance(read, Consent):
        return read
    message = (
        f"{source}.read() of {_BACKEND} answered {type(read).__name__!r}, not a "
        f"next.consent.Consent, so {_FALLBACK}. Return a Consent, UNDECIDED for a "
        "visitor who has not chosen."
    )
    if fail_loudly():
        raise TypeError(message)
    _failures.warn(("type", source), message)
    return UNDECIDED


def _backend_config() -> Mapping[str, object]:
    """Return what the configured backend adds to `$consent`, read once per backend.

    Every render that reads a memoised failure is degraded and counts as a recurrence,
    logged at the `FailureLog` rate.
    """
    backend = _backend()
    if backend is None:
        return {}
    config, failed = _built_config(backend)
    if failed:
        source = type(backend).__qualname__
        mark_degraded()
        _failures.warn(
            ("client_config", source),
            "%s.client_config() of %s still raises, so the runtime writes its "
            "consent cookie under the default name and age.",
            source,
            _BACKEND,
        )
    return config


@functools.cache
def _built_config(backend: ConsentBackend) -> tuple[Mapping[str, object], bool]:
    """Read `backend.client_config()` once, with whether a failure was contained."""
    return isolated_build(functools.partial(_client_config, backend))


def _client_config(backend: ConsentBackend) -> Mapping[str, object]:
    """Return what `backend` adds to `$consent`, nothing when the call fails."""
    source = type(backend).__qualname__
    try:
        config: object = backend.client_config()
    except Exception as exc:  # noqa: BLE001 - a backend may raise anything it likes
        _failures.contain(
            exc,
            ("client_config", source),
            "%s.client_config() of %s raised, so the runtime writes its consent "
            "cookie under the default name and age.",
            source,
            _BACKEND,
        )
        return {}
    if isinstance(config, Mapping):
        return config
    _failures.warn(
        ("client_config", source),
        "%s.client_config() of %s answered %r, not a mapping, so $consent carries "
        "none of its entries.",
        source,
        _BACKEND,
        type(config).__name__,
    )
    return {}


def server_mode(request: HttpRequest | None) -> bool:
    """Whether the server applies the consent cookie to this render, not the runtime.

    Under `"auto"` a render a shared cache may store does not read the cookie.
    """
    mode = server_render()
    if mode is not None:
        return mode
    return not shared_render(request)


def consent_payload(consent: Consent) -> dict[str, object]:
    """Return the `$consent` entry for the runtime, the backend's own entries included.

    The choice overrides a backend entry of the same name, so the runtime always
    starts from the state the server read.
    """
    categories = consent_categories()
    payload: dict[str, object] = dict(_backend_config())
    payload.update(
        categories=list(categories),
        decided=consent.decided,
        granted=[name for name in categories if consent.allows(name)],
    )
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
