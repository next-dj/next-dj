"""The two `/robots.txt` sources, a declared `robots.py` and a static `robots.txt`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.utils import stat_mtime_ns

from .markers import Rule


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable
    from pathlib import Path

    from .discovery import SeoRoot


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


@dataclass(frozen=True, slots=True)
class RobotsRules:
    """The `rules` and `host` a `robots.py` declares."""

    path: Path
    rules: tuple[Rule, ...]
    host: str | None

    def render(self, sitemap_url: str | None) -> str:
        """Render the groups, then `Host` and `sitemap_url` as a trailing block."""
        blocks = [_group(rule) for rule in self.rules or DEFAULT_RULES]
        trailer = []
        if self.host is not None:
            trailer.append(f"Host: {self.host}")
        if sitemap_url is not None:
            trailer.append(f"Sitemap: {sitemap_url}")
        if trailer:
            blocks.append("\n".join(trailer))
        return "\n\n".join(blocks) + "\n"


def declared_rules(module: types.ModuleType) -> tuple[Rule, ...]:
    """Return the `Rule` groups a `robots.py` declares, anything else read as none."""
    declared = getattr(module, "rules", ())
    if not isinstance(declared, list | tuple):
        return ()
    return tuple(rule for rule in declared if isinstance(rule, Rule))


def rules_from_module(path: Path, module: types.ModuleType) -> RobotsRules:
    """Read `rules` and `host` leniently, the checks report the wrong shapes."""
    host = getattr(module, "host", None)
    return RobotsRules(
        path=path,
        rules=declared_rules(module),
        host=host if isinstance(host, str) else None,
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


def robots_candidates(
    roots: Iterable[SeoRoot],
) -> tuple[tuple[Path, RobotsSource | None], ...]:
    """Pair every `/robots.txt` source with what it serves, in the order preferred.

    A `robots.py` precedes its `robots.txt` and still counts when its import failed.
    """
    candidates: list[tuple[Path, RobotsSource | None]] = []
    for root in roots:
        if root.robots is not None:
            module = root.robots.module
            path = root.robots.path
            served = None if module is None else rules_from_module(path, module)
            candidates.append((path, served))
        if root.robots_file is not None:
            candidates.append((root.robots_file, RobotsFile(root.robots_file)))
    return tuple(candidates)


__all__ = [
    "DEFAULT_RULES",
    "RobotsFile",
    "RobotsRules",
    "RobotsSource",
    "declared_rules",
    "robots_candidates",
    "rules_from_module",
]
