"""The scripts of every page tree and the plan one render of a page follows.

Strategy, category and consent split a render into head tags and manifest entries.
"""

import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from django.http import HttpRequest

from next.caches import BoundedCache
from next.conf.signals import settings_reloaded
from next.consent import UNDECIDED, Consent, get_consent
from next.consent.manager import consent_configured, consent_payload, server_mode
from next.discovery import routed_page_trees
from next.pages.responses import vary_on_cookie
from next.static import StaticCollector, get_static_manager
from next.static.errors import StaticAssetNotFoundError
from next.static.runtime import CONSENT_PAYLOAD_KEY, SCRIPTS_PAYLOAD_KEY
from next.urls.manager import router_manager
from next.utils import template_edits_watched

from .discovery import ScriptsSource, load_scripts, source_stale
from .markers import HEAD_STRATEGIES, Script
from .registry import ScriptsRegistry, scripts_registry
from .render import head_tags, manifest_entry


logger = logging.getLogger(__name__)

SCRIPT_NOTE: Final = "next.scripts.script"
"""The collector note `{% script %}` leaves, the name of a script to render."""

CONSENT_NOTE: Final = "next.scripts.consent"
"""The collector note `{% #consented %}` leaves, so the runtime learns the consent."""

_NOTHING: Final[tuple[str, Mapping[str, object]]] = ("", {})


_NO_SCRIPTS: Final[tuple[Script, ...]] = ()


class ScriptsManager:
    """Discovers the `scripts.py` of every page tree and plans what a render adds.

    A watched process reads a source again once its file moves.
    """

    def __init__(self, registry: ScriptsRegistry | None = None) -> None:
        """Start with nothing discovered."""
        self._registry = registry if registry is not None else scripts_registry
        self._loaded = False
        self._lock = threading.RLock()
        self._trees: dict[Path, tuple[Script, ...]] = {}
        self._roots_of: BoundedCache[Path, Path | None] = BoundedCache()
        self._warned: set[tuple[Path, str]] = set()

    def _load(self) -> None:
        for tree, _skip_names in routed_page_trees(router_manager):
            root = tree.path.resolve()
            self._registry.register(root, load_scripts(root))
        self._loaded = True

    def _refresh(self) -> None:
        for root in self._registry.roots():
            self._refresh_root(root)

    def _refresh_root(self, root: Path) -> None:
        if source_stale(self._registry.source(root), root):
            self._registry.register(root, load_scripts(root))
            self._trees.pop(root, None)

    def sources(self) -> tuple[ScriptsSource, ...]:
        """Return every `scripts.py` the page trees hold, loading them when needed."""
        self._ensure_loaded()
        return self._registry.sources()

    def _ensure_loaded(self) -> None:
        watched = template_edits_watched()
        if self._loaded and not watched:
            return
        with self._lock:
            if self._loaded:
                self._refresh()
            else:
                self._load()

    def root_of(self, page_path: Path) -> Path | None:
        """Return the page tree `page_path` belongs to, the innermost one."""
        held = self._roots_of.get(page_path, page_path)
        if held is not page_path:
            return held
        resolved = page_path.resolve()
        roots = [
            root for root in self._registry.roots() if resolved.is_relative_to(root)
        ]
        root = max(roots, key=lambda path: len(path.parts)) if roots else None
        self._roots_of[page_path] = root
        return root

    def tree(self, page_path: Path | None) -> tuple[Script, ...]:
        """Return the scripts of the tree `page_path` belongs to.

        A watched process stats the `scripts.py` of that one tree, not of every tree.
        """
        if page_path is None:
            return _NO_SCRIPTS
        if not self._loaded:
            self._ensure_loaded()
        root = self.root_of(page_path)
        if root is None:
            return _NO_SCRIPTS
        if template_edits_watched():
            with self._lock:
                self._refresh_root(root)
        held = self._trees.get(root)
        if held is None:
            source = self._registry.source(root)
            held = _NO_SCRIPTS if source is None else tuple(source.scripts)
            self._trees[root] = held
        return held

    def reset(self, **kwargs) -> None:
        """Forget every tree, so the next render discovers them again."""
        with self._lock:
            self._registry.reset()
            self._trees.clear()
            self._roots_of.clear()
            self._warned.clear()
            self._loaded = False

    def _selected(
        self,
        tree: tuple[Script, ...],
        requested: Sequence[object],
        page_path: Path | None,
    ) -> list[Script]:
        names = {name for name in requested if isinstance(name, str)}
        known = {script.name for script in tree}
        for name in sorted(names - known):
            self._warn_unknown(page_path, name)
        return [script for script in tree if script.auto or script.name in names]

    def _warn_unknown(self, page_path: Path | None, name: str) -> None:
        key = (page_path or Path(), name)
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(
            "{%% script %r %%} names no script the scripts.py of %s declares",
            name,
            page_path,
        )

    def render(
        self,
        collector: StaticCollector,
        *,
        page_path: Path | None,
        request: HttpRequest | None,
        nonce: str | None,
    ) -> tuple[str, Mapping[str, object]]:
        """Return the head tags and the reserved payload entries of one render."""
        tree = self.tree(page_path)
        if not (tree or consent_configured() or collector.notes(CONSENT_NOTE)):
            return _NOTHING
        selected = self._selected(tree, collector.notes(SCRIPT_NOTE), page_path)
        server = server_mode(request)
        consent = get_consent(request) if server else UNDECIDED
        head, manifest = _split(selected, consent)
        if server and (consent.decided or any(script.gated for script in selected)):
            # `$consent` carries a decided visitor's choice whatever the page gates.
            vary_on_cookie(request)
        payload: dict[str, object] = {}
        if manifest:
            payload[SCRIPTS_PAYLOAD_KEY] = [
                manifest_entry(script, src)
                for script, src in _sourced(manifest, request)
            ]
        payload[CONSENT_PAYLOAD_KEY] = consent_payload(consent)
        tags = "\n".join(
            head_tags(script, src, nonce) for script, src in _sourced(head, request)
        )
        return tags, payload


def _split(
    selected: list[Script], consent: Consent
) -> tuple[list[Script], list[Script]]:
    """Split the selected scripts into head tags and manifest entries."""
    head: list[Script] = []
    manifest: list[Script] = []
    for script in selected:
        rendered = script.strategy in HEAD_STRATEGIES and consent.allows(
            script.category
        )
        (head if rendered else manifest).append(script)
    return head, manifest


def _sourced(
    scripts: list[Script], request: HttpRequest | None
) -> list[tuple[Script, str | None]]:
    """Pair each script with the public URL of its `src`, dropping what cannot load.

    A staticfiles name the storage cannot answer costs its script, not the page.
    """
    manager = get_static_manager()
    paired: list[tuple[Script, str | None]] = []
    for script in scripts:
        src: str | None = None
        if script.src is not None and urlsplit(script.src).netloc:
            # A vendor URL is the vendor's, so the project version stays off it.
            src = script.src
        elif script.src is not None:
            try:
                src = manager.asset_url(
                    manager.resolve_url(script.src), request=request
                )
            except StaticAssetNotFoundError:
                logger.warning("The script %r names a missing file", script.name)
                if script.init is None:
                    continue
        paired.append((script, src))
    return paired


scripts_manager = ScriptsManager()


def forget_scripts(**kwargs) -> None:
    """Drop every discovered `scripts.py`, so a reload takes effect."""
    scripts_manager.reset()


settings_reloaded.connect(forget_scripts)


__all__ = [
    "CONSENT_NOTE",
    "SCRIPT_NOTE",
    "ScriptsManager",
    "forget_scripts",
    "scripts_manager",
]
