"""Discover co-located CSS and JS files and the asset lists a module declares.

This module owns the filesystem side of the static pipeline. It walks layout
chains, reads `styles` and `scripts` module lists, and feeds a collector
through the active backend. Both entry points answer from an asset plan, which
remembers what the disk held rather than what the backend answered.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from next.pages import loaders as pages_loaders
from next.utils import (
    MAX_ANCESTOR_WALK_DEPTH,
    resolved_tree,
    stat_mtime_ns,
    store_bounded,
    template_edits_watched,
)

from .assets import StaticAsset, default_kinds
from .collector import default_placeholders
from .signals import asset_registered


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from next.components import ComponentInfo

    from .backends import StaticBackend
    from .collector import StaticCollector


logger = logging.getLogger(__name__)


_PAGE_PLAN_CACHE_MAX_SIZE = 2048
_COMPONENT_PLAN_CACHE_MAX_SIZE = 2048

# What identifies the component a plan was built for. The folder it reads comes
# from one of the two paths, and the logical name comes from the component name.
type _ComponentKey = tuple[Path | None, Path | None, str]


class _FoundAsset(NamedTuple):
    """One co-located file a role directory contributes to a render."""

    source_path: Path
    logical_name: str
    kind: str


class _AssetPlan(NamedTuple):
    """What one page or component contributes, and where it was read from.

    A warm render skips the stem probes and the module import, and still
    hands every found file to the backend. Module URLs are literals the
    backend never sees, so they ride the plan as finished assets. The
    registry generation rides along too, because which filenames count as
    assets is answered from registries no directory mtime moves with.
    """

    files: tuple[_FoundAsset, ...]
    module_assets: tuple[StaticAsset, ...]
    directory_mtimes: tuple[tuple[Path, int | None], ...]
    registries: tuple[int, int, int]


class _LayoutWalk(NamedTuple):
    """The directories holding a layout, and the mtimes of the watched ones."""

    layouts: tuple[Path, ...]
    mtimes: tuple[tuple[Path, int | None], ...]


def _url_suffix(url: str) -> str:
    """Return the lowercase dot-suffix of a URL or empty string when absent."""
    last_segment = url.rsplit("?", 1)[0].rsplit("#", 1)[0].rsplit("/", 1)[-1]
    dot = last_segment.rfind(".")
    if dot < 0:
        return ""
    return last_segment[dot:].lower()


def _rel_path_str(child: Path, root: Path) -> str | None:
    """Return the forward-slashed path of `child` relative to `root`.

    Both operands must already be resolved absolute paths. Returns an empty string when
    `child == root`, and `None` when `child` is not nested under `root`. Skips the
    per-segment work of `Path.relative_to().parts`.
    """
    child_str = os.fspath(child)
    root_str = os.fspath(root)
    if child_str == root_str:
        return ""
    prefix = root_str + os.sep
    if not child_str.startswith(prefix):  # pragma: no cover
        return None
    rel = child_str[len(prefix) :]
    if os.sep != "/":  # pragma: no cover
        rel = rel.replace(os.sep, "/")
    return rel


def _directory_mtimes(
    directories: Sequence[Path],
) -> tuple[tuple[Path, int | None], ...]:
    """Snapshot the mtime of every directory a plan is about to read.

    Taken whether or not the process watches asset edits, so a plan built with
    `DEBUG` off still has something to compare against once it comes on. A
    directory that does not stat is recorded as `None`, which no real mtime
    equals, so one that appears later rebuilds the plan that walked past it.
    """
    return tuple((directory, stat_mtime_ns(directory)) for directory in directories)


def _resolved_parent(path: Path) -> Path:
    """Return the resolved directory holding `path`, or its own spelling.

    A relative path resolves through the working directory, which an atomic
    deploy removes out from under a live worker, and a render is no place to
    raise over the name of a folder.
    """
    parent = path.parent
    try:
        return resolved_tree(parent)
    except OSError:
        return parent


@runtime_checkable
class BackendProvider(Protocol):
    """Contract consumed by the asset discovery layer.

    The static manager is the canonical implementation. Tests can pass
    any object exposing `default_backend` and `page_roots` without
    instantiating the full manager. Implementations must return
    resolved absolute paths from `page_roots`.
    """

    @property
    def default_backend(self) -> StaticBackend:
        """Return the primary backend used for file registration."""
        raise NotImplementedError

    def page_roots(self) -> tuple[Path, ...]:
        """Return the configured page-tree roots as resolved absolute paths."""
        raise NotImplementedError


class StemRegistry:
    """Map discovery role to registered filename stems.

    The built-in `template`, `layout`, and `component` roles each carry
    the stem of their own name. Users register extra stems during
    `AppConfig.ready` to teach discovery about further filenames.
    """

    def __init__(self) -> None:
        """Seed the registry with the built-in template, layout, and component roles."""
        self._roles: dict[str, list[str]] = {
            "template": ["template"],
            "layout": ["layout"],
            "component": ["component"],
        }
        self._version = 0

    @property
    def version(self) -> int:
        """Return a counter every registration bumps, so a cached answer can tell."""
        return self._version

    def register(self, role: str, stem: str) -> None:
        """Add a stem under the given role, creating the role when missing."""
        stems = self._roles.setdefault(role, [])
        if stem not in stems:
            stems.append(stem)
            self._version += 1

    def stems(self, role: str) -> tuple[str, ...]:
        """Return registered stems for the role in registration order."""
        return tuple(self._roles.get(role, ()))


default_stems: StemRegistry = StemRegistry()


class PathResolver:
    """Resolve page root and logical names for page, layout, and component paths.

    The resolver is shared between the asset discovery layer and the
    staticfiles finder so both layers produce identical logical names
    for the same on-disk location. The resolver assumes that the
    provider callable returns already resolved absolute page roots.
    """

    def __init__(self, page_roots_provider: Callable[[], tuple[Path, ...]]) -> None:
        """Store the page-roots provider callable consulted on every lookup."""
        self._provider = page_roots_provider
        self._find_page_root_cache: dict[Path, Path | None] = {}

    def page_roots(self) -> tuple[Path, ...]:
        """Return the current tuple of page tree roots from the provider."""
        return self._provider()

    def find_page_root(self, path: Path) -> Path | None:
        """Return the page tree root that contains the path, or None."""
        cached = self._find_page_root_cache.get(path)
        if cached is not None or path in self._find_page_root_cache:
            return cached
        resolved_parent = path.parent.resolve()
        for root in self.page_roots():
            if resolved_parent.is_relative_to(root):
                self._find_page_root_cache[path] = root
                return root
        self._find_page_root_cache[path] = None
        return None

    def logical_name_for_template(
        self, template_dir: Path, page_root: Path | None
    ) -> str:
        """Return the logical URL name for a page template directory.

        The caller is expected to pass a resolved `template_dir` and a
        resolved `page_root` from `find_page_root`.
        """
        if page_root is None:
            return self._fallback(template_dir)
        rel = _rel_path_str(template_dir, page_root)
        if rel is None:  # pragma: no cover
            return self._fallback(template_dir)
        return rel or "index"

    def logical_name_for_layout(self, layout_dir: Path, page_root: Path | None) -> str:
        """Return the logical URL name for a layout directory.

        The caller is expected to pass a resolved `layout_dir` and a
        resolved `page_root` from `find_page_root`.
        """
        if page_root is None:
            return f"{self._fallback(layout_dir)}/layout"
        rel = _rel_path_str(layout_dir, page_root)
        if rel is None:  # pragma: no cover
            return f"{self._fallback(layout_dir)}/layout"
        return f"{rel}/layout" if rel else "layout"

    @staticmethod
    def _fallback(directory: Path) -> str:
        return directory.name or "index"


class AssetDiscovery:
    """Detect co-located asset files and module-level asset list variables.

    The `provider` argument supplies the active backend and the page
    tree roots. The optional `resolver` argument is a path resolver.
    The default resolver is backed by the provider. The optional
    `stems` argument is a stem registry. The default is the
    process-wide `default_stems`.
    """

    def __init__(
        self,
        provider: BackendProvider,
        *,
        resolver: PathResolver | None = None,
        stems: StemRegistry | None = None,
    ) -> None:
        """Bind the provider and wire optional resolver and stems."""
        self._provider = provider
        self._resolver = resolver or PathResolver(provider.page_roots)
        self._stems = stems or default_stems
        self._page_plan_cache: OrderedDict[Path, _AssetPlan] = OrderedDict()
        self._component_plan_cache: OrderedDict[_ComponentKey, _AssetPlan] = (
            OrderedDict()
        )

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect layout, template, and module-level assets for a page file.

        Assets are added from the outermost layout inward, then from
        the template directory, then from `styles` and `scripts`
        module lists declared in `page.py`.
        """
        plan = self._page_plan_cache.get(file_path)
        if plan is None or self._plan_stale(plan):
            plan = self._build_page_asset_plan(file_path)
        store_bounded(self._page_plan_cache, file_path, plan, _PAGE_PLAN_CACHE_MAX_SIZE)
        self._apply_plan(plan, collector)

    def _registry_generation(self) -> tuple[int, int, int]:
        """Return the generation of every registry a plan reads while it is built.

        Read before a plan probes anything, so a registration landing while the
        probe runs leaves the plan stale rather than stamped as up to date. The
        stem registry is this instance's own, because a caller is free to hand
        `AssetDiscovery` one and the probe reads that one.
        """
        return (
            self._stems.version,
            default_kinds.version,
            default_placeholders.version,
        )

    def _apply_plan(self, plan: _AssetPlan, collector: StaticCollector) -> None:
        """Hand every file the plan found to the backend, then its module URLs."""
        for found in plan.files:
            self._register_file(found, collector)
        for asset in plan.module_assets:
            collector.add(asset)

    def _build_page_asset_plan(self, file_path: Path) -> _AssetPlan:
        """Probe every role directory behind `file_path` in one pass."""
        resolved = file_path.resolve()
        registries = self._registry_generation()
        page_root = self._resolver.find_page_root(resolved)
        # Imported before the walk stats anything, because the import writes
        # `__pycache__` into the very directory the walk is about to snapshot.
        lists = self._module_lists(resolved) if resolved.exists() else {}
        walk = self._walk_layouts(resolved, page_root)
        files: list[_FoundAsset] = []
        for layout_dir in walk.layouts:
            files += self._find_role_files(
                layout_dir,
                logical_name=self._resolver.logical_name_for_layout(
                    layout_dir, page_root
                ),
                role="layout",
            )
        template_dir = resolved.parent
        files += self._find_role_files(
            template_dir,
            logical_name=self._resolver.logical_name_for_template(
                template_dir, page_root
            ),
            role="template",
        )
        return _AssetPlan(
            files=tuple(files),
            module_assets=self._module_assets(lists),
            directory_mtimes=walk.mtimes,
            registries=registries,
        )

    def _plan_stale(self, plan: _AssetPlan) -> bool:
        """Whether anything the plan was read from has changed since.

        A registration moves no file, so the registry generation is compared
        whatever the process watches. Only a process watching template edits
        pays the stats, and there an asset created or deleted moves the mtime
        of the directory holding it, as does a directory that comes or goes.
        """
        if plan.registries != self._registry_generation():
            return True
        if not template_edits_watched():
            return False
        return any(
            stat_mtime_ns(directory) != mtime
            for directory, mtime in plan.directory_mtimes
        )

    def discover_component_assets(
        self, info: ComponentInfo, collector: StaticCollector
    ) -> None:
        """Collect co-located CSS, JS, and module asset lists for a component.

        Every instance of a component on a page arrives here, so the plan is
        what keeps the second instance from walking the folder again.
        """
        source = info.template_path or info.module_path
        if info.is_simple or source is None:
            # A simple component owns no folder, so it has nothing to plan and
            # an entry per instance would only crowd out the plans that do.
            return
        key: _ComponentKey = (info.template_path, info.module_path, info.name)
        plan = self._component_plan_cache.get(key)
        if plan is None or self._plan_stale(plan):
            plan = self._build_component_asset_plan(info, _resolved_parent(source))
        store_bounded(
            self._component_plan_cache, key, plan, _COMPONENT_PLAN_CACHE_MAX_SIZE
        )
        self._apply_plan(plan, collector)

    def _build_component_asset_plan(
        self, info: ComponentInfo, component_dir: Path
    ) -> _AssetPlan:
        """Probe the component folder once and note what it was read from."""
        module_path = info.module_path
        registries = self._registry_generation()
        # Imported before the mtimes are taken, because the import writes
        # `__pycache__` into the folder the snapshot is about to read.
        lists = (
            self._module_lists(module_path)
            if module_path is not None and module_path.exists()
            else {}
        )
        directories = [component_dir]
        module_dir = None if module_path is None else _resolved_parent(module_path)
        if module_dir is not None and module_dir != component_dir:
            directories.append(module_dir)
        # Stat before the probe, so a file landing between the two reads leaves
        # the plan stale rather than invisible until a restart.
        mtimes = _directory_mtimes(directories)
        files = tuple(
            self._find_role_files(
                component_dir, logical_name=f"components/{info.name}", role="component"
            )
        )
        return _AssetPlan(
            files=files,
            module_assets=self._module_assets(lists),
            directory_mtimes=mtimes,
            registries=registries,
        )

    def _find_role_files(
        self, directory: Path, *, logical_name: str, role: str
    ) -> list[_FoundAsset]:
        """Return the `{stem}{ext}` files that exist in `directory` for the role.

        The set of extensions probed comes from `KindRegistry.kinds()`, so registering a
        new kind during `AppConfig.ready` is enough to teach discovery about it. Each
        hit carries its resolved path, because the staticfiles finder spells it that way
        and the two layers have to agree on which file a logical name means.
        """
        found: list[_FoundAsset] = []
        for stem in self._stems.stems(role):
            for kind in default_kinds.kinds():
                suffix = default_kinds.extension(kind)
                candidate = directory / f"{stem}{suffix}"
                if candidate.exists():
                    found.append(_FoundAsset(candidate.resolve(), logical_name, kind))
        return found

    def _module_lists(self, module_path: Path) -> dict[str, list[str]]:
        """Read the URL list variable named after every registered slot.

        Read while the plan is built rather than while it is applied, because
        importing the module writes `__pycache__` beside it and that would
        move a directory mtime the plan had already taken.
        """
        lists = pages_loaders.read_module_string_lists(
            module_path, [slot.name for slot in default_placeholders]
        )
        return lists if lists is not None else {}

    def _module_assets(self, lists: dict[str, list[str]]) -> tuple[StaticAsset, ...]:
        """Turn every URL the module lists name into an asset.

        Built with the plan rather than on every render, because these URLs
        are literals the backend is never asked about.
        """
        assets: list[StaticAsset] = []
        for slot_name, urls in lists.items():
            for url in urls:
                asset = self._module_asset(url, slot_name)
                if asset is not None:
                    assets.append(asset)
        return tuple(assets)

    def _module_asset(self, url: str, slot_name: str) -> StaticAsset | None:
        """Resolve a module-level URL to an asset of the kind its suffix names.

        The kind comes from `KindRegistry.kind_for_extension(suffix)`
        where suffix is the lowercase trailing dot-extension of the
        URL. URLs whose extension is not registered, or whose resolved
        kind belongs to a different slot, are dropped with a debug log.
        """
        suffix = _url_suffix(url)
        if not suffix:
            logger.debug("Module URL %r has no recognised extension", url)
            return None
        kind = default_kinds.kind_for_extension(suffix)
        if kind is None:
            logger.debug("Module URL %r has unregistered extension %r", url, suffix)
            return None
        kind_slot = default_kinds.slot(kind)
        if kind_slot != slot_name:
            logger.debug(
                "Module URL %r maps to kind %r in slot %r, so the %r list dropped it",
                url,
                kind,
                kind_slot,
                slot_name,
            )
            return None
        return StaticAsset(url=url, kind=kind)

    def _register_file(self, found: _FoundAsset, collector: StaticCollector) -> None:
        """Register a file with the backend and add the result to the collector.

        The signal follows the collector, so a component mounted many times on
        one page announces each of its assets once. Warnings are logged for
        `OSError` and `ValueError` and drop that one asset. All other exception
        types propagate so bugs in custom backends surface loudly.
        """
        backend = self._provider.default_backend
        try:
            url = backend.register_file(
                found.source_path, found.logical_name, found.kind
            )
        except (OSError, ValueError) as e:
            logger.warning(
                "Failed to register static asset %s as %r: %s",
                found.source_path,
                found.logical_name,
                e,
                extra={"source_path": str(found.source_path), "kind": found.kind},
            )
            return
        asset = StaticAsset(url=url, kind=found.kind, source_path=found.source_path)
        if collector.add(asset):
            asset_registered.send(sender=asset, collector=collector, backend=backend)

    def _walk_layouts(self, file_path: Path, page_root: Path | None) -> _LayoutWalk:
        """Walk up from the page directory, outermost first, and stat as it goes.

        Inside a page tree every directory is watched, because a layout dropped
        into an empty one has to invalidate the plan that walked past it, and
        the tree root bounds what that costs. Outside one there is no boundary
        between project and system directories, so the walk watches what it read.
        """
        layouts: list[Path] = []
        watched: list[tuple[Path, int | None]] = []
        inside_a_tree = page_root is not None
        current_dir = file_path.parent
        for depth in range(MAX_ANCESTOR_WALK_DEPTH):
            # Stat first so a file landing between the two reads leaves the
            # plan stale rather than invisible until a restart.
            mtime = stat_mtime_ns(current_dir)
            holds_layout = (current_dir / "layout.djx").exists()
            if holds_layout:
                layouts.append(current_dir)
            if inside_a_tree or holds_layout or depth == 0:
                watched.append((current_dir, mtime))
            parent = current_dir.parent
            # The tree root ends the walk, and so does the filesystem root.
            if current_dir in (page_root, parent):
                break
            current_dir = parent
        return _LayoutWalk(tuple(reversed(layouts)), tuple(reversed(watched)))
