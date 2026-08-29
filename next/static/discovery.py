"""Discover co-located CSS/JS files and page and component module asset lists.

This module owns the filesystem side of the static pipeline. It walks
layout chains, reads `styles` and `scripts` module lists, and pushes
results onto a collector via the active backend.

The path-to-logical-name conversion lives on the `PathResolver` so both
discovery and the staticfiles finder share the exact same mapping. The
`StemRegistry` controls which filenames are auto-picked-up per role. It
lets users teach the framework about new stems like `page.css` or
`panel.js` without patching the core.

The `BackendProvider` protocol inverts the dependency direction. The
discovery layer does not import the static manager directly. Any object
exposing `default_backend` and `page_roots` satisfies the protocol,
which makes unit-testing without a full manager trivial.

The provider contract requires that `page_roots` returns already
resolved absolute paths. Both the static manager and the co-located
finder satisfy this contract, which lets the discovery layer skip a
round of resolution on every logical-name lookup.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from django.conf import settings

from next.pages import loaders as pages_loaders

from .assets import StaticAsset, default_kinds
from .collector import default_placeholders
from .signals import asset_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.components import ComponentInfo

    from .backends import StaticBackend
    from .collector import StaticCollector


logger = logging.getLogger(__name__)


_MODULE_LIST_CACHE_MAX_SIZE = 2048
_LAYOUT_DIR_CACHE_MAX_SIZE = 2048
_PAGE_PLAN_CACHE_MAX_SIZE = 2048


class _FoundAsset(NamedTuple):
    """One co-located file a role directory contributes to a render."""

    source_path: Path
    logical_name: str
    kind: str


class _PageAssetPlan(NamedTuple):
    """What one page path contributes, and the directories it was read from.

    A warm render registers the files named here instead of probing every
    stem again. `module_path` is the page itself, or `None` where it is gone.
    """

    files: tuple[_FoundAsset, ...]
    module_path: Path | None
    directory_mtimes: tuple[tuple[Path, float], ...]


def _url_suffix(url: str) -> str:
    """Return the lowercase dot-suffix of a URL or empty string when absent."""
    last_segment = url.rsplit("?", 1)[0].rsplit("#", 1)[0].rsplit("/", 1)[-1]
    dot = last_segment.rfind(".")
    if dot < 0:
        return ""
    return last_segment[dot:].lower()


def _rel_path_str(child: Path, root: Path) -> str | None:
    """Return the forward-slashed path of `child` relative to `root`.

    Both operands must already be resolved absolute paths. Returns an
    empty string when `child == root`, and `None` when `child` is not
    nested under `root`. Skips the per-segment work of
    `Path.relative_to().parts`.
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


def _directory_mtimes(directories: list[Path]) -> tuple[tuple[Path, float], ...]:
    """Snapshot the mtime of each directory, dropping the ones that do not stat.

    Taken whether or not the process watches asset edits, so a plan built
    with `DEBUG` off still has something to compare against once it comes on.
    """
    snapshot: list[tuple[Path, float]] = []
    for directory in directories:
        try:
            snapshot.append((directory, directory.stat().st_mtime))
        except OSError:
            continue
    return tuple(snapshot)


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

    def register(self, role: str, stem: str) -> None:
        """Add a stem under the given role, creating the role when missing."""
        stems = self._roles.setdefault(role, [])
        if stem not in stems:
            stems.append(stem)

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
        self._module_list_cache: OrderedDict[Path, dict[str, list[str]]] = OrderedDict()
        self._layout_dir_cache: OrderedDict[Path, list[Path]] = OrderedDict()
        self._page_plan_cache: OrderedDict[Path, _PageAssetPlan] = OrderedDict()

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect layout, template, and module-level assets for a page file.

        Assets are added from the outermost layout inward, then from
        the template directory, then from `styles` and `scripts`
        module lists declared in `page.py`.
        """
        plan = self._page_asset_plan(file_path)
        for found in plan.files:
            self._register_file(found, collector)
        if plan.module_path is not None:
            self._collect_module_lists(plan.module_path, collector)

    def _page_asset_plan(self, file_path: Path) -> _PageAssetPlan:
        """Return the memoised plan of `file_path`, walking the disk on a miss."""
        plan = self._page_plan_cache.get(file_path)
        if plan is not None and not self._plan_stale(plan):
            self._page_plan_cache.move_to_end(file_path)
            return plan
        plan = self._build_page_asset_plan(file_path)
        self._page_plan_cache[file_path] = plan
        if len(self._page_plan_cache) > _PAGE_PLAN_CACHE_MAX_SIZE:
            self._page_plan_cache.popitem(last=False)
        return plan

    def _build_page_asset_plan(self, file_path: Path) -> _PageAssetPlan:
        """Probe every role directory behind `file_path` in one pass."""
        resolved = file_path.resolve()
        page_root = self._resolver.find_page_root(resolved)
        files: list[_FoundAsset] = []
        directories: list[Path] = []
        for layout_dir in self._find_layout_directories(resolved, page_root):
            directories.append(layout_dir)
            files += self._find_role_files(
                layout_dir,
                logical_name=self._resolver.logical_name_for_layout(
                    layout_dir, page_root
                ),
                role="layout",
            )
        template_dir = resolved.parent
        directories.append(template_dir)
        files += self._find_role_files(
            template_dir,
            logical_name=self._resolver.logical_name_for_template(
                template_dir, page_root
            ),
            role="template",
        )
        return _PageAssetPlan(
            files=tuple(files),
            module_path=resolved if resolved.exists() else None,
            directory_mtimes=_directory_mtimes(directories),
        )

    def _plan_stale(self, plan: _PageAssetPlan) -> bool:
        """Whether a directory the plan was read from has moved since.

        Only `DEBUG` pays the stats. An asset created or deleted moves the
        mtime of the directory holding it. Read per call, so an override
        takes effect.
        """
        if not settings.DEBUG:
            return False
        for directory, mtime in plan.directory_mtimes:
            try:
                current = directory.stat().st_mtime
            except OSError:
                return True
            if current > mtime:
                return True
        return False

    def discover_component_assets(
        self, info: ComponentInfo, collector: StaticCollector
    ) -> None:
        """Collect co-located CSS, JS, and module asset lists for a component."""
        component_dir = self._component_directory(info)
        if component_dir is None:
            return
        logical_name = f"components/{info.name}"
        self._collect_role_directory(
            component_dir,
            logical_name=logical_name,
            role="component",
            collector=collector,
        )
        module_path = info.module_path
        if module_path is not None and module_path.exists():
            self._collect_module_lists(module_path, collector)

    def _collect_role_directory(
        self,
        directory: Path,
        *,
        logical_name: str,
        role: str,
        collector: StaticCollector,
    ) -> None:
        """Register every `{stem}{ext}` file the directory holds for the role."""
        for found in self._find_role_files(
            directory, logical_name=logical_name, role=role
        ):
            self._register_file(found, collector)

    def _find_role_files(
        self, directory: Path, *, logical_name: str, role: str
    ) -> list[_FoundAsset]:
        """Return the `{stem}{ext}` files that exist in `directory` for the role.

        The set of extensions probed comes from `KindRegistry.kinds()`, so
        registering a new kind during `AppConfig.ready` is enough to teach
        discovery about it. Each hit carries its resolved path, because the
        collector dedups on it.
        """
        found: list[_FoundAsset] = []
        for stem in self._stems.stems(role):
            for kind in default_kinds.kinds():
                suffix = default_kinds.extension(kind)
                candidate = directory / f"{stem}{suffix}"
                if candidate.exists():
                    found.append(_FoundAsset(candidate.resolve(), logical_name, kind))
        return found

    def _collect_module_lists(
        self, module_path: Path, collector: StaticCollector
    ) -> None:
        """Read URL list variables matching every registered placeholder slot.

        The discovery layer iterates over registered slots in
        `default_placeholders` and reads the variable named after each
        slot. Each URL gets a `kind` derived from its file extension via
        `KindRegistry.kind_for_extension`. URLs whose suffix is not in
        the registry are dropped with a debug log.

        The caller in `discover_page_assets` passes a resolved module
        path. The component entry point still calls with the raw path,
        so the key is normalised here as a safety net.
        """
        cache_key = module_path if module_path.is_absolute() else module_path.resolve()
        if cache_key in self._module_list_cache:
            self._module_list_cache.move_to_end(cache_key)
            cached = self._module_list_cache[cache_key]
        else:
            lists = pages_loaders.read_module_string_lists(
                module_path, [slot.name for slot in default_placeholders]
            )
            if lists is None:
                self._module_list_cache[cache_key] = {}
                if len(self._module_list_cache) > _MODULE_LIST_CACHE_MAX_SIZE:
                    self._module_list_cache.popitem(last=False)
                return
            cached = lists
            self._module_list_cache[cache_key] = cached
            if len(self._module_list_cache) > _MODULE_LIST_CACHE_MAX_SIZE:
                self._module_list_cache.popitem(last=False)
        for slot_name, urls in cached.items():
            for url in urls:
                self._register_module_url(url, slot_name, collector)

    def _register_module_url(
        self, url: str, slot_name: str, collector: StaticCollector
    ) -> None:
        """Resolve a module-level URL to a kind and add it to the collector.

        The kind comes from `KindRegistry.kind_for_extension(suffix)`
        where suffix is the lowercase trailing dot-extension of the
        URL. URLs whose extension is not registered, or whose resolved
        kind belongs to a different slot, are dropped with a debug log.
        """
        suffix = _url_suffix(url)
        if not suffix:
            logger.debug("Module URL %r has no recognised extension", url)
            return
        kind = default_kinds.kind_for_extension(suffix)
        if kind is None:
            logger.debug("Module URL %r has unregistered extension %r", url, suffix)
            return
        kind_slot = default_kinds.slot(kind)
        if kind_slot != slot_name:
            logger.debug(
                "Module URL %r maps to kind %r in slot %r, so the %r list dropped it",
                url,
                kind,
                kind_slot,
                slot_name,
            )
            return
        collector.add(StaticAsset(url=url, kind=kind))

    def _register_file(self, found: _FoundAsset, collector: StaticCollector) -> None:
        """Register a file with the backend and add the result to the collector.

        Warnings are logged for `OSError` and `ValueError`. All other
        exception types propagate so bugs in custom backends surface
        loudly.
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
        collector.add(asset)
        asset_registered.send(sender=asset, collector=collector, backend=backend)

    def _component_directory(self, info: ComponentInfo) -> Path | None:
        """Return the directory that holds a composite component, or None."""
        if info.is_simple:
            return None
        if info.template_path is not None:
            return info.template_path.parent
        if info.module_path is not None:  # pragma: no cover
            return info.module_path.parent
        return None  # pragma: no cover

    def _find_layout_directories(
        self, file_path: Path, page_root: Path | None
    ) -> list[Path]:
        """Walk up from the page directory and return layout dirs outermost first.

        The caller is expected to pass a resolved absolute `file_path`.
        The resolver contract also guarantees that `page_root` is
        resolved, which lets this loop compare paths with `==` without
        issuing another filesystem call per iteration.
        """
        if file_path in self._layout_dir_cache:
            self._layout_dir_cache.move_to_end(file_path)
            return self._layout_dir_cache[file_path]
        directories: list[Path] = []
        current_dir = file_path.parent
        while True:
            if (current_dir / "layout.djx").exists():
                directories.append(current_dir)
            if page_root is not None and current_dir == page_root:
                break
            parent = current_dir.parent
            if parent == current_dir:
                break
            current_dir = parent
        result = list(reversed(directories))
        self._layout_dir_cache[file_path] = result
        if len(self._layout_dir_cache) > _LAYOUT_DIR_CACHE_MAX_SIZE:
            self._layout_dir_cache.popitem(last=False)
        return result
