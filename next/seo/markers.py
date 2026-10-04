"""Value objects that a `sitemap.py` and a `robots.py` declare their entries with.

Both validate on construction, so an invalid value is never rendered.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Final, TypeGuard, cast

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
        """Freeze the kwargs and reject values the sitemap protocol does not allow."""
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
    """Return `value` as a tuple of strings, treating a bare string as one item."""
    return (value,) if isinstance(value, str) else tuple(value)


def _check_path(name: str, path: object) -> None:
    """Reject a path that is not a string or breaks the robots.txt path grammar."""
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
    """One `User-agent` group of a `robots.py`.

    Each string field accepts a string or a sequence of strings and stores a tuple.
    """

    user_agent: str | Sequence[str] = "*"
    allow: str | Sequence[str] = ()
    disallow: str | Sequence[str] = ()
    crawl_delay: float | None = None

    def __post_init__(self) -> None:
        """Store every field as a tuple and reject values that would break the group."""
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
        """Return the user agents of the group as the tuple stored at construction."""
        return cast("tuple[str, ...]", self.user_agent)


__all__ = ["CHANGEFREQS", "RobotsRule", "SitemapEntry", "is_number"]
