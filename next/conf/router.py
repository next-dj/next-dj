"""The shape of the `ROUTER` scope and the reads of its values with defaults."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, TypeGuard

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings


SCOPE: Final = "ROUTER"
CURRENT: Final = "CURRENT"
SCOPES: Final = ("all", "opt-in")
"""Which links the client router owns, each one or those inside `[data-next-router]`."""

PREFETCH_MODES: Final = ("intent", "viewport", "off")
"""When the client router prefetches a link."""

PRESS_MODES: Final = ("mouse", "all", "off")
"""Which pointers send the full page request at the press of a link."""

FOREIGN_MODES: Final = ("hard", "extract")
"""How the client router follows a link whose answer is no navigation envelope."""

INTERCEPT_MODES: Final = ("opt-in", "links")
"""Whether a plain link to an intercepted page opens its layer."""

LINKS: Final = "links"
"""The `INTERCEPT` mode in which a plain link to an intercepted page opens its layer."""

EAGERNESS: Final = ("conservative", "moderate", "eager")
"""The eagerness values `SPECULATION` may name."""

EAGERNESS_KEY: Final = "eagerness"
"""The one key of the mapping form of `SPECULATION`."""

_PRERENDER: Final = "prerender"

type Usable = Callable[[object], bool]

CONTAINER_SELECTOR: Final = re.compile(
    r"(?P<tag>[a-zA-Z][a-zA-Z0-9-]*)"
    r"|\[(?P<attr>[a-zA-Z_][a-zA-Z0-9_-]*)"
    r"(?:=(?:\"(?P<double>[^\"\\\n]*)\"|'(?P<single>[^'\\\n]*)'"
    r"|(?P<bare>-?[a-zA-Z_][a-zA-Z0-9_-]*)))?\]"
)
"""The one grammar of a container selector, a tag name, `[attr]` or `[attr=value]`."""


def _flag(value: object) -> TypeGuard[bool]:
    return isinstance(value, bool)


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _seconds(value: object) -> TypeGuard[float]:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and value > 0


def _path(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _prefixes(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) and item.startswith("/") for item in value
    )


def _speculation(value: object) -> TypeGuard[str | Mapping[str, object] | None]:
    return value is None or isinstance(value, str | Mapping)


def _switch(value: object) -> TypeGuard[bool | Mapping[str, object]]:
    return isinstance(value, bool | Mapping)


def _choice(values: tuple[str, ...]) -> Callable[[object], TypeGuard[str]]:
    def usable(value: object) -> TypeGuard[str]:
        return isinstance(value, str) and value in values

    return usable


def is_simple_selector(value: object) -> bool:
    """Whether `value` is a container selector the server pass can read."""
    return isinstance(value, str) and CONTAINER_SELECTOR.fullmatch(value) is not None


def _selectors(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, list | tuple) and all(map(is_simple_selector, value))


SHAPES: Final[Mapping[str, tuple[Usable, str]]] = MappingProxyType(
    {
        "ENABLED": (_flag, "True or False"),
        "SCOPE": (_choice(SCOPES), "'all' or 'opt-in'"),
        "EXCLUDE": (_prefixes, "a list of URL path prefixes starting with '/'"),
        "FOREIGN": (_choice(FOREIGN_MODES), "'hard' or 'extract'"),
        "PREFETCH": (_choice(PREFETCH_MODES), "'intent', 'viewport' or 'off'"),
        "PREFETCH_DELAY": (_seconds, "a number of seconds above 0"),
        "PREFETCH_TTL": (_seconds, "a number of seconds above 0"),
        "PREFETCH_SHELL_TTL": (_seconds, "a number of seconds above 0"),
        "PREFETCH_ENTRIES": (_count, "an integer from 1"),
        "PRESS": (_choice(PRESS_MODES), "'mouse', 'all' or 'off'"),
        "SNAPSHOTS": (_count, "an integer from 1"),
        "PROGRESS_DELAY": (_seconds, "a number of seconds above 0"),
        "PROGRESS": (_flag, "True or False"),
        "TRANSITIONS": (_flag, "True or False"),
        "INTERCEPT": (_choice(INTERCEPT_MODES), "'opt-in' or 'links'"),
        "SPECULATION": (_speculation, "None, an eagerness or a dict"),
        "MAX_LAYOUTS": (_count, "an integer from 1"),
        "CUT": (_path, "the dotted path of a NavigationCut subclass"),
        "DIGEST": (_path, "the dotted path of a ChromeDigest subclass"),
        CURRENT: (_switch, "True, False or a dict"),
    }
)
"""Every `ROUTER` key with the predicate it passes and the words naming it."""

CURRENT_SHAPES: Final[Mapping[str, tuple[Usable, str]]] = MappingProxyType(
    {
        "ENABLED": (_flag, "True or False"),
        "BACKEND": (_path, "the dotted path of a CurrentLinks subclass"),
        "CONTAINERS": (
            _selectors,
            "a list of selectors, each a tag name, [attr] or [attr=value]",
        ),
    }
)
"""Every `ROUTER["CURRENT"]` key with the predicate it passes."""


@dataclass(frozen=True, slots=True)
class Misfit:
    """One `ROUTER` key or value outside its shape, at its path below the scope.

    `unknown` names the keys a mapping holds that the shape does not know.
    """

    path: tuple[str, ...]
    expected: str = ""
    found: str = ""
    unknown: tuple[str, ...] = ()


def _shown(value: object) -> str:
    """Return how a report names a misfit value, a scalar by its repr."""
    if value is None or isinstance(value, int | float | str):
        return repr(value)
    return f"a {type(value).__name__}"


def _level(
    scope: Mapping[str, object],
    shapes: Mapping[str, tuple[Usable, str]],
    path: tuple[str, ...],
) -> list[Misfit]:
    """Return the misfits of one mapping level, its unknown keys first."""
    unknown = tuple(sorted(str(key) for key in scope if key not in shapes))
    found = [Misfit(path, unknown=unknown)] if unknown else []
    found.extend(
        Misfit((*path, key), expected, _shown(scope[key]))
        for key, (usable, expected) in shapes.items()
        if key in scope and not usable(scope[key])
    )
    return found


def misfits(scope: Mapping[str, object]) -> list[Misfit]:
    """Return every unknown key and every value of `scope` outside its shape."""
    found = _level(scope, SHAPES, ())
    current = scope.get(CURRENT)
    if isinstance(current, Mapping):
        found.extend(_level(current, CURRENT_SHAPES, (CURRENT,)))
    return found


def _speculation_read(value: object) -> tuple[str | None, str | None]:
    """Return the eagerness `SPECULATION` names and why it emits no rules, one None."""
    if isinstance(value, Mapping):
        if _PRERENDER in value:
            return None, "asks for prerender, which the router never emits"
        unknown = sorted(str(key) for key in value if key != EAGERNESS_KEY)
        if unknown:
            return None, f"has unknown keys {', '.join(map(repr, unknown))}"
        value = value.get(EAGERNESS_KEY)
    if isinstance(value, str) and value in EAGERNESS:
        return value, None
    named = ", ".join(EAGERNESS)
    return None, f"names the eagerness {_shown(value)}, not one of {named}"


def speculation_problem(value: object) -> str | None:
    """Return why a `SPECULATION` string or mapping emits no rules, else `None`."""
    return _speculation_read(value)[1]


def speculation_eagerness(value: object) -> str | None:
    """Return the eagerness `SPECULATION` names, `None` when unset or unusable."""
    return _speculation_read(value)[0]


@dataclass(frozen=True, slots=True)
class RouterSettings:
    """The `ROUTER` scope read once, each omitted or misfit key at its default."""

    enabled: bool
    scope: str
    exclude: tuple[str, ...]
    foreign: str
    prefetch: str
    prefetch_delay: float
    prefetch_ttl: float
    prefetch_shell_ttl: float
    prefetch_entries: int
    press: str
    snapshots: int
    progress_delay: float
    progress: bool
    transitions: bool
    intercept: str
    speculation: object
    max_layouts: int
    cut: str
    digest: str
    current_enabled: bool
    current_backend: str
    current_containers: tuple[str, ...]


def _pick[T](
    scope: Mapping[str, object],
    key: str,
    usable: Callable[[object], TypeGuard[T]],
    defaults: Mapping[str, Any],
) -> T:
    """Return the value of `key` when `usable` passes it, its default when omitted."""
    value = scope.get(key)
    if key in scope and usable(value):
        return value
    default: T = defaults[key]
    return default


def read_settings(scope: Mapping[str, object]) -> RouterSettings:
    """Return the settings `scope` asks for, the default replacing each misfit."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    switch = _pick(scope, CURRENT, _switch, defaults)
    current: Mapping[str, object] = (
        {"ENABLED": switch} if isinstance(switch, bool) else switch
    )
    nested: Mapping[str, Any] = defaults[CURRENT]
    return RouterSettings(
        enabled=_pick(scope, "ENABLED", _flag, defaults),
        scope=_pick(scope, "SCOPE", _choice(SCOPES), defaults),
        exclude=tuple(_pick(scope, "EXCLUDE", _prefixes, defaults)),
        foreign=_pick(scope, "FOREIGN", _choice(FOREIGN_MODES), defaults),
        prefetch=_pick(scope, "PREFETCH", _choice(PREFETCH_MODES), defaults),
        prefetch_delay=float(_pick(scope, "PREFETCH_DELAY", _seconds, defaults)),
        prefetch_ttl=float(_pick(scope, "PREFETCH_TTL", _seconds, defaults)),
        prefetch_shell_ttl=float(
            _pick(scope, "PREFETCH_SHELL_TTL", _seconds, defaults)
        ),
        prefetch_entries=_pick(scope, "PREFETCH_ENTRIES", _count, defaults),
        press=_pick(scope, "PRESS", _choice(PRESS_MODES), defaults),
        snapshots=_pick(scope, "SNAPSHOTS", _count, defaults),
        progress_delay=float(_pick(scope, "PROGRESS_DELAY", _seconds, defaults)),
        progress=_pick(scope, "PROGRESS", _flag, defaults),
        transitions=_pick(scope, "TRANSITIONS", _flag, defaults),
        intercept=_pick(scope, "INTERCEPT", _choice(INTERCEPT_MODES), defaults),
        speculation=_pick(scope, "SPECULATION", _speculation, defaults),
        max_layouts=_pick(scope, "MAX_LAYOUTS", _count, defaults),
        cut=_pick(scope, "CUT", _path, defaults),
        digest=_pick(scope, "DIGEST", _path, defaults),
        current_enabled=_pick(current, "ENABLED", _flag, nested),
        current_backend=_pick(current, "BACKEND", _path, nested),
        current_containers=tuple(_pick(current, "CONTAINERS", _selectors, nested)),
    )


class _Memo:
    """Hold the settings read from the merged `ROUTER` mapping held now."""

    __slots__ = ("held", "settings")

    def __init__(self) -> None:
        """Start before the first read."""
        self.held: object = None
        self.settings: RouterSettings | None = None

    def get(self) -> RouterSettings:
        """Return the settings of the current mapping, reading them when it moved."""
        held = next_framework_settings.ROUTER
        settings = self.settings
        if settings is None or self.held is not held:
            scope = held if isinstance(held, Mapping) else {}
            settings = read_settings(scope)
            self.held, self.settings = held, settings
        return settings


_memo = _Memo()


def router_settings() -> RouterSettings:
    """Return the `ROUTER` settings, read again once the merged mapping moves."""
    return _memo.get()


def router_enabled() -> bool:
    """Return whether the client router is on, `ROUTER["ENABLED"]`."""
    return _memo.get().enabled


__all__ = [
    "CONTAINER_SELECTOR",
    "CURRENT",
    "CURRENT_SHAPES",
    "EAGERNESS",
    "EAGERNESS_KEY",
    "FOREIGN_MODES",
    "INTERCEPT_MODES",
    "LINKS",
    "PREFETCH_MODES",
    "PRESS_MODES",
    "SCOPE",
    "SCOPES",
    "SHAPES",
    "Misfit",
    "RouterSettings",
    "is_simple_selector",
    "misfits",
    "read_settings",
    "router_enabled",
    "router_settings",
    "speculation_eagerness",
    "speculation_problem",
]
