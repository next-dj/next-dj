"""Value objects a `sitemap.py` and a `robots.py` declare their entries with."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Entry:
    """One URL an `@sitemap.items` callable yields for its trail."""

    kwargs: Mapping[str, object] = field(default_factory=dict)
    lastmod: datetime | date | None = None
    changefreq: str | None = None
    priority: float | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    """One `User-agent` group of a `robots.py`, sequences read as tuples."""

    user_agent: str | Sequence[str] = "*"
    allow: Sequence[str] = ()
    disallow: Sequence[str] = ()
    crawl_delay: int | None = None

    def __post_init__(self) -> None:
        """Pin the sequences as tuples so a shared list cannot move under a group."""
        if not isinstance(self.user_agent, str):
            object.__setattr__(self, "user_agent", tuple(self.user_agent))
        object.__setattr__(self, "allow", tuple(self.allow))
        object.__setattr__(self, "disallow", tuple(self.disallow))

    @property
    def user_agents(self) -> tuple[str, ...]:
        """Return the agents of the group, one or several."""
        agent = self.user_agent
        return (agent,) if isinstance(agent, str) else tuple(agent)


__all__ = ["Entry", "Rule"]
