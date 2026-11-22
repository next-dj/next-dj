"""The checks of the boundary token seams, keys, caches and lifetimes."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from django.apps import apps
from django.conf import settings
from django.core.cache import DEFAULT_CACHE_ALIAS
from django.core.cache.backends.locmem import LocMemCache
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.core.exceptions import ImproperlyConfigured

from next.checks import NEXT
from next.checks.common import import_backend_class, raw_scope
from next.conf.defer import SCOPE, defer_option
from next.defer import MIN_KEY_LENGTH
from next.identity import SubjectBinding
from next.pages.checks.responses import shared_page_paths
from next.pages.loaders import load_python_module_memo
from next.pages.responses import cache_control
from next.suspense.tokens import CacheStorage, NoSeal, TokenCodec, TokenSeal
from next.suspense.tokens.binding import BINDINGS, NoBinding

from .calls import mints_tokens
from .settings import shipped, value_at, where


_SEAL: Final = ("TOKENS", "OPTIONS", "SEAL")
_CODEC: Final = ("TOKENS", "OPTIONS", "CODEC")
_BINDING: Final = ("TOKENS", "OPTIONS", "BINDING")
_ALIAS: Final = ("TOKENS", "OPTIONS", "CACHE_ALIAS")
_KEYS: Final = ("TOKENS", "OPTIONS", "KEYS")
_BUCKET: Final = ("TOKENS", "OPTIONS", "BUCKET")
_SESSIONS_APP: Final = "django.contrib.sessions"
_AUTH_APP: Final = "django.contrib.auth"
_CACHE_SESSIONS: Final = frozenset(
    {
        "django.contrib.sessions.backends.cache",
        "django.contrib.sessions.backends.cached_db",
    }
)


def _shipped_backend() -> bool:
    """Whether the merged settings name the shipped token backend."""
    return defer_option("TOKENS", "BACKEND") == shipped(("TOKENS", "BACKEND"))


def _token_option(name: str) -> object:
    """Return a merged token option of the shipped backend, `None` for another one."""
    if not _shipped_backend():
        return None
    return defer_option("TOKENS", "OPTIONS", name)


def _token_class(name: str) -> type | None:
    """Return the class a token option names, `None` when it names none."""
    dotted = _token_option(name)
    if not isinstance(dotted, str):
        return None
    if name == "BINDING" and dotted in BINDINGS:
        return BINDINGS[dotted]
    try:
        klass = import_backend_class(dotted)
    except ImportError:
        return None
    return klass if isinstance(klass, type) else None


def _names(name: str, root: type) -> bool:
    klass = _token_class(name)
    return klass is not None and issubclass(klass, root)


def _cache_storage() -> str | None:
    """Return the cache alias the token storage writes to, `None` for none."""
    alias = _token_option("CACHE_ALIAS")
    if not _names("STORAGE", CacheStorage) or not isinstance(alias, str):
        return None
    return alias


def _missing(path: tuple[str, ...], root: type, code: str, hint: str) -> CheckMessage:
    """Return the error for a seam whose library does not import in this process."""
    return Error(
        f"{where(path)} names {root.__name__}, whose library does not import, so no "
        "boundary token can be minted.",
        hint=hint,
        obj=settings,
        id=code,
    )


def _seal_errors() -> list[CheckMessage]:
    """Report a seal that seals nothing in production or misses its library."""
    errors: list[CheckMessage] = []
    klass = _token_class("SEAL")
    if klass is None or not issubclass(klass, TokenSeal):
        return errors
    if issubclass(klass, NoSeal) and not settings.DEBUG:
        errors.append(
            Error(
                f"{where(_SEAL)} names NoSeal while DEBUG is off, so anyone can forge "
                "a boundary token with any props.",
                hint="Use next.suspense.SignedSeal or next.suspense.EncryptedSeal.",
                obj=settings,
                id="next.E221",
            )
        )
    if not klass.available():
        hint = (
            "Install the package the seal needs, the next-dj[crypto] extra for "
            "EncryptedSeal, or use SignedSeal."
        )
        errors.append(_missing(_SEAL, klass, "next.E230", hint))
    return errors


def _codec_errors() -> list[CheckMessage]:
    """Report a codec that misses the library it writes with."""
    klass = _token_class("CODEC")
    if klass is None or not issubclass(klass, TokenCodec) or klass.available():
        return []
    hint = (
        "Install the package the codec writes with, msgpack for MsgpackCodec, or keep "
        "the default JsonCodec."
    )
    return [_missing(_CODEC, klass, "next.E237", hint)]


def _binding_errors() -> list[CheckMessage]:
    """Report a binding that reads the session in a project without the sessions app."""
    klass = _token_class("BINDING")
    if (
        klass is None
        or not issubclass(klass, SubjectBinding)
        or not klass.needs_sessions
        or apps.is_installed(_SESSIONS_APP)
    ):
        return []
    return [
        Error(
            f"{where(_BINDING)} names {klass.__name__}, which reads the session, but "
            f"{_SESSIONS_APP} is not installed, so no bound boundary token can be "
            "minted.",
            hint="Add django.contrib.sessions to INSTALLED_APPS or bind otherwise.",
            obj=settings,
            id="next.E226",
        )
    ]


def _store(alias: str) -> tuple[object, tuple[str, ...]] | None:
    """Return the backend class and the locations a cache alias stores in."""
    config = settings.CACHES.get(alias)
    if not isinstance(config, Mapping):
        return None
    backend = config.get("BACKEND")
    try:
        resolved = import_backend_class(backend) if isinstance(backend, str) else None
    except ImportError:
        resolved = None
    location: object = config.get("LOCATION", "")
    places: list[object]
    if isinstance(location, str):
        places = list(re.split("[;,]", location))
    elif isinstance(location, list | tuple):
        places = list(location)
    else:
        places = [location]
    return resolved or backend, tuple(sorted(str(place).strip() for place in places))


def _session_cache_errors() -> list[CheckMessage]:
    """Report a token cache that stores where the session engine stores sessions."""
    alias = _cache_storage()
    engine = settings.SESSION_ENGINE
    if alias is None or engine not in _CACHE_SESSIONS:
        return []
    sessions = settings.SESSION_CACHE_ALIAS
    store = _store(alias)
    if alias != sessions and (store is None or store != _store(sessions)):
        return []
    return [
        Error(
            f"{where(_ALIAS)} is {alias!r}, which stores in the cache the session "
            "engine keeps sessions in, so token entries and sessions evict and expose "
            "each other.",
            hint="Give boundary tokens a cache alias of their own.",
            obj=settings,
            id="next.E234",
        )
    ]


def _shared_cache_warnings() -> list[CheckMessage]:
    """Warn about token claims stored in the default cache nobody chose for them."""
    alias = _cache_storage()
    chosen = value_at(raw_scope(SCOPE) or {}, _ALIAS)
    if alias != DEFAULT_CACHE_ALIAS or chosen is not None:
        return []
    return [
        DjangoWarning(
            f"{where(_ALIAS)} is left at {alias!r} while the token storage keeps "
            "claims in a cache. Django unpickles a cache entry before any MAC is "
            "checked, so whoever can write that cache can run code in every process.",
            hint=(
                "Set CACHE_ALIAS to a cache whose writers are trusted exactly like "
                "those of the session cache."
            ),
            obj=settings,
            id="next.W180",
        )
    ]


def _shell_lifetimes() -> list[tuple[Path, int]]:
    """Return each page a shared cache may store, with its static lifetime."""
    lifetimes: list[tuple[Path, int]] = []
    for path in shared_page_paths():
        control = cache_control(getattr(load_python_module_memo(path), "cache", None))
        seconds = None if control is None else control.seconds
        if seconds is not None:
            lifetimes.append((path, seconds))
    return lifetimes


def _encrypts() -> bool:
    klass = _token_class("SEAL")
    return klass is not None and issubclass(klass, TokenSeal) and klass.encrypts


def _lifetime_warnings() -> list[CheckMessage]:
    """Warn about a token lifetime a cached shell or an encrypting seal defeats."""
    age, held = _token_option("MAX_AGE"), _token_option("BUCKET")
    bucket = held if isinstance(held, int) else 0
    warnings: list[CheckMessage] = []
    if bucket and _encrypts():
        warnings.append(
            DjangoWarning(
                f"{where(_BUCKET)} is set while the seal encrypts under a fresh key, "
                "so equal claims never give equal tokens.",
                hint="Drop BUCKET, or use SignedSeal for byte-stable shells.",
                obj=settings,
                id="next.W174",
            )
        )
    if not isinstance(age, int):
        return warnings
    short = [
        str(path) for path, seconds in _shell_lifetimes() if seconds + bucket > age
    ]
    if short:
        warnings.append(
            DjangoWarning(
                f"The boundary token MAX_AGE of {age} seconds is below the shared "
                f"cache lifetime plus BUCKET of {', '.join(short)}, so a cached shell "
                "hands out expired tokens.",
                hint="Raise MAX_AGE to the longest shell lifetime plus BUCKET.",
                obj=settings,
                id="next.W167",
            )
        )
    return warnings


@register(NEXT)
def check_defer_tokens(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report token seams that forge, fail or expire (`E221`, `E226`, `E230`, `E234`).

    It also reports a codec without its library (`E237`) and warns about the rest.
    """
    return [
        *_seal_errors(),
        *_codec_errors(),
        *_binding_errors(),
        *_session_cache_errors(),
        *_shared_cache_warnings(),
        *_lifetime_warnings(),
    ]


@register(NEXT)
def check_token_keys(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Refuse a project secret too short to seal boundary tokens with (`next.E231`).

    A declaration only a lazy backend loads at render meets the refusal at mint instead.
    """
    if not _shipped_backend() or _token_option("KEYS") is not None:
        return []
    try:
        keys = (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
    except ImproperlyConfigured:
        return []
    if all(isinstance(key, str) and len(key) >= MIN_KEY_LENGTH for key in keys):
        return []
    if not mints_tokens():
        return []
    return [
        Error(
            f"SECRET_KEY or an entry of SECRET_KEY_FALLBACKS is shorter than "
            f"{MIN_KEY_LENGTH} characters, and with {where(_KEYS)} unset boundary "
            "tokens are signed and verified with it, so anyone could forge one.",
            hint=(
                "Generate each secret with secrets.token_urlsafe(32), or name a ring "
                "of long keys in KEYS."
            ),
            obj=settings,
            id="next.E231",
        )
    ]


def _locmem(alias: str) -> bool:
    """Whether the cache under `alias` keeps its entries inside one process."""
    config = settings.CACHES.get(alias)
    backend = config.get("BACKEND") if isinstance(config, Mapping) else None
    if not isinstance(backend, str):
        return False
    try:
        klass = import_backend_class(backend)
    except ImportError:
        return False
    return isinstance(klass, type) and issubclass(klass, LocMemCache)


@register(NEXT, deploy=True)
def check_token_cache(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Refuse a process-local token cache for a deployment (`next.E223`)."""
    alias = _cache_storage()
    if alias is None or not _locmem(alias):
        return []
    return [
        Error(
            f"{where(_ALIAS)} {alias!r} is a locmem cache, which keeps token entries "
            "inside one process, so with several workers or hosts a redeem misses the "
            "entry another process wrote.",
            hint=(
                "Point CACHE_ALIAS at a shared cache such as Redis, or list next.E223 "
                "in SILENCED_SYSTEM_CHECKS for a single-process deployment."
            ),
            obj=settings,
            id="next.E223",
        )
    ]


@register(NEXT, deploy=True)
def check_token_binding(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn when a project with accounts mints tokens bound to nobody (`next.W186`)."""
    klass = _token_class("BINDING")
    if klass is not NoBinding or not apps.is_installed(_AUTH_APP) or not mints_tokens():
        return []
    return [
        DjangoWarning(
            f"{where(_BINDING)} is NoBinding in a project with "
            f"{_AUTH_APP} installed, so a boundary token minted for one signed-in "
            "user also opens the boundary for any client that obtains it until it "
            "expires.",
            hint=(
                'Set it to "user" or "session" when deferred content belongs to an '
                "account, or list next.W186 in SILENCED_SYSTEM_CHECKS when every "
                "client-trip boundary shows public content."
            ),
            obj=settings,
            id="next.W186",
        )
    ]


__all__ = [
    "check_defer_tokens",
    "check_token_binding",
    "check_token_cache",
    "check_token_keys",
]
