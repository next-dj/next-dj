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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from next import pages

from .assets import _KIND_CSS, _KIND_JS, StaticAsset
from .signals import asset_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.components import ComponentInfo

    from .backends import StaticBackend
    from .collector import StaticCollector


logger = logging.getLogger(__name__)


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

    Default roles and their stems are as follows. The `template` role
    maps to `["template"]` and matches `template.css` or `template.js`.
    The `layout` role maps to `["layout"]` and matches `layout.css` or
    `layout.js`. The `component` role maps to `["component"]` and
    matches `component.css`.

    Users may register extra stems during `AppConfig.ready` to teach
    discovery about new filenames.

    Example::

        default_stems.register("template", "page")

    The example above teaches discovery to also pick up `page.css` or
    `page.js` alongside `template.css` or `template.js`.
    """

    DEFAULT_ROLES: tuple[str, ...] = ("template", "layout", "component")

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

    def roles(self) -> tuple[str, ...]:
        """Return all registered roles in registration order."""
        return tuple(self._roles)


default_stems: StemRegistry = StemRegistry()


class PathResolver:
    """Resolve page root and logical names for page, layout, and component paths.

    The resolver is shared between the asset discovery layer and the
    staticfiles finder so both layers produce identical logical names
    for the same on-disk location. The resolver assumes that the
    provider callable returns already resolved absolute page roots.
    """

    def __init__(
        self,
        page_roots_provider: Callable[[], tuple[Path, ...]],
    ) -> None:
        """Store the page-roots provider callable consulted on every lookup."""
        self._provider = page_roots_provider

    def page_roots(self) -> tuple[Path, ...]:
        """Return the current tuple of page tree roots from the provider."""
        return self._provider()

    def find_page_root(self, path: Path) -> Path | None:
        """Return the page tree root that contains the path, or None."""
        resolved_parent = path.parent.resolve()
        for root in self.page_roots():
            if resolved_parent.is_relative_to(root):
                return root
        return None

    def logical_name_for_template(
        self,
        template_dir: Path,
        page_root: Path | None,
    ) -> str:
        """Return the logical URL name for a page template directory.

        The caller is expected to pass a resolved `template_dir` and a
        resolved `page_root` from `find_page_root`.
        """
        if page_root is None:
            return self._fallback(template_dir)
        try:
            rel = template_dir.relative_to(page_root)
        except ValueError:  # pragma: no cover
            return self._fallback(template_dir)
        parts = rel.parts
        return "/".join(parts) if parts else "index"

    def logical_name_for_layout(
        self,
        layout_dir: Path,
        page_root: Path | None,
    ) -> str:
        """Return the logical URL name for a layout directory.

        The caller is expected to pass a resolved `layout_dir` and a
        resolved `page_root` from `find_page_root`.
        """
        if page_root is None:
            return f"{self._fallback(layout_dir)}/layout"
        try:
            rel = layout_dir.relative_to(page_root)
        except ValueError:  # pragma: no cover
            return f"{self._fallback(layout_dir)}/layout"
        parts = rel.parts
        if parts:
            return "/".join((*parts, "layout"))
        return "layout"

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
        self._module_list_cache: dict[Path, tuple[list[str], list[str]]] = {}
        self._layout_dir_cache: dict[Path, list[Path]] = {}

    def discover_page_assets(
        self,
        file_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Collect layout, template, and module-level assets for a page file.

        Assets are added from the outermost layout inward, then from
        the template directory, then from `styles` and `scripts`
        module lists declared in `page.py`.
        """
        resolved = file_path.resolve()
        page_root = self._resolver.find_page_root(resolved)
        for layout_dir in self._find_layout_directories(resolved, page_root):
            self._collect_role_directory(
                layout_dir,
                logical_name=self._resolver.logical_name_for_layout(
                    layout_dir, page_root
                ),
                role="layout",
                collector=collector,
            )

        self._collect_role_directory(
            resolved.parent,
            logical_name=self._resolver.logical_name_for_template(
                resolved.parent, page_root
            ),
            role="template",
            collector=collector,
        )

        if resolved.exists():
            self._collect_module_lists(resolved, collector)

    def discover_component_assets(
        self,
        info: ComponentInfo,
        collector: StaticCollector,
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
        """Register every `{stem}.css` and `{stem}.js` found in the directory."""
        for stem in self._stems.stems(role):
            css_file = directory / f"{stem}.css"
            if css_file.exists():
                self._register_file(css_file, logical_name, _KIND_CSS, collector)
            js_file = directory / f"{stem}.js"
            if js_file.exists():
                self._register_file(js_file, logical_name, _KIND_JS, collector)

    def _collect_module_lists(
        self,
        module_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Read `styles` and `scripts` list variables from a Python module.

        The caller in `discover_page_assets` passes a resolved module
        path. The component entry point still calls with the raw path,
        so the key is normalised here as a safety net.
        """
        cache_key = module_path if module_path.is_absolute() else module_path.resolve()
        cached = self._module_list_cache.get(cache_key)
        if cached is None:
            module = pages._load_python_module(module_path)
            if module is None:
                self._module_list_cache[cache_key] = ([], [])
                return
            styles = pages._read_string_list(module, "styles")
            scripts = pages._read_string_list(module, "scripts")
            self._module_list_cache[cache_key] = (styles, scripts)
            cached = (styles, scripts)
        styles_list, scripts_list = cached
        for url in styles_list:
            collector.add(StaticAsset(url=url, kind=_KIND_CSS))
        for url in scripts_list:
            collector.add(StaticAsset(url=url, kind=_KIND_JS))

    def _register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
        collector: StaticCollector,
    ) -> None:
        """Register a file with the backend and add the result to the collector.

        Warnings are logged for `OSError` and `ValueError`. All other
        exception types propagate so bugs in custom backends surface
        loudly.
        """
        backend = self._provider.default_backend
        try:
            url = backend.register_file(source_path, logical_name, kind)
        except (OSError, ValueError) as e:
            logger.warning(
                "Failed to register static asset %s as %r: %s",
                source_path,
                logical_name,
                e,
                extra={"source_path": str(source_path), "kind": kind},
            )
            return
        asset = StaticAsset(url=url, kind=kind, source_path=source_path.resolve())
        collector.add(asset)
        asset_registered.send(
            sender=asset,
            collector=collector,
            backend=backend,
        )

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
        self,
        file_path: Path,
        page_root: Path | None,
    ) -> list[Path]:
        """Walk up from the page directory and return layout dirs outermost first.

        The caller is expected to pass a resolved absolute `file_path`.
        The resolver contract also guarantees that `page_root` is
        resolved, which lets this loop compare paths with `==` without
        issuing another filesystem call per iteration.
        """
        cached = self._layout_dir_cache.get(file_path)
        if cached is not None:
            return cached
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
        return result
