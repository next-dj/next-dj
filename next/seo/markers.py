"""Value objects a `sitemap.py` and a `robots.py` declare their entries with.

Both validate on construction, so a value that would break the document never renders.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Final, TypeGuard

from next.utils import is_int

from .errors import RobotsRuleError, SitemapEntryError


CHANGEFREQS: Final = frozenset(
    {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}
)
"""The `changefreq` values the sitemap protocol names."""

_AGENT: Final = re.compile(r"[A-Za-z0-9._*-]+")
_PATH_START: Final = ("/", "*")
_CONTROL: Final = re.compile(r"[\x00-\x20\x7f#]")


def is_number(value: object) -> TypeGuard[float]:
    """Whether `value` is a finite int or float that is not a bool."""
    return is_int(value) or (isinstance(value, float) and math.isfinite(value))


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """One URL an `@sitemap.items` callable lists for its trail."""

    kwargs: Mapping[str, object] = field(default_factory=dict)
    lastmod: date | None = None
    changefreq: str | None = None
    priority: float | None = None

    def __post_init__(self) -> None:
        """Pin the kwargs read-only and refuse a value the protocol has no place for."""
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))
        if self.lastmod is not None and not isinstance(self.lastmod, date):
            raise SitemapEntryError(
                self.lastmod, field="lastmod", expected="a date or a datetime"
            )
        if self.changefreq is not None and self.changefreq not in CHANGEFREQS:
            raise SitemapEntryError(
                self.changefreq,
                field="changefreq",
                expected="one of " + ", ".join(sorted(CHANGEFREQS)),
            )
        priority = self.priority
        if priority is not None and not (is_number(priority) and 0 <= priority <= 1):
            raise SitemapEntryError(
                priority, field="priority", expected="a number from 0 to 1"
            )


def _as_tuple(value: str | Sequence[str]) -> tuple[str, ...]:
    """Return the strings of `value`, a bare string read as one."""
    return (value,) if isinstance(value, str) else tuple(value)


def _check_path(name: str, path: object) -> None:
    """Refuse a path that is no string, starts nowhere or carries a line break."""
    if (
        not isinstance(path, str)
        or not path.startswith(_PATH_START)
        or _CONTROL.search(path) is not None
        or "$" in path[:-1]
    ):
        raise RobotsRuleError(
            path,
            field=name,
            expected="a path starting with '/' or '*', without spaces, control "
            "characters or '#', and '$' only at its end",
        )


@dataclass(frozen=True, slots=True)
class RobotsRule:
    """One `User-agent` group of a `robots.py`, a bare string read as one value."""

    user_agent: str | Sequence[str] = "*"
    allow: str | Sequence[str] = ()
    disallow: str | Sequence[str] = ()
    crawl_delay: float | None = None

    def __post_init__(self) -> None:
        """Pin every value as a tuple and refuse one that would break the group."""
        agents = _as_tuple(self.user_agent)
        if not agents or any(
            not isinstance(agent, str) or _AGENT.fullmatch(agent) is None
            for agent in agents
        ):
            raise RobotsRuleError(
                self.user_agent, field="user_agent", expected="one or more agent tokens"
            )
        object.__setattr__(self, "user_agent", agents)
        for name in ("allow", "disallow"):
            paths = _as_tuple(getattr(self, name))
            for path in paths:
                _check_path(name, path)
            object.__setattr__(self, name, paths)
        delay = self.crawl_delay
        if delay is not None and not (is_number(delay) and delay >= 0):
            raise RobotsRuleError(
                delay, field="crawl_delay", expected="a number of seconds from 0"
            )

    @property
    def user_agents(self) -> tuple[str, ...]:
        """Return the agents of the group, one or several."""
        return _as_tuple(self.user_agent)


__all__ = ["CHANGEFREQS", "RobotsRule", "SitemapEntry", "is_number"]
