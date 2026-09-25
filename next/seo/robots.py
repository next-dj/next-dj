"""The two `/robots.txt` sources, a declared `robots.py` and a static `robots.txt`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.utils import stat_mtime_ns

from .markers import Rule


if TYPE_CHECKING:
    import types
    from pathlib import Path


DEFAULT_RULES: tuple[Rule, ...] = (Rule(allow=("/",)),)
"""What an empty `robots.py` declares, every crawler allowed everywhere."""


def _group(rule: Rule) -> str:
    """Render one `User-agent` group."""
    lines = [f"User-agent: {agent}" for agent in rule.user_agents]
    lines.extend(f"Allow: {path}" for path in rule.allow)
    lines.extend(f"Disallow: {path}" for path in rule.disallow)
    if rule.crawl_delay is not None:
        lines.append(f"Crawl-delay: {rule.crawl_delay}")
    return "\n".join(lines)


def render_rules(
    rules: tuple[Rule, ...], host: str | None, sitemap_url: str | None
) -> str:
    """Render the groups, then the `Host` and `Sitemap` lines as a trailing block."""
    blocks = [_group(rule) for rule in rules or DEFAULT_RULES]
    trailer = []
    if host is not None:
        trailer.append(f"Host: {host}")
    if sitemap_url is not None:
        trailer.append(f"Sitemap: {sitemap_url}")
    if trailer:
        blocks.append("\n".join(trailer))
    return "\n\n".join(blocks) + "\n"


@dataclass(frozen=True, slots=True)
class RobotsRules:
    """The `rules` and `host` a `robots.py` declares."""

    path: Path
    rules: tuple[Rule, ...]
    host: str | None

    def render(self, sitemap_url: str | None) -> str:
        """Return the robots text with `sitemap_url` as its `Sitemap` line."""
        return render_rules(self.rules, self.host, sitemap_url)


def rules_from_module(path: Path, module: types.ModuleType) -> RobotsRules:
    """Read `rules` and `host` leniently, the checks report the wrong shapes."""
    declared = getattr(module, "rules", ())
    rules = tuple(
        rule
        for rule in (declared if isinstance(declared, list | tuple) else ())
        if isinstance(rule, Rule)
    )
    host = getattr(module, "host", None)
    return RobotsRules(
        path=path, rules=rules, host=host if isinstance(host, str) else None
    )


class RobotsFile:
    """A static `robots.txt`, served byte for byte and re-read when its mtime moves."""

    __slots__ = ("_held", "path")

    def __init__(self, path: Path) -> None:
        """Hold nothing until the first read."""
        self.path = path
        self._held: tuple[int, bytes] | None = None

    def read(self) -> bytes | None:
        """Return the file bytes, from the memo while the mtime stands.

        `None` means the file is gone since discovery, which the view answers with 404.
        """
        mtime_ns = stat_mtime_ns(self.path)
        if mtime_ns is None:
            self._held = None
            return None
        held = self._held
        if held is not None and held[0] == mtime_ns:
            return held[1]
        content = self.path.read_bytes()
        self._held = (mtime_ns, content)
        return content


type RobotsSource = RobotsRules | RobotsFile


__all__ = [
    "DEFAULT_RULES",
    "RobotsFile",
    "RobotsRules",
    "RobotsSource",
    "render_rules",
    "rules_from_module",
]
