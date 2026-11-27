"""The shape of the `ROUTER` scope and the reads of its values with defaults."""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeGuard

from next.caches import IdentityMemo

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import (
    EMPTY,
    Section,
    Shape,
    count,
    enforce,
    flag,
    member,
    modes,
    number,
    pick,
    shown,
    text,
)


SCOPE: Final = "ROUTER"
ACTIVE_LINKS: Final = "ACTIVE_LINKS"


class RouterScope(enum.StrEnum):
    """Which links the client router owns, the pages, all or those it is let to own."""

    PAGES = "pages"
    ALL = "all"
    OPT_IN = "opt_in"


class PrefetchMode(enum.StrEnum):
    """When the client router prefetches a link."""

    INTENT = "intent"
    VIEWPORT = "viewport"
    OFF = "off"


class PressMode(enum.StrEnum):
    """Which pointers send the full page request at the press of a link."""

    MOUSE = "mouse"
    ALL = "all"
    OFF = "off"


class ForeignMode(enum.StrEnum):
    """How the client router follows a link whose answer is no navigation envelope."""

    HARD = "hard"
    EXTRACT = "extract"


class InterceptMode(enum.StrEnum):
    """Whether a plain link to an intercepted page opens its layer."""

    OPT_IN = "opt_in"
    LINKS = "links"


class LinkPass(enum.StrEnum):
    """Where the active-link pass marks links, compiled templates or whole documents."""

    TEMPLATE = "template"
    DOCUMENT = "document"


class Eagerness(enum.StrEnum):
    """The eagerness values `SPECULATION` may name."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    EAGER = "eager"


EAGERNESS_KEY: Final = "eagerness"
"""The one key of the mapping form of `SPECULATION`."""

_PRERENDER: Final = "prerender"

CONTAINER_SELECTOR: Final = re.compile(
    r"(?P<tag>[a-zA-Z][a-zA-Z0-9-]*)"
    r"|\[(?P<attr>[a-zA-Z_][a-zA-Z0-9_-]*)"
    r"(?:=(?:\"(?P<double>[^\"\\\n]*)\"|'(?P<single>[^'\\\n]*)'"
    r"|(?P<bare>-?[a-zA-Z_][a-zA-Z0-9_-]*)))?\]"
)
"""The one grammar of a container selector, a tag name, `[attr]` or `[attr=value]`."""


_COUNT: Final = count(1)
_SECONDS: Final = number(positive=True)


def _prefixes(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and item.startswith("/") for item in value
    )


def _speculation(value: object) -> TypeGuard[str | Mapping[str, object] | None]:
    return value is None or isinstance(value, str | Mapping)


def _switch(value: object) -> TypeGuard[bool | Mapping[str, object]]:
    return isinstance(value, bool | Mapping)


def is_simple_selector(value: object) -> bool:
    """Whether `value` is a container selector the server pass can read."""
    return isinstance(value, str) and CONTAINER_SELECTOR.fullmatch(value) is not None


def _selectors(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(map(is_simple_selector, value))


_FLAG: Final = Shape(flag, "True or False")
_COUNTED: Final = Shape(_COUNT, "an integer from 1")
_TIMED: Final = Shape(_SECONDS, "a number of seconds above 0")

SHAPE: Final = Section(
    {
        "ENABLED": _FLAG,
        "SCOPE": modes(RouterScope),
        "EXCLUDE": Shape(_prefixes, "a list of URL path prefixes starting with '/'"),
        "FOREIGN": modes(ForeignMode),
        "PREFETCH": modes(PrefetchMode),
        "PREFETCH_DELAY": _TIMED,
        "PREFETCH_TTL": _TIMED,
        "PREFETCH_SHELL_TTL": _TIMED,
        "PREFETCH_ENTRIES": _COUNTED,
        "PRESS": modes(PressMode),
        "SNAPSHOTS": _COUNTED,
        "PROGRESS_DELAY": _TIMED,
        "PROGRESS": _FLAG,
        "TRANSITIONS": _FLAG,
        "INTERCEPT": modes(InterceptMode),
        "SPECULATION": Shape(_speculation, "None, an eagerness or a dict"),
        "CUT": Shape(text, "the dotted path of a NavigationCut subclass"),
        "DIGEST": Shape(text, "the dotted path of a ChromeDigest subclass"),
        "SHED": Shape(text, "the dotted path of a PrefetchShed subclass"),
        ACTIVE_LINKS: Section(
            {
                "ENABLED": _FLAG,
                "PASS": modes(LinkPass),
                "CONTAINERS": Shape(
                    _selectors,
                    "a list of selectors, each a tag name, [attr] or [attr=value]",
                ),
            },
            other=Shape(flag, "True, False or a dict"),
        ),
    }
)
"""Every `ROUTER` key with what it accepts, `ACTIVE_LINKS` nested as its own section."""


_EAGER: Final = member(Eagerness)


def _speculation_read(value: object) -> tuple[Eagerness | None, str | None]:
    """Return the eagerness `SPECULATION` names and why it emits no rules, one None."""
    if isinstance(value, Mapping):
        if _PRERENDER in value:
            return None, "asks for prerender, which the router never emits"
        unknown = sorted(str(key) for key in value if key != EAGERNESS_KEY)
        if unknown:
            return None, f"has unknown keys {', '.join(map(repr, unknown))}"
        value = value.get(EAGERNESS_KEY)
    if _EAGER(value):
        return Eagerness(value), None
    named = ", ".join(Eagerness)
    return None, f"names the eagerness {shown(value)}, not one of {named}"


def speculation_problem(value: object) -> str | None:
    """Return why a `SPECULATION` string or mapping emits no rules, else `None`."""
    return _speculation_read(value)[1]


def speculation_eagerness(value: object) -> Eagerness | None:
    """Return the eagerness `SPECULATION` names, `None` when unset or unusable."""
    return _speculation_read(value)[0]


@dataclass(frozen=True, slots=True)
class RouterSettings:
    """The `ROUTER` scope read once, each omitted key at its default."""

    enabled: bool
    scope: RouterScope
    exclude: tuple[str, ...]
    foreign: ForeignMode
    prefetch: PrefetchMode
    prefetch_delay: float
    prefetch_ttl: float
    prefetch_shell_ttl: float
    prefetch_entries: int
    press: PressMode
    snapshots: int
    progress_delay: float
    progress: bool
    transitions: bool
    intercept: InterceptMode
    speculation: object
    cut: str
    digest: str
    shed: str
    links_enabled: bool
    links_pass: LinkPass
    links_containers: tuple[str, ...]


def read_settings(scope: Mapping[str, object]) -> RouterSettings:
    """Return the settings `scope` asks for, refusing a value outside its shape."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    enforce(f"NEXT_FRAMEWORK[{SCOPE!r}]", scope, SHAPE)
    switch = pick(scope, ACTIVE_LINKS, _switch, defaults)
    current: Mapping[str, object] = (
        {"ENABLED": switch} if isinstance(switch, bool) else switch
    )
    nested: Mapping[str, Any] = defaults[ACTIVE_LINKS]
    return RouterSettings(
        enabled=pick(scope, "ENABLED", flag, defaults),
        scope=RouterScope(pick(scope, "SCOPE", member(RouterScope), defaults)),
        exclude=tuple(pick(scope, "EXCLUDE", _prefixes, defaults)),
        foreign=ForeignMode(pick(scope, "FOREIGN", member(ForeignMode), defaults)),
        prefetch=PrefetchMode(pick(scope, "PREFETCH", member(PrefetchMode), defaults)),
        prefetch_delay=float(pick(scope, "PREFETCH_DELAY", _SECONDS, defaults)),
        prefetch_ttl=float(pick(scope, "PREFETCH_TTL", _SECONDS, defaults)),
        prefetch_shell_ttl=float(pick(scope, "PREFETCH_SHELL_TTL", _SECONDS, defaults)),
        prefetch_entries=pick(scope, "PREFETCH_ENTRIES", _COUNT, defaults),
        press=PressMode(pick(scope, "PRESS", member(PressMode), defaults)),
        snapshots=pick(scope, "SNAPSHOTS", _COUNT, defaults),
        progress_delay=float(pick(scope, "PROGRESS_DELAY", _SECONDS, defaults)),
        progress=pick(scope, "PROGRESS", flag, defaults),
        transitions=pick(scope, "TRANSITIONS", flag, defaults),
        intercept=InterceptMode(
            pick(scope, "INTERCEPT", member(InterceptMode), defaults)
        ),
        speculation=pick(scope, "SPECULATION", _speculation, defaults),
        cut=pick(scope, "CUT", text, defaults),
        digest=pick(scope, "DIGEST", text, defaults),
        shed=pick(scope, "SHED", text, defaults),
        links_enabled=pick(current, "ENABLED", flag, nested),
        links_pass=LinkPass(pick(current, "PASS", member(LinkPass), nested)),
        links_containers=tuple(pick(current, "CONTAINERS", _selectors, nested)),
    )


def _read() -> RouterSettings:
    """Read the settings of the merged `ROUTER` mapping, empty for no mapping."""
    held = next_framework_settings.ROUTER
    return read_settings(held if isinstance(held, Mapping) else EMPTY)


_SETTINGS: Final[IdentityMemo[RouterSettings]] = IdentityMemo()


def router_settings() -> RouterSettings:
    """Return the `ROUTER` settings, read again once the merged mapping moves."""
    return _SETTINGS.get(next_framework_settings.ROUTER, _read)


def router_enabled() -> bool:
    """Return whether the client router is on, `ROUTER["ENABLED"]`."""
    return _SETTINGS.get(next_framework_settings.ROUTER, _read).enabled


__all__ = [
    "ACTIVE_LINKS",
    "CONTAINER_SELECTOR",
    "EAGERNESS_KEY",
    "SCOPE",
    "SHAPE",
    "Eagerness",
    "ForeignMode",
    "InterceptMode",
    "LinkPass",
    "PrefetchMode",
    "PressMode",
    "RouterScope",
    "RouterSettings",
    "is_simple_selector",
    "read_settings",
    "router_enabled",
    "router_settings",
    "speculation_eagerness",
    "speculation_problem",
]
