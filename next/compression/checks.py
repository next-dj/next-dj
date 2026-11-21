"""System checks for the `COMPRESSION` scope, its codecs and the middleware order."""

import inspect
from collections.abc import Mapping, Sequence
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.conf.checks import (
    error_for_unusable_path,
    error_for_wrong_shape,
    errors_for_unknown_keys,
)
from next.conf.compression import SCOPE, SHAPES, compression_settings, misfits
from next.conf.defaults import USER_SETTING

from .backends import CompressionBackend
from .manager import GZIP_MIDDLEWARE, MIDDLEWARE, in_range


BODY_READERS: Final = (
    "django.middleware.common.CommonMiddleware",
    "django.middleware.http.ConditionalGetMiddleware",
    "django.middleware.cache.FetchFromCacheMiddleware",
)
"""The middleware `CompressionMiddleware` must sit above, a subclass counting too."""

_PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"
_SAMESITE: Final = ("SESSION_COOKIE_SAMESITE", "CSRF_COOKIE_SAMESITE")
_CROSS_SITE: Final = "none"
_RESET: Final = "reset"


def _raw_scope() -> Mapping[str, object] | None:
    """Return the `COMPRESSION` mapping as written, `None` when it is no dict."""
    raw = getattr(settings, USER_SETTING, None)
    scope = raw.get(SCOPE) if isinstance(raw, dict) else None
    return scope if isinstance(scope, dict) else None


def _written(scope: Mapping[str, object] | None, key: str) -> object:
    """Return the usable value the project writes for `key`, else `None`."""
    if scope is None or key not in scope or not SHAPES[key][0](scope[key]):
        return None
    return scope[key]


def _imported(dotted: str) -> object:
    """Return what `dotted` imports, `None` when it does not import."""
    try:
        return import_string(dotted)
    except ImportError:
        return None


def _backend_problem(dotted: str) -> str | None:
    """Return why `dotted` names no concrete backend class, `None` when it does."""
    try:
        klass = import_string(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    if not (isinstance(klass, type) and issubclass(klass, CompressionBackend)):
        return "is no subclass of CompressionBackend"
    if inspect.isabstract(klass):
        return "is an abstract class"
    return None


def _backends() -> dict[str, CompressionBackend]:
    """Return a fresh instance of each usable `BACKENDS` entry by coding, first wins."""
    found: dict[str, CompressionBackend] = {}
    for dotted in compression_settings().backends:
        if _backend_problem(dotted) is None:
            backend: CompressionBackend = import_string(dotted)()
            found.setdefault(backend.coding, backend)
    return found


@register(NEXT)
def check_compression_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and the value shapes of the `COMPRESSION` scope."""
    scope = _raw_scope()
    if scope is None:
        return []
    errors = errors_for_unknown_keys(
        dict(scope), allowed=frozenset(SHAPES), prefix=_PREFIX
    )
    errors.extend(
        error_for_wrong_shape(
            f"{_PREFIX}[{misfit.key!r}]", misfit.expected, misfit.found
        )
        for misfit in misfits(scope)
    )
    return errors


def _path_error(key: str, dotted: str, problem: str) -> CheckMessage:
    """Build the `next.E219` error for one unusable `COMPRESSION` path."""
    return error_for_unusable_path(
        f"{_PREFIX}[{key!r}]",
        dotted,
        problem,
        "the framework skips it",
        "Name a concrete CompressionBackend subclass for BACKENDS, and a callable "
        "taking the request and the response for EXCLUDE.",
    )


@register(NEXT)
def check_compression_paths(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `BACKENDS` or `EXCLUDE` path that names nothing usable (`next.E219`)."""
    current = compression_settings()
    errors = [
        _path_error("BACKENDS", dotted, problem)
        for dotted in current.backends
        if (problem := _backend_problem(dotted)) is not None
    ]
    exclude = current.exclude
    if exclude is not None:
        target = _imported(exclude)
        if target is None:
            errors.append(_path_error("EXCLUDE", exclude, "does not import"))
        elif not callable(target):
            errors.append(_path_error("EXCLUDE", exclude, "names no callable"))
    return errors


def _codec_error(message: str, hint: str) -> CheckMessage:
    """Build one `next.E228` error."""
    return Error(message, hint=hint, obj=settings, id="next.E228")


def _unknown_codings(
    scope: Mapping[str, object] | None, backends: Mapping[str, CompressionBackend]
) -> list[CheckMessage]:
    """Report each coding `ORDER`, `LEVELS` or `WINDOWS` names and no backend serves."""
    errors: list[CheckMessage] = []
    known = ", ".join(sorted(backends)) or "none"
    for key in ("ORDER", "LEVELS", "WINDOWS"):
        written = _written(scope, key)
        codings = list(written) if isinstance(written, Sequence | Mapping) else []
        errors.extend(
            _codec_error(
                f"{_PREFIX}[{key!r}] names the coding {coding!r}, which no BACKENDS "
                "entry serves.",
                f"Use a coding a backend serves ({known}) or add its backend.",
            )
            for coding in dict.fromkeys(codings)
            if coding not in backends
        )
    return errors


def _range_errors(
    scope: Mapping[str, object] | None, backends: Mapping[str, CompressionBackend]
) -> list[CheckMessage]:
    """Report each written level or window outside the range of its codec."""
    errors: list[CheckMessage] = []
    levels = _written(scope, "LEVELS")
    for coding, level in (levels if isinstance(levels, Mapping) else {}).items():
        backend = backends.get(coding)
        if backend is not None and not in_range(level, backend.levels):
            low, high = backend.levels
            errors.append(
                _codec_error(
                    f"{_PREFIX}['LEVELS'][{coding!r}] is {level}, outside {low} to "
                    f"{high}, so the codec keeps its default level {backend.level}.",
                    f"Write a level from {low} to {high}.",
                )
            )
    windows = _written(scope, "WINDOWS")
    for coding, window in (windows if isinstance(windows, Mapping) else {}).items():
        backend = backends.get(coding)
        if backend is not None and not in_range(window, backend.windows):
            span = backend.windows
            allowed = "no window" if span is None else f"{span[0]} to {span[1]}"
            errors.append(
                _codec_error(
                    f"{_PREFIX}['WINDOWS'][{coding!r}] is {window}, while the codec "
                    f"takes {allowed}, so it keeps its default window.",
                    "Drop the entry or write a window the codec takes.",
                )
            )
    return errors


def _availability_errors(
    scope: Mapping[str, object] | None, backends: Mapping[str, CompressionBackend]
) -> list[CheckMessage]:
    """Report a codec `ORDER` forces whose library is missing, and a dead reset mode.

    The default `ORDER` forces only the codecs of the core dependencies.
    """
    current = compression_settings()
    if not current.enabled:
        return []
    written = _written(scope, "ORDER") is not None
    ordered = [backends[c] for c in dict.fromkeys(current.order) if c in backends]
    errors = [
        _codec_error(
            f"{_PREFIX}['ORDER'] offers {backend.coding!r}, whose library does not "
            "import, so no response is encoded with it.",
            "Install the library of the codec, the 'brotli' extra for 'br', or drop "
            "the coding from ORDER.",
        )
        for backend in ordered
        if not backend.available() and (written or not backend.optional)
    ]
    if current.sse_topics == _RESET and not any(
        backend.resets and backend.available() for backend in ordered
    ):
        errors.append(
            _codec_error(
                f"{_PREFIX}['SSE_TOPICS'] is 'reset', while no available codec of "
                "ORDER can drop its window, so topic streams stay uncompressed.",
                "Offer zstd or gzip in ORDER, or set SSE_TOPICS to 'off'.",
            )
        )
    return errors


@register(NEXT)
def check_compression_codecs(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report unknown codings, levels and windows out of range, and missing codecs."""
    scope = _raw_scope()
    backends = _backends()
    return [
        *_unknown_codings(scope, backends),
        *_range_errors(scope, backends),
        *_availability_errors(scope, backends),
    ]


def _position(middleware: Sequence[object], base: str) -> int | None:
    """Return where `middleware` lists `base` or a subclass, `None` for nowhere."""
    parent = _imported(base)
    for index, entry in enumerate(middleware):
        if entry == base:
            return index
        if isinstance(entry, str) and isinstance(parent, type):
            found = _imported(entry)
            if isinstance(found, type) and issubclass(found, parent):
                return index
    return None


@register(NEXT)
def check_compression_middleware(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a doubled compressor, a missing middleware and one listed too low."""
    middleware = list(getattr(settings, "MIDDLEWARE", None) or ())
    position = _position(middleware, MIDDLEWARE)
    messages: list[CheckMessage] = []
    if position is not None and _position(middleware, GZIP_MIDDLEWARE) is not None:
        messages.append(
            Error(
                f"settings.MIDDLEWARE lists both {MIDDLEWARE} and {GZIP_MIDDLEWARE}, "
                "so whichever sits lower encodes every response and the other one "
                "never runs.",
                hint=f"Remove {GZIP_MIDDLEWARE}.",
                obj=settings,
                id="next.E227",
            )
        )
    if position is None:
        if compression_settings().enabled:
            messages.append(
                DjangoWarning(
                    f"{_PREFIX}['ENABLED'] is on, while settings.MIDDLEWARE does not "
                    f"list {MIDDLEWARE}, so only the streams that compress "
                    "themselves are compressed.",
                    hint=f"List {MIDDLEWARE} directly below SecurityMiddleware, or "
                    "set ENABLED to False.",
                    obj=settings,
                    id="next.W164",
                )
            )
        return messages
    below = [
        base
        for base in BODY_READERS
        if (index := _position(middleware, base)) is not None and index < position
    ]
    if below:
        messages.append(
            DjangoWarning(
                f"settings.MIDDLEWARE lists {MIDDLEWARE} below {', '.join(below)}, "
                "which then read the compressed body, so an ETag, a Content-Length "
                "or a cached copy describes compressed bytes.",
                hint=f"List {MIDDLEWARE} directly below SecurityMiddleware.",
                obj=settings,
                id="next.W165",
            )
        )
    return messages


def _cross_site(value: object) -> bool:
    """Whether a `SameSite` setting sends the cookie on cross-site requests."""
    return isinstance(value, str) and value.lower() == _CROSS_SITE


@register(NEXT)
def check_compression_cookies(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that a cross-site cookie lets another site drive a BREACH oracle."""
    if not compression_settings().enabled:
        return []
    cross_site = [
        name for name in _SAMESITE if _cross_site(getattr(settings, name, ""))
    ]
    if not cross_site:
        return []
    return [
        DjangoWarning(
            f"{' and '.join(cross_site)} is 'None' while compression is on, so another "
            "site can send credentialed requests and measure compressed lengths, the "
            "BREACH attack.",
            hint="Set the cookie to 'Lax' or 'Strict', or keep pages that reflect "
            "input beside a secret uncompressed.",
            obj=settings,
            id="next.W166",
        )
    ]


__all__ = [
    "BODY_READERS",
    "GZIP_MIDDLEWARE",
    "MIDDLEWARE",
    "check_compression_codecs",
    "check_compression_cookies",
    "check_compression_middleware",
    "check_compression_paths",
    "check_compression_settings",
]
