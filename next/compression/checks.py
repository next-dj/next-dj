"""System checks for the `COMPRESSION` scope, its codecs and the middleware order."""

from collections.abc import Mapping, Sequence
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning

from next.checks import NEXT, register
from next.checks.common import (
    DeclaringCall,
    callable_problem,
    class_problem,
    import_backend_class,
    misplaced_errors,
    raw_scope,
)
from next.conf.checks import error_for_misfit, error_for_unusable_path
from next.conf.compression import (
    SCOPE,
    SHAPE,
    CodecEntry,
    StreamOwner,
    TopicMode,
    compression_settings,
)
from next.conf.shapes import Shape, misfits
from next.pages.checks.access import load_declarations
from next.pages.compression import compress_declaration
from next.pages.loaders import PAGE_MODULE

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


def _written(scope: Mapping[str, object] | None, key: str) -> object:
    """Return the usable value the project writes for `key`, else `None`."""
    shape = SHAPE.keys[key]
    if scope is None or key not in scope:
        return None
    usable = isinstance(shape, Shape) and shape.usable(scope[key])
    return scope[key] if usable else None


def _imported(dotted: str) -> object:
    """Return what `dotted` imports, `None` when it does not import."""
    try:
        return import_backend_class(dotted)
    except ImportError:
        return None


type _Built = tuple[int, CodecEntry, CompressionBackend]


def _built() -> list[_Built]:
    """Return each usable `BACKENDS` entry with its position and a fresh backend."""
    found: list[_Built] = []
    for index, entry in enumerate(compression_settings().backends):
        if class_problem(entry.path, CompressionBackend) is None:
            found.append((index, entry, import_backend_class(entry.path)()))
    return found


def _by_coding(built: Sequence[_Built]) -> dict[str, CompressionBackend]:
    """Return the backends of `built` by coding, the first entry of each winning."""
    found: dict[str, CompressionBackend] = {}
    for _index, _entry, backend in built:
        found.setdefault(backend.coding, backend)
    return found


@register(NEXT)
def check_compression_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and the value shapes of the `COMPRESSION` scope."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    return [error_for_misfit(_PREFIX, misfit) for misfit in misfits(scope, SHAPE)]


def _path_error(where: str, dotted: str, problem: str) -> CheckMessage:
    """Build the `next.E219` error for one unusable `COMPRESSION` path."""
    return error_for_unusable_path(
        f"{_PREFIX}{where}",
        dotted,
        problem,
        "the framework skips it",
        "Name a concrete CompressionBackend subclass in each BACKENDS entry, and a "
        "callable taking the request and the response for EXCLUDE.",
    )


@register(NEXT)
def check_compression_paths(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `BACKENDS` or `EXCLUDE` path that names nothing usable (`next.E219`)."""
    current = compression_settings()
    errors = [
        _path_error(f"['BACKENDS'][{index}]['BACKEND']", entry.path, problem)
        for index, entry in enumerate(current.backends)
        if (problem := class_problem(entry.path, CompressionBackend)) is not None
    ]
    exclude = current.exclude
    problem = None if exclude is None else callable_problem(exclude)
    if exclude is not None and problem is not None:
        errors.append(_path_error("['EXCLUDE']", exclude, problem))
    return errors


def _codec_error(message: str, hint: str) -> CheckMessage:
    """Build one `next.E228` error."""
    return Error(message, hint=hint, obj=settings, id="next.E228")


def _unknown_codings(
    scope: Mapping[str, object] | None, backends: Mapping[str, CompressionBackend]
) -> list[CheckMessage]:
    """Report each coding `ORDER` names and no backend serves."""
    written = _written(scope, "ORDER")
    codings = list(written) if isinstance(written, Sequence) else []
    known = ", ".join(sorted(backends)) or "none"
    return [
        _codec_error(
            f"{_PREFIX}['ORDER'] names the coding {coding!r}, which no BACKENDS "
            "entry serves.",
            f"Use a coding a backend serves ({known}) or add its backend.",
        )
        for coding in dict.fromkeys(codings)
        if coding not in backends
    ]


def _option_errors(built: Sequence[_Built]) -> list[CheckMessage]:
    """Report each `LEVEL` or `WINDOW` option outside the range of its codec."""
    errors: list[CheckMessage] = []
    for index, entry, backend in built:
        where = f"{_PREFIX}['BACKENDS'][{index}]['OPTIONS']"
        level = entry.level
        if level is not None and not in_range(level, backend.levels):
            low, high = backend.levels
            errors.append(
                _codec_error(
                    f"{where}['LEVEL'] is {level}, outside {low} to {high}, so "
                    f"the {backend.coding!r} codec keeps its level {backend.level}.",
                    f"Write a level from {low} to {high}.",
                )
            )
        window = entry.window
        if window is not None and not in_range(window, backend.windows):
            span = backend.windows
            allowed = "no window" if span is None else f"{span[0]} to {span[1]}"
            errors.append(
                _codec_error(
                    f"{where}['WINDOW'] is {window}, while the {backend.coding!r} "
                    f"codec takes {allowed}, so it keeps its default window.",
                    "Drop the option or write a window the codec takes.",
                )
            )
    return errors


def _missing_library(backend: CompressionBackend) -> CheckMessage:
    """Build the `next.E228` error of a codec the project names without its library."""
    install = (
        "the library of the codec"
        if backend.extra is None
        else f"the {backend.extra!r} extra, pip install 'next.dj[{backend.extra}]'"
    )
    return _codec_error(
        f"{_PREFIX} names the {backend.coding!r} codec, whose library does not "
        "import, so no response is encoded with it.",
        f"Install {install}, or drop the coding from ORDER.",
    )


def _availability_errors(
    scope: Mapping[str, object] | None, backends: Mapping[str, CompressionBackend]
) -> list[CheckMessage]:
    """Report a codec the project names whose library is missing, and a dead reset mode.

    An extra's codec the defaults offer is left out quietly when its library is missing.
    """
    current = compression_settings()
    if not current.enabled:
        return []
    order = _written(scope, "ORDER")
    named = current.backends if _written(scope, "BACKENDS") is not None else ()
    ordered = [backends[c] for c in dict.fromkeys(current.order) if c in backends]
    errors = [
        _missing_library(backend)
        for backend in ordered
        if not backend.available()
        and (
            backend.extra is None
            or (isinstance(order, Sequence) and backend.coding in order)
            or _named(named, type(backend))
        )
    ]
    if current.sse_topics == TopicMode.RESET and not any(
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


def _named(entries: Sequence[CodecEntry], klass: type) -> bool:
    """Return whether one of the `entries` names `klass`."""
    return any(_imported(entry.path) is klass for entry in entries)


@register(NEXT)
def check_compression_codecs(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report unknown codings, levels and windows out of range, and missing codecs."""
    scope = raw_scope(SCOPE)
    built = _built()
    backends = _by_coding(built)
    return [
        *_unknown_codings(scope, backends),
        *_option_errors(built),
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
    """Report a doubled compressor and a middleware listed too low."""
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


@register(NEXT)
def check_stream_middleware(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that page streams go to a middleware `MIDDLEWARE` lacks (`next.W193`)."""
    current = compression_settings()
    middleware = list(getattr(settings, "MIDDLEWARE", None) or ())
    if (
        not current.enabled
        or current.streams != StreamOwner.MIDDLEWARE
        or _position(middleware, MIDDLEWARE) is not None
    ):
        return []
    return [
        DjangoWarning(
            f"{_PREFIX}['STREAMS'] is 'middleware' while settings.MIDDLEWARE lists no "
            f"{MIDDLEWARE}, so next.dj compresses no page stream.",
            hint=f"List {MIDDLEWARE} directly below SecurityMiddleware, or set "
            "STREAMS to 'self'.",
            obj=settings,
            id="next.W193",
        )
    ]


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


_COMPRESS_CALL: Final = DeclaringCall(
    "page.compress()", "next.E245", PAGE_MODULE, "page"
)


@register(NEXT)
def check_compress_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `page.compress()` call made outside a `page.py` (`next.E245`)."""
    load_declarations()
    return misplaced_errors(_COMPRESS_CALL, compress_declaration.outside())


__all__ = [
    "BODY_READERS",
    "GZIP_MIDDLEWARE",
    "MIDDLEWARE",
    "check_compress_declarations",
    "check_compression_codecs",
    "check_compression_cookies",
    "check_compression_middleware",
    "check_compression_paths",
    "check_compression_settings",
    "check_stream_middleware",
]
