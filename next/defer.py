"""The vocabulary of deferred boundaries and error boundaries, shared by every area."""

from __future__ import annotations

import enum
import keyword
import re
import reprlib
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from django.conf import settings
from django.utils.crypto import salted_hmac

from next.utils import RAW_TEXT_ELEMENTS, VOID_ELEMENTS


if TYPE_CHECKING:
    from django.template import Context


class DeferMode(enum.StrEnum):
    """How a boundary is delivered, `inline` keeping its wrapper for reloads."""

    STREAM = "stream"
    LOAD = "load"
    VISIBLE = "visible"
    MANUAL = "manual"
    INLINE = "inline"


class Delivery(enum.StrEnum):
    """How one response delivers its deferred boundaries, decided once per request."""

    STREAM = "stream"
    INLINE = "inline"
    SHELL = "shell"
    PREFETCH = "prefetch"


JOBLESS: Final[frozenset[Delivery]] = frozenset({Delivery.SHELL, Delivery.PREFETCH})
"""The deliveries that answer the loading UI and run no deferred job."""


class Depth(enum.StrEnum):
    """How much of a page a router prefetch asks for, which `request.next.depth` names.

    A shell runs no deferred job, and a full prefetch is the page a press may show.
    """

    SHELL = "shell"
    FULL = "full"


class WrapperTag(enum.Enum):
    """The `tag` no declaration names, a `div` unless the call site adopts the root."""

    SITE = "site"


class ErrorKind(enum.StrEnum):
    """Why a boundary settled as an error, the last two raised only by the client."""

    ERROR = "error"
    TIMEOUT = "timeout"
    OVERLOADED = "overloaded"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    BAD_REQUEST = "bad_request"
    INCOMPLETE = "incomplete"
    NETWORK = "network"

    @property
    def status(self) -> int | None:
        """Return the HTTP status this kind stands for, `None` for an unexpected one."""
        return _STATUSES.get(self)


_STATUSES: Final[Mapping[ErrorKind, int]] = {
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.BAD_REQUEST: 400,
    ErrorKind.OVERLOADED: 503,
}

RESERVED_OPTIONS: Final[frozenset[str]] = frozenset(
    {"defer", "timeout", "reload", "delay"}
)
"""Call-site keywords the component tag reserves, so none of them names a prop."""

TOKEN_OVERRIDES: Final[frozenset[str]] = frozenset(
    {"max_age", "bucket", "binding", "max_bytes"}
)
"""The token options one component may set over the project `TOKENS` mapping."""

DEFERRALS_KEY: Final = "_next_deferrals"
"""Context key of the `DeferralSink` a page view hands its deferred calls to."""

MAX_DURATION: Final = 86_400
"""The longest duration an option takes, one day in seconds."""

MIN_KEY_LENGTH: Final = 32
"""The fewest characters a token signing key holds."""

DEFAULT_TAG: Final = "div"
"""The wrapper element of a boundary whose call site and declaration name none."""

_DURATION = re.compile(r"(?P<amount>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s)")
_TAG = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_MS_PER_SECOND: Final = 1000
_SHOWN_DIGITS: Final = 18
_UNWRAPPING_TAGS: Final = (
    VOID_ELEMENTS
    | RAW_TEXT_ELEMENTS
    | {
        "applet",
        "body",
        "frameset",
        "head",
        "html",
        "image",
        "listing",
        "object",
        "plaintext",
        "template",
    }
)
"""Void and raw-text elements, plus those a parser frames, rewrites or keeps inert."""


class BoundaryOptionError(ValueError):
    """A boundary option written with a value outside its shape."""


def _shown(value: object) -> str:
    """Return how an error names a value, a huge integer without its digits."""
    if isinstance(value, int) and abs(value) >= 10**_SHOWN_DIGITS:
        return "a very large integer"
    return reprlib.repr(value)


def _literal_seconds(value: str) -> float:
    """Return the seconds a `"2s"` or `"500ms"` literal names."""
    match = _DURATION.fullmatch(value)
    if match is None:
        msg = f"a duration literal reads like '2s' or '500ms', got {_shown(value)}"
        raise BoundaryOptionError(msg)
    seconds = float(match["amount"])
    return seconds / _MS_PER_SECOND if match["unit"] == "ms" else seconds


def parse_duration(value: object) -> float:
    """Return seconds from a number of seconds or a `"2s"` or `"500ms"` literal.

    A bare numeric string is refused, since a template literal reads it as milliseconds.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        msg = f"a duration is a number of seconds or a '2s' literal, got {value!r}"
        raise BoundaryOptionError(msg)
    seconds = _literal_seconds(value) if isinstance(value, str) else value
    if not 0 <= seconds <= MAX_DURATION:
        msg = (
            "a duration is a number of seconds from 0 up to one day, got "
            f"{_shown(value)}"
        )
        raise BoundaryOptionError(msg)
    return float(seconds)


def _mode(value: object) -> DeferMode | None:
    if value is None:
        return None
    if isinstance(value, str) and value in DeferMode:
        return DeferMode(value)
    modes = ", ".join(DeferMode)
    msg = f"mode must be one of {modes}, got {value!r}"
    raise BoundaryOptionError(msg)


def _timeout(value: object) -> float | None:
    if value is None:
        return None
    seconds = parse_duration(value)
    if seconds == 0:
        msg = "timeout must be above zero, None leaves the boundary unbounded"
        raise BoundaryOptionError(msg)
    return seconds


def _props(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        msg = f"props takes a tuple of names, got {type(value).__name__!r}"
        raise BoundaryOptionError(msg)
    names: dict[str, None] = {}
    for name in value:
        if not isinstance(name, str) or not name.isidentifier():
            msg = f"a prop is named by a Python identifier, got {name!r}"
            raise BoundaryOptionError(msg)
        if keyword.iskeyword(name):
            msg = f"a prop is named by a Python identifier, got {name!r}"
            raise BoundaryOptionError(msg)
        if name in RESERVED_OPTIONS:
            msg = f"the prop {name!r} is reserved by the component tag"
            raise BoundaryOptionError(msg)
        if name in names:
            msg = f"the prop {name!r} is declared twice"
            raise BoundaryOptionError(msg)
        names[name] = None
    return tuple(names)


def _tag(value: object) -> str | None:
    if value is None:
        return None
    if value is WrapperTag.SITE:
        return DEFAULT_TAG
    if not isinstance(value, str) or not _TAG.fullmatch(value):
        msg = f"tag names an HTML element or is None, got {value!r}"
        raise BoundaryOptionError(msg)
    if value.lower() in _UNWRAPPING_TAGS:
        msg = (
            f"tag names an element that cannot wrap the loading HTML, got {value!r}, "
            "use a container such as div, span or section"
        )
        raise BoundaryOptionError(msg)
    return value


def _parallel(value: object) -> bool:
    if isinstance(value, bool):
        return value
    msg = f"parallel must be True or False, got {type(value).__name__!r}"
    raise BoundaryOptionError(msg)


def _count(value: object, *, least: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _token_value(key: str, value: object) -> object:
    if key == "binding":
        usable = isinstance(value, str) and bool(value)
    elif key == "bucket":
        usable = value is None or _count(value, least=0)
    else:
        usable = _count(value, least=1)
    if not usable:
        msg = f"tokens[{key!r}] got the unusable value {value!r}"
        raise BoundaryOptionError(msg)
    return value


def _tokens(value: object) -> tuple[tuple[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        msg = f"tokens takes a mapping or None, got {type(value).__name__!r}"
        raise BoundaryOptionError(msg)
    unknown = sorted(str(key) for key in value if key not in TOKEN_OVERRIDES)
    if unknown:
        allowed = ", ".join(sorted(TOKEN_OVERRIDES))
        msg = f"tokens takes the keys {allowed}, got {', '.join(unknown)}"
        raise BoundaryOptionError(msg)
    tokens = {key: _token_value(key, value[key]) for key in sorted(value)}
    bucket, max_age = tokens.get("bucket"), tokens.get("max_age")
    if isinstance(bucket, int) and isinstance(max_age, int) and bucket >= max_age:
        msg = f"tokens bucket {bucket} must stay below max_age {max_age}"
        raise BoundaryOptionError(msg)
    return tuple(tokens.items())


@dataclass(frozen=True, slots=True)
class BoundaryOptions:
    """What a `component.py` or a `page.py` declares about its boundary.

    A `None` mode leaves the files to decide, an undeclared tag the call site.
    """

    mode: DeferMode | None = None
    timeout: float | None = None
    props: tuple[str, ...] = ()
    tag: str | None = DEFAULT_TAG
    tag_declared: bool = False
    parallel: bool = True
    tokens: tuple[tuple[str, object], ...] = ()

    @classmethod
    def parse(
        cls,
        mode: object = DeferMode.STREAM,
        *,
        timeout: object = None,
        props: object = (),
        tag: object = WrapperTag.SITE,
        parallel: object = True,
        tokens: object = None,
    ) -> BoundaryOptions:
        """Return the options as written, raising `BoundaryOptionError` on a misfit."""
        return cls(
            mode=_mode(mode),
            timeout=_timeout(timeout),
            props=_props(props),
            tag=_tag(tag),
            tag_declared=tag is not WrapperTag.SITE,
            parallel=_parallel(parallel),
            tokens=_tokens(tokens),
        )


@dataclass(frozen=True, slots=True)
class BoundaryFailure:
    """The `error` an `error.djx` reads, strings and integers only, never an exception.

    Outside `DEBUG` it has no `message` or `traceback`, which a template reads as empty.
    """

    kind: ErrorKind
    digest: str
    status: int | None
    retry: bool

    @staticmethod
    def of(
        kind: ErrorKind, digest: str, *, retry: bool, message: str, traceback: str
    ) -> BoundaryFailure:
        """Return the error of `kind`, with its message and traceback under `DEBUG`."""
        if settings.DEBUG:
            return DebugBoundaryFailure(
                kind, digest, kind.status, retry, message, traceback
            )
        return BoundaryFailure(kind, digest, kind.status, retry)


@dataclass(frozen=True, slots=True)
class DebugBoundaryFailure(BoundaryFailure):
    """A `BoundaryFailure` with the message and the traceback a `DEBUG` page shows."""

    message: str = field(repr=False)
    traceback: str = field(repr=False)


class Addressing(enum.StrEnum):
    """Whether a boundary is a page body or a component call."""

    PAGE = "page"
    COMPONENT = "component"


@dataclass(frozen=True, slots=True)
class Site:
    """One component call site in one template, or one page boundary file.

    `name` is the component name or the page path, `origin` the template it sits in.
    """

    addressing: Addressing
    name: str
    mode: DeferMode
    origin: str
    index: int = 0


class CallRenderer(Protocol):
    """How the component tag renders the parts of one deferred call."""

    def loading(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the loading UI, with the error a misfit shape at its site settled."""

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the component inside its error boundary, with the error it showed."""

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        """Render the error UI of `kind` without the component, with the error shown."""


@dataclass(frozen=True, slots=True)
class DeferredCall:
    """One deferred component call as its tag hands it over.

    `tag` is the wrapper element, `None` where the boundary adopts the root instead.
    """

    site: Site
    options: BoundaryOptions
    key: str
    tag: str | None
    source: str | None
    renderer: CallRenderer


_KEY_SALT: Final = "next.defer.key"
_KEY_LENGTH: Final = 8


def stable_key(origin: str, index: int | str) -> str:
    """Return the key of a call site, the same across responses, users and hosts."""
    digest = salted_hmac(_KEY_SALT, f"{origin}:{index}", algorithm="sha256")
    return digest.hexdigest()[:_KEY_LENGTH]


_deadline: ContextVar[float | None] = ContextVar("next_defer_deadline", default=None)


def remaining() -> float | None:
    """Return the seconds left before the current job's deadline, `None` without one."""
    at = _deadline.get()
    if at is None:
        return None
    return max(0.0, at - time.monotonic())


@contextmanager
def deadline(at: float | None) -> Generator[None]:
    """Run the block under a deadline at the `time.monotonic()` instant `at`."""
    token = _deadline.set(at)
    try:
        yield
    finally:
        _deadline.reset(token)


__all__ = [
    "DEFAULT_TAG",
    "DEFERRALS_KEY",
    "JOBLESS",
    "MAX_DURATION",
    "MIN_KEY_LENGTH",
    "RESERVED_OPTIONS",
    "TOKEN_OVERRIDES",
    "Addressing",
    "BoundaryFailure",
    "BoundaryOptionError",
    "BoundaryOptions",
    "CallRenderer",
    "DebugBoundaryFailure",
    "DeferMode",
    "DeferredCall",
    "Delivery",
    "Depth",
    "ErrorKind",
    "Site",
    "WrapperTag",
    "deadline",
    "parse_duration",
    "remaining",
    "stable_key",
]
