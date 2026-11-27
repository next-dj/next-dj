"""What a `component.py` or a `page.py` declares about its boundary, parsed once."""

from __future__ import annotations

import enum
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from next.errors import DurationError
from next.utils import RAW_TEXT_ELEMENTS, VOID_ELEMENTS, parse_duration

from .errors import BoundaryOptionError


class DeferMode(enum.StrEnum):
    """How a boundary is delivered, `inline` keeping its wrapper for reloads."""

    STREAM = "stream"
    LOAD = "load"
    VISIBLE = "visible"
    MANUAL = "manual"
    INLINE = "inline"


class WrapperTag(enum.Enum):
    """The `tag` no declaration names, a `div` unless the call site adopts the root."""

    SITE = "site"


RESERVED_OPTIONS: Final[frozenset[str]] = frozenset(
    {"defer", "timeout", "reload", "delay"}
)
"""Call-site keywords the component tag reserves, so none of them names a prop."""

TOKEN_OVERRIDES: Final[frozenset[str]] = frozenset({"max_age", "binding", "max_bytes"})
"""The token options one component may set over the project `TOKENS` mapping."""

DEFAULT_TAG: Final = "div"
"""The wrapper element of a boundary whose call site and declaration name none."""

_TAG = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
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
    try:
        seconds = parse_duration(value)
    except DurationError as exc:
        raise BoundaryOptionError(str(exc)) from exc
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
    return tuple((key, _token_value(key, value[key])) for key in sorted(value))


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


__all__ = [
    "DEFAULT_TAG",
    "RESERVED_OPTIONS",
    "TOKEN_OVERRIDES",
    "BoundaryOptions",
    "DeferMode",
    "WrapperTag",
]
