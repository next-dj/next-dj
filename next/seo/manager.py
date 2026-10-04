"""The façade over the sitemap backends and the SEO sources of the page trees."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, override

from django.conf import settings
from django.urls import clear_url_caches
from django.utils.functional import Promise

from next.backends import BackendListManager, load_backends
from next.conf.defaults import DEFAULTS
from next.conf.scopes import scope_value
from next.conf.signals import settings_reloaded
from next.diagnostics import FailureLog
from next.site.config import site_closed_to_crawlers, site_config
from next.urls.manager import seo_routes_version
from next.utils import UNSET, Unset, template_edits_watched

from .backends import SitemapBackend, backend_path, shortest_cache
from .discovery import (
    SOURCE_NAMES,
    BrokenSource,
    forget_page_tree_roots,
    page_tree_roots,
)
from .registry import sitemap_items_registry
from .robots import RobotsSource, robots_candidates
from .signals import sitemap_backend_loaded


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from django.contrib.sitemaps import Sitemap
    from django.http import HttpRequest

    from next.pages.responses import CacheControl

    from .discovery import SeoRoot


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)

SEO_SCOPE: Final = "SEO"
SITEMAP_BACKENDS: Final = "SITEMAP_BACKENDS"
SEO_KEYS: Final = frozenset(DEFAULTS[SEO_SCOPE])
"""The keys a `NEXT_FRAMEWORK["SEO"]` mapping may carry."""

_DEFAULT_ENTRIES: Final[list[Mapping[str, Any]]] = DEFAULTS[SEO_SCOPE][SITEMAP_BACKENDS]
_DEFAULT_BACKEND: Final = str(_DEFAULT_ENTRIES[0]["BACKEND"])
_FINGERPRINT_WIDTH: Final = 12


def sitemap_backend_entries() -> list[Mapping[str, Any]]:
    """Return the `SITEMAP_BACKENDS` entries, the default list for a non-list value."""
    raw = scope_value(SEO_SCOPE, SITEMAP_BACKENDS)
    entries = raw if isinstance(raw, list) else _DEFAULT_ENTRIES
    return [entry for entry in entries if isinstance(entry, Mapping)]


def _first_served[P: tuple[Path, object]](
    kind: str, candidates: Sequence[P]
) -> P | None:
    """Return the first candidate that serves, warning about the rest under `DEBUG`.

    In production only the `next.E114` check reports the ignored sources.
    """
    served = next((pair for pair in candidates if pair[1] is not None), None)
    if served is not None and len(candidates) > 1 and settings.DEBUG:
        _failures.warn(
            (kind, served[0]),
            "%s has %d sources and %s serves it, the rest are ignored: %s",
            kind,
            len(candidates),
            served[0],
            ", ".join(str(pair[0]) for pair in candidates if pair is not served),
        )
    return served


def _source_bytes(roots: Sequence[SeoRoot]) -> bytes:
    """Return the bytes of every SEO source file, in router order."""
    chunks: list[bytes] = []
    for root in roots:
        for name in SOURCE_NAMES:
            try:
                chunks.append((root.path / name).read_bytes())
            except OSError:
                chunks.append(b"")
    return b"\0".join(chunks)


def _spelled_collection(value: object) -> str | None:
    """Return the stable text of a collection, `None` for any other value.

    A mapping or a set is sorted, and a list or a tuple keeps its order.
    """
    if isinstance(value, Mapping):
        pairs = (f"{stable_repr(key)}:{stable_repr(val)}" for key, val in value.items())
        return "{" + ",".join(sorted(pairs)) + "}"
    if isinstance(value, set | frozenset):
        return "{" + ",".join(sorted(map(stable_repr, value))) + "}"
    if isinstance(value, list | tuple):
        return "[" + ",".join(map(stable_repr, value)) + "]"
    return None


def stable_repr(value: object) -> str:
    """Return a representation of `value` that is identical in every process.

    It sorts collections, names callables by dotted path and evaluates lazy strings.
    """
    if isinstance(value, Promise):
        return repr(str(value))
    spelled = _spelled_collection(value)
    if spelled is not None:
        return spelled
    if is_dataclass(value) and not isinstance(value, type):
        named = {field.name: getattr(value, field.name) for field in fields(value)}
        return type(value).__qualname__ + stable_repr(named)
    if callable(value):
        owner = value if hasattr(value, "__qualname__") else type(value)
        return f"{owner.__module__}.{owner.__qualname__}"
    return repr(value)


def _serves(backend: SitemapBackend) -> bool:
    """Whether `backend` serves, an exception counting as serving once contained.

    It runs during URL resolution, outside any view, so an intended exception such as
    `Http404` is contained too and never fails the resolution of every other URL.
    """
    try:
        return bool(backend.serves())
    except Exception as exc:  # noqa: BLE001 - contained, the probe runs project code
        path = backend_path(backend)
        _failures.contain(
            exc,
            (path, "serves"),
            "%s.serves() raised, so /sitemap.xml stays mounted and answers 503 "
            "while its sections fail too. Make serves() return without raising.",
            path,
            pass_through=(),
        )
        return True


class SeoManager(BackendListManager[SitemapBackend]):
    """Load the sitemap backends and memoise the robots source."""

    def __init__(self) -> None:
        """Create the manager with no backend loaded and no source discovered."""
        super().__init__()
        self._robots: RobotsSource | Literal[Unset.UNSET] | None = UNSET
        self._fingerprint: str | None = None

    @property
    def version(self) -> int:
        """Return the SEO routes version, which every reset changes."""
        return seo_routes_version.value

    @override
    def reload(self) -> None:
        """Rebuild the backends from `SEO["SITEMAP_BACKENDS"]`."""
        self._backends = load_backends(
            sitemap_backend_entries(),
            base=SitemapBackend,
            default=_DEFAULT_BACKEND,
            signal=sitemap_backend_loaded,
        )
        self._mark_loaded()

    @property
    def backends(self) -> tuple[SitemapBackend, ...]:
        """Return the loaded backends in the order their sections merge."""
        self._ensure_backends()
        return tuple(self._backends)

    def reset(self, **kwargs) -> None:
        """Clear the backends and every discovered source, and change the version.

        The mounted routes depend on the sources, so the URL caches are cleared too.
        """
        with self._lock:
            seo_routes_version.move()
            self._backends = []
            self._loaded = False
            self._robots = UNSET
            self._fingerprint = None
            forget_page_tree_roots()
        clear_url_caches()

    def refresh(self) -> None:
        """Reset the sources under `DEBUG` once a source file changed on disk.

        Every entry point calls it first, so an edit applies without a process reload.
        """
        if template_edits_watched() and any(root.stale() for root in self.roots()):
            self.reset()

    def roots(self) -> tuple[SeoRoot, ...]:
        """Return every routed page tree with its SEO sources loaded."""
        return page_tree_roots()

    def serves_sitemap(self) -> bool:
        """Whether a backend has sections, which mounts `/sitemap.xml`.

        It runs during URL resolution. A backend that raises fails the request under
        `DEBUG` or `STRICT_LOADING`, and otherwise counts as serving, so its route
        answers 503 instead of failing every other route.
        """
        return any(_serves(backend) for backend in self.backends)

    def broken_sitemap(self) -> BrokenSource | None:
        """Return the first `sitemap.py` that failed to import, or `None`.

        One broken tree makes the whole sitemap answer 404, so no partial one is served.
        """
        return next(
            (
                BrokenSource(root.sitemap_path)
                for root in self.roots()
                if root.sitemap is not None and root.sitemap.module is None
            ),
            None,
        )

    def sections(self, request: HttpRequest | None) -> dict[str, Sitemap[Any]]:
        """Return the sections of every backend, keeping the first for a shared name.

        A site closed to crawlers lists none. A site that only `DEBUG` closes lists
        its sections for a preview under noindex.
        """
        if site_closed_to_crawlers(request):
            return {}
        merged: dict[str, Sitemap[Any]] = {}
        for backend in self.backends:
            try:
                sections = backend.sections(request)
            except Exception as exc:
                exc.add_note(f"Raised by {backend_path(backend)}.sections().")
                raise
            for name, section in sections.items():
                merged.setdefault(name, section)
        return merged

    def cache_control(self) -> CacheControl | None:
        """Return the shortest cache control a backend declares, or `None`.

        No-store takes precedence, since a shared copy would expose that backend.
        """
        return shortest_cache(backend.cache_control() for backend in self.backends)

    def robots_source(self) -> RobotsSource | None:
        """Return the `/robots.txt` source, warning once about the ignored ones.

        A source found while a reset happens is returned but not memoised.
        """
        held = self._robots
        if held is not UNSET:
            return held
        version = self.version
        served = _first_served("/robots.txt", robots_candidates(self.roots()))
        found = None if served is None else served[1]
        if self.version == version:
            self._robots = found
        return found

    def fingerprint(self) -> str:
        """Return a digest of the sources and the settings the SEO responses read.

        It prefixes the cache keys, so a response cached before an edit is never
        served. A digest taken while a reset happens is not memoised.
        """
        held = self._fingerprint
        if held is not None:
            return held
        version = self.version
        digest = hashlib.sha256(_source_bytes(self.roots()))
        digest.update(stable_repr(sitemap_backend_entries()).encode())
        digest.update(stable_repr(site_config()).encode())
        held = digest.hexdigest()[:_FINGERPRINT_WIDTH]
        if self.version == version:
            self._fingerprint = held
        return held


seo_manager = SeoManager()


def forget_seo_sources(**kwargs) -> None:
    """Reset the SEO manager, as the receiver of the settings and router reloads."""
    seo_manager.reset()


settings_reloaded.connect(forget_seo_sources)


def reset_seo_sources() -> None:
    """Clear the discovered SEO sources and every `@sitemap.items` registration."""
    seo_manager.reset()
    sitemap_items_registry.reset()


__all__ = [
    "SEO_KEYS",
    "SEO_SCOPE",
    "SITEMAP_BACKENDS",
    "SeoManager",
    "forget_seo_sources",
    "reset_seo_sources",
    "seo_manager",
    "sitemap_backend_entries",
    "stable_repr",
]
