"""The `/robots.txt` sources, a `robots.py` and a static text file."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from next.deps.resolver import current_resolver
from next.introspect import describe_callable
from next.utils import WEB_SCHEMES

from .discovery import BrokenSource, declared_cache
from .markers import RobotsRule


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from django.http import HttpRequest

    from next.pages.responses import CacheControl

    from .discovery import SeoRoot


logger = logging.getLogger(__name__)

CLOSED_ROBOTS_TXT: Final = "User-agent: *\nDisallow:\n"
"""The `/robots.txt` body of a site closed to search, so crawlers can read noindex."""

_BREAKS: Final = re.compile(r"[\r\n]")


def render_group(rule: RobotsRule) -> str:
    """Render one `User-agent` group, adding `Disallow:` to a group without rules.

    Under RFC 9309 a group of agent lines alone merges into the next group.
    """
    lines = [f"User-agent: {agent}" for agent in rule.user_agents]
    lines.extend(f"Allow: {path}" for path in rule.allow)
    lines.extend(f"Disallow: {path}" for path in rule.disallow)
    if not rule.allow and not rule.disallow:
        lines.append("Disallow:")
    if rule.crawl_delay is not None:
        lines.append(f"Crawl-delay: {rule.crawl_delay}")
    return "\n".join(lines)


def render_robots(rules: Sequence[RobotsRule], sitemaps: Sequence[str]) -> str:
    """Render the groups, or one allowing every crawler, then the sitemap lines."""
    blocks = [render_group(rule) for rule in rules or (RobotsRule(),)]
    if sitemaps:
        blocks.append("\n".join(f"Sitemap: {url}" for url in sitemaps))
    return "\n\n".join(blocks) + "\n"


def rules_of(value: object) -> tuple[RobotsRule, ...]:
    """Return the `RobotsRule` items of a declared list or tuple, ignoring the rest."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(rule for rule in value if isinstance(rule, RobotsRule))


def declared_rules(module: types.ModuleType) -> tuple[RobotsRule, ...]:
    """Return the groups a `robots.py` lists, empty when `rules` is a callable."""
    return rules_of(getattr(module, "rules", ()))


def is_sitemap_url(value: object) -> bool:
    """Whether a declared `Sitemap:` line is an absolute http or https URL."""
    if not isinstance(value, str) or _BREAKS.search(value) is not None:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in WEB_SCHEMES and bool(parts.netloc)


@dataclass(frozen=True, slots=True)
class DeclaredRobots:
    """A `robots.py` whose `rules` is a list or a callable resolved per request."""

    path: Path
    module: types.ModuleType

    def rules(self, request: HttpRequest | None) -> tuple[RobotsRule, ...]:
        """Return the groups, calling `rules` through the resolver when it is one."""
        declared = getattr(self.module, "rules", ())
        if callable(declared):
            try:
                resolved = current_resolver().resolve_dependencies(
                    declared, request=request
                )
                declared = declared(**resolved)
            except Exception as exc:
                exc.add_note(
                    f"Raised by the rules callable {describe_callable(declared)} "
                    f"of {self.path}."
                )
                raise
        return rules_of(declared)

    @property
    def sitemaps(self) -> tuple[str, ...]:
        """Return the extra sitemap URLs the module lists for the robots.txt."""
        declared = getattr(self.module, "sitemaps", ())
        if not isinstance(declared, list | tuple):
            return ()
        return tuple(url for url in declared if is_sitemap_url(url))

    @property
    def cache(self) -> CacheControl | None:
        """Return the cache control the module declares for the response."""
        return declared_cache(self.module)

    def render(self, request: HttpRequest | None, sitemap_url: str | None) -> str:
        """Render the groups, then the framework sitemap ahead of the declared ones."""
        own = () if sitemap_url is None else (sitemap_url,)
        return render_robots(self.rules(request), (*own, *self.sitemaps))


class TextFile:
    """A static text file served unchanged and read again when its mtime changes."""

    __slots__ = ("_held", "path")

    def __init__(self, path: Path) -> None:
        """Store the path without reading the file."""
        self.path = path
        self._held: tuple[int, bytes] | None = None

    def read(self) -> bytes | None:
        """Return the bytes of the file, `None` once it no longer exists.

        A failed read returns the last content read and raises only when there is none.
        """
        try:
            return self._read()
        except FileNotFoundError:
            self._held = None
            return None
        except OSError:
            held = self._held
            if held is None:
                raise
            logger.exception(
                "%s failed to read, so the last copy read is served", self.path
            )
            return held[1]

    def _read(self) -> bytes:
        mtime_ns = self.path.stat().st_mtime_ns
        held = self._held
        if held is not None and held[0] == mtime_ns:
            return held[1]
        content = self.path.read_bytes()
        self._held = (mtime_ns, content)
        return content


type RobotsSource = DeclaredRobots | TextFile | BrokenSource


def robots_candidates(
    roots: Iterable[SeoRoot],
) -> tuple[tuple[Path, RobotsSource], ...]:
    """Return every `/robots.txt` source with what it serves, in order of precedence.

    A `robots.py` precedes the `robots.txt` beside it and keeps the route even when
    its import failed.
    """
    candidates: list[tuple[Path, RobotsSource]] = []
    for root in roots:
        if root.robots is not None:
            module = root.robots.module
            path = root.robots.path
            served = (
                BrokenSource(path) if module is None else DeclaredRobots(path, module)
            )
            candidates.append((path, served))
        if root.robots_file is not None:
            candidates.append((root.robots_file, TextFile(root.robots_file)))
    return tuple(candidates)


def rule_pattern(path: str) -> re.Pattern[str]:
    """Compile a robots path, `*` matching anything and a final `$` anchoring it."""
    anchored = path.endswith("$")
    body = ".*".join(re.escape(part) for part in path.removesuffix("$").split("*"))
    return re.compile(body + ("$" if anchored else ""))


__all__ = [
    "CLOSED_ROBOTS_TXT",
    "DeclaredRobots",
    "RobotsSource",
    "TextFile",
    "declared_rules",
    "is_sitemap_url",
    "render_group",
    "render_robots",
    "robots_candidates",
    "rule_pattern",
    "rules_of",
]
