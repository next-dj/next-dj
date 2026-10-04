"""The scripts of every page tree and the plan one render of a page follows.

Strategy, category and consent split a render into head tags and manifest entries.
"""

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from django.http import HttpRequest

from next.caches import BoundedCache
from next.consent import UNDECIDED, Consent, get_consent, joint_category
from next.consent.manager import consent_configured, consent_payload, server_mode
from next.diagnostics import FailureLog
from next.discovery import routed_page_trees
from next.pages.responses import vary_on_cookie
from next.static import StaticAsset, StaticCollector, default_kinds, get_static_manager
from next.static.errors import StaticAssetNotFoundError, StaticAssetTraversalError
from next.static.runtime import CONSENT_PAYLOAD_KEY, SCRIPTS_PAYLOAD_KEY
from next.urls.manager import router_manager
from next.utils import template_edits_watched

from .discovery import ScriptsSource, load_scripts, source_stale
from .markers import HEAD_STRATEGIES, Script
from .registry import ScriptsRegistry
from .render import asset_entry, head_tags, manifest_entry


logger = logging.getLogger(__name__)

SCRIPT_NOTE: Final = "next.scripts.script"
"""The collector note `{% script %}` adds, the name of a script to render."""

CONSENT_NOTE: Final = "next.scripts.consent"
"""The collector note `{% #consented %}` adds, so the render sends `$consent`."""

GATED_NOTE: Final = "next.scripts.gated"
"""The collector note for a script a client-rendered `{% #consented %}` holds back."""

_NOTHING: Final[tuple[str, Mapping[str, object]]] = ("", {})

_NO_SCRIPTS: Final[tuple[Script, ...]] = ()

_MISSING: Final = object()
"""Marks a lookup that found nothing, apart from a held `None`."""

_ASSET_ERRORS: Final = (StaticAssetNotFoundError, StaticAssetTraversalError)


@dataclass(frozen=True, slots=True)
class GatedNote:
    """A script a client-rendered `{% #consented %}` body asked for, held back.

    `target` is the name of a declared script or an asset the body registered, and
    the runtime loads it once the visitor grants `category`.
    """

    category: str
    target: str | StaticAsset


class ScriptsManager:
    """Discovers the `scripts.py` of every page tree and plans what a render adds.

    A process that watches template edits reads a source again when its file changes.
    """

    def __init__(self, registry: ScriptsRegistry | None = None) -> None:
        """Start with nothing discovered, in a registry of its own unless given one."""
        self._registry = registry if registry is not None else ScriptsRegistry()
        self._loaded = False
        self._lock = threading.RLock()
        self._trees: dict[Path, tuple[Script, ...]] = {}
        self._roots_of: BoundedCache[Path, object] = BoundedCache()
        self._kept: dict[Path, ScriptsSource | None] = {}
        self._failures = FailureLog(logger)

    def _load(self) -> None:
        # A source whose file is unchanged since the reset is reused, so a reload
        # executes only the `scripts.py` files that changed.
        for tree, _skip_names in routed_page_trees(router_manager):
            root = tree.path.resolve()
            if root in self._kept and not source_stale(self._kept[root], root):
                self._registry.register(root, self._kept[root])
            else:
                self._registry.register(root, load_scripts(root))
        self._kept.clear()
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

    def _root_of(self, page_path: Path) -> Path | None:
        """Return the page tree `page_path` belongs to, the innermost one."""
        held = self._roots_of.get(page_path, _MISSING)
        if held is not _MISSING:
            return held  # type: ignore[return-value]
        resolved = page_path.resolve()
        roots = [
            root for root in self._registry.roots() if resolved.is_relative_to(root)
        ]
        root = max(roots, key=lambda path: len(path.parts)) if roots else None
        self._roots_of[page_path] = root
        return root

    def tree(self, page_path: Path | None) -> tuple[Script, ...]:
        """Return the scripts of the tree `page_path` belongs to.

        A process that watches edits checks the `scripts.py` of that one tree only.
        """
        if page_path is None:
            return _NO_SCRIPTS
        if not self._loaded:
            self._ensure_loaded()
        root = self._root_of(page_path)
        if root is None:
            return _NO_SCRIPTS
        if template_edits_watched():
            with self._lock:
                self._refresh_root(root)
        held = self._trees.get(root)
        if held is None:
            source = self._registry.source(root)
            held = _NO_SCRIPTS if source is None else source.scripts
            self._trees[root] = held
        return held

    def reset(self, **kwargs) -> None:
        """Forget every tree, so the next render discovers them again.

        The sources are kept, and one whose file is unchanged is not executed again.
        """
        with self._lock:
            self._kept.update(
                (root, self._registry.source(root)) for root in self._registry.roots()
            )
            self._registry.reset()
            self._trees.clear()
            self._roots_of.clear()
            self._failures.clear()
            self._loaded = False

    def _selected(
        self,
        tree: tuple[Script, ...],
        collector: StaticCollector,
        page_path: Path | None,
    ) -> list[Script]:
        """Return the scripts this render runs, a held-back one waiting for its block.

        A script that is `auto`, or that the page names outside a gated block, is not
        held back. A held-back script waits for its own category and the category of
        the block, so a `marketing` script named in an `analytics` block needs both.
        """
        names = {name for name in collector.notes(SCRIPT_NOTE) if isinstance(name, str)}
        held: dict[str, str] = {}
        for note in _gated(collector):
            if isinstance(note.target, str):
                held.setdefault(note.target, note.category)
        known = {script.name for script in tree}
        for name in sorted((names | held.keys()) - known):
            self._failures.warn(
                (page_path, name),
                "{%% script %r %%} names no script the scripts.py of %s declares",
                name,
                page_path,
            )
        return [
            script
            if script.auto or script.name in names
            else replace(
                script, category=joint_category(script.category, held[script.name])
            )
            for script in tree
            if script.auto or script.name in names or script.name in held
        ]

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
        selected = self._selected(tree, collector, page_path)
        server = server_mode(request)
        consent = get_consent(request) if server else UNDECIDED
        head, manifest = _split(selected, consent)
        if server and (consent.decided or any(script.gated for script in selected)):
            # `$consent` carries the choice of a decided visitor whatever is gated.
            vary_on_cookie(request)
        payload: dict[str, object] = {}
        entries = [
            manifest_entry(script, src)
            for script, src in self._sourced(manifest, request)
        ] + _held_assets(collector, request)
        if entries:
            payload[SCRIPTS_PAYLOAD_KEY] = entries
        payload[CONSENT_PAYLOAD_KEY] = consent_payload(consent)
        tags = "\n".join(
            head_tags(script, src, nonce)
            for script, src in self._sourced(head, request)
        )
        return tags, payload

    def _sourced(
        self, scripts: list[Script], request: HttpRequest | None
    ) -> list[tuple[Script, str | None]]:
        """Pair each script with the public URL of its `src`, dropping what cannot load.

        A staticfiles name the storage cannot resolve drops its script, not the page.
        """
        manager = get_static_manager()
        paired: list[tuple[Script, str | None]] = []
        for script in scripts:
            src: str | None = None
            if script.src is not None and urlsplit(script.src).netloc:
                # A third-party URL is used as written, without the static version.
                src = script.src
            elif script.src is not None:
                try:
                    src = manager.asset_url(
                        manager.resolve_url(script.src), request=request
                    )
                except _ASSET_ERRORS:
                    self._failures.warn(
                        ("missing", script.name, script.src),
                        "The script %r names a missing file, %r, so only its init "
                        "runs. Point src at a staticfiles name a finder resolves.",
                        script.name,
                        script.src,
                    )
                    if script.init is None:
                        continue
            paired.append((script, src))
        return paired


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


def _gated(collector: StaticCollector) -> list[GatedNote]:
    """Return what the gated blocks of a render held back, in the order they did."""
    return [note for note in collector.notes(GATED_NOTE) if isinstance(note, GatedNote)]


def _held_assets(
    collector: StaticCollector, request: HttpRequest | None
) -> list[dict[str, object]]:
    """Return the manifest entries of the assets the gated blocks held back.

    An asset the page also registers outside the block loads with it, so it is skipped.
    """
    manager = get_static_manager()
    entries: dict[str, dict[str, object]] = {}
    for note in _gated(collector):
        asset = note.target
        if not isinstance(asset, StaticAsset):
            continue
        if asset in collector.assets_in_slot(default_kinds.slot(asset.kind)):
            continue
        src = (
            None
            if asset.inline is not None
            else manager.asset_url(asset.url, request=request)
        )
        entry = asset_entry(asset, note.category, src)
        entries.setdefault(str(entry["name"]), entry)
    return list(entries.values())


scripts_manager = ScriptsManager()


def forget_scripts(**kwargs) -> None:
    """Drop every discovered `scripts.py`, so a reload takes effect."""
    scripts_manager.reset()


__all__ = [
    "CONSENT_NOTE",
    "GATED_NOTE",
    "SCRIPT_NOTE",
    "GatedNote",
    "ScriptsManager",
    "forget_scripts",
    "scripts_manager",
]
