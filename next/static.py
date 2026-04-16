"""Discover and inject co-located static assets for pages and components.

Asset files are named after the ``.djx`` file they decorate and live in the
same directory: ``template.css``/``template.js`` sit beside ``template.djx``,
``layout.css``/``layout.js`` beside ``layout.djx``, and
``component.css``/``component.js`` beside ``component.djx``. Pages and
components may also declare ``styles`` and ``scripts`` list variables at
module level (``page.py``/``component.py``). The collector gathers references
during rendering and the manager injects them into placeholder slots emitted
by the ``{% collect_styles %}`` and ``{% collect_scripts %}`` template tags.
Files are served via the ``/_next/static/`` route prefix.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from django.http import HttpRequest, HttpResponseNotFound
from django.urls import URLPattern, path
from django.views.static import serve as django_serve

from .conf import import_class_cached, next_framework_settings


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path

    from django.http.response import HttpResponseBase

    from .components import ComponentInfo


logger = logging.getLogger(__name__)


__all__ = [
    "AssetDiscovery",
    "FileStaticBackend",
    "StaticAsset",
    "StaticBackend",
    "StaticCollector",
    "StaticManager",
    "StaticsFactory",
    "static_manager",
    "static_serve_view",
]


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"

_KIND_CSS = "css"
_KIND_JS = "js"


@dataclass(frozen=True, slots=True)
class StaticAsset:
    """One CSS or JS file reference to include on the rendered page.

    A file asset originates from a co-located file on disk and has
    ``source_path`` set to its absolute filesystem path. An external asset
    carries a raw URL supplied by a ``styles`` or ``scripts`` module variable
    and has no ``source_path``.
    """

    url: str
    kind: str
    source_path: Path | None = None


class StaticCollector:
    """Accumulate static asset references during a single page render.

    The collector is stored in template context under ``_static_collector`` so
    that nested component rendering can append its own assets. Duplicate URLs
    are ignored and insertion order follows the CSS-cascade convention: items
    added with ``prepend=True`` (shared third-party dependencies declared with
    ``{% use_style %}`` / ``{% use_script %}``) appear first in the order they
    were registered, followed by co-located files and module-level lists in
    nested depth-first render order.
    """

    def __init__(self) -> None:
        """Create an empty collector with separate ordered lists for CSS and JS."""
        self._seen_urls: set[str] = set()
        self._styles: list[StaticAsset] = []
        self._scripts: list[StaticAsset] = []
        self._styles_prepend_idx: int = 0
        self._scripts_prepend_idx: int = 0

    def add(self, asset: StaticAsset, *, prepend: bool = False) -> None:
        """Append (or prepend) one asset unless its URL was already seen.

        ``prepend=True`` inserts the asset at the current front of its list so
        that dependencies declared with ``{% use_style %}`` / ``{% use_script %}``
        appear before co-located files. Multiple prepended items keep their
        registration order relative to each other.
        """
        if asset.url in self._seen_urls:
            return
        self._seen_urls.add(asset.url)
        if asset.kind == _KIND_CSS:
            self._insert(self._styles, asset, prepend=prepend, kind=_KIND_CSS)
        elif asset.kind == _KIND_JS:
            self._insert(self._scripts, asset, prepend=prepend, kind=_KIND_JS)
        else:
            logger.debug(
                "Ignoring asset with unknown kind %r: %s", asset.kind, asset.url
            )

    def _insert(
        self,
        target: list[StaticAsset],
        asset: StaticAsset,
        *,
        prepend: bool,
        kind: str,
    ) -> None:
        """Insert ``asset`` at the current prepend cursor or at the end."""
        if prepend:
            idx = (
                self._styles_prepend_idx
                if kind == _KIND_CSS
                else self._scripts_prepend_idx
            )
            target.insert(idx, asset)
            if kind == _KIND_CSS:
                self._styles_prepend_idx += 1
            else:
                self._scripts_prepend_idx += 1
        else:
            target.append(asset)

    def styles(self) -> list[StaticAsset]:
        """Return collected CSS assets in insertion order."""
        return list(self._styles)

    def scripts(self) -> list[StaticAsset]:
        """Return collected JS assets in insertion order."""
        return list(self._scripts)


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags."""

    @abstractmethod
    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Register a co-located asset file and return the URL used to serve it.

        ``logical_name`` is a slash-separated path without file extension, such
        as ``"about"``, ``"components/card"``, or ``"guides/layout"``. The
        backend appends the ``.css`` or ``.js`` extension based on ``kind``.
        """

    @abstractmethod
    def render_link_tag(self, url: str) -> str:
        """Return an HTML link tag string for a CSS asset URL."""

    @abstractmethod
    def render_script_tag(self, url: str) -> str:
        """Return an HTML script tag string for a JS asset URL."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern]:
        """Return Django URL patterns that serve files registered by this backend."""


@dataclass
class _FileRegistryEntry:
    """Mapping from a served path to an absolute filesystem path."""

    source_path: Path


class FileStaticBackend(StaticBackend):
    """Serve co-located assets under the ``/_next/static/`` URL prefix.

    Assets are renamed by their logical identity so that
    ``_components/card/component.css`` becomes ``/_next/static/components/card.css``
    and ``pages/about/template.css`` becomes ``/_next/static/about.css``. Files
    are served by a single catch-all view backed by an in-memory registry.
    """

    URL_PREFIX: ClassVar[str] = "_next/static"

    _DEFAULT_CSS_TAG: ClassVar[str] = '<link rel="stylesheet" href="{url}">'
    _DEFAULT_JS_TAG: ClassVar[str] = '<script src="{url}"></script>'

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Build the backend from an optional configuration dict with ``OPTIONS``."""
        cfg = config or {}
        opts = cfg.get("OPTIONS") or {}
        self._css_tag = str(opts.get("css_tag") or self._DEFAULT_CSS_TAG)
        self._js_tag = str(opts.get("js_tag") or self._DEFAULT_JS_TAG)
        self._file_registry: dict[str, _FileRegistryEntry] = {}

    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Register a file under ``logical_name.<kind>`` and return its served URL."""
        extension = _kind_to_extension(kind)
        relative = f"{logical_name}{extension}"
        self._file_registry[relative] = _FileRegistryEntry(
            source_path=source_path.resolve(),
        )
        return f"/{self.URL_PREFIX}/{relative}"

    def render_link_tag(self, url: str) -> str:
        """Return a ``<link rel="stylesheet">`` tag for the given URL."""
        return self._css_tag.format(url=url)

    def render_script_tag(self, url: str) -> str:
        """Return a ``<script>`` tag for the given URL."""
        return self._js_tag.format(url=url)

    def generate_urls(self) -> list[URLPattern]:
        """Return the single catch-all URL pattern for the serve view."""
        return [
            path(
                f"{self.URL_PREFIX}/<path:file_path>",
                static_serve_view,
                name="next_static_serve",
            ),
        ]

    def lookup(self, relative: str) -> _FileRegistryEntry | None:
        """Return the registry entry for the given relative served path, if any."""
        return self._file_registry.get(relative)

    def clear_registry(self) -> None:
        """Drop every registered file mapping."""
        self._file_registry.clear()


def _kind_to_extension(kind: str) -> str:
    """Return the file extension for ``css`` or ``js`` kinds."""
    if kind == _KIND_CSS:
        return ".css"
    if kind == _KIND_JS:
        return ".js"
    msg = f"Unsupported asset kind: {kind!r}"
    raise ValueError(msg)


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Load a Python file as an anonymous module or return ``None`` on failure."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"next_static_module_{file_path.stem}", file_path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError) as e:
        logger.debug(
            "Could not load module %s for static asset discovery: %s", file_path, e
        )
        return None
    else:
        return module


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string list attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


@dataclass
class _DiscoveryRoots:
    """Cached collection of page tree roots used to compute logical URL paths."""

    roots: tuple[Path, ...] = field(default_factory=tuple)


class AssetDiscovery:
    """Detect co-located CSS and JS files and module-level asset list variables."""

    _TEMPLATE_CSS: ClassVar[str] = "template.css"
    _TEMPLATE_JS: ClassVar[str] = "template.js"
    _LAYOUT_CSS: ClassVar[str] = "layout.css"
    _LAYOUT_JS: ClassVar[str] = "layout.js"
    _COMPONENT_CSS: ClassVar[str] = "component.css"
    _COMPONENT_JS: ClassVar[str] = "component.js"

    def __init__(self, backend_provider: StaticManager) -> None:
        """Store the manager that owns the backend used for file registration."""
        self._manager = backend_provider

    def discover_page_assets(
        self,
        file_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Collect layout, template, and module-level assets for a page module path.

        Layout assets are collected from the outermost layout down to the page
        directory. Template assets are collected next so that inner styles can
        override outer ones. Module-level ``styles`` and ``scripts`` from
        ``page.py`` are appended last.
        """
        page_root = self._find_page_root(file_path)
        layout_dirs = self._find_layout_directories(file_path, page_root)
        for layout_dir in layout_dirs:
            self._collect_layout_directory(layout_dir, page_root, collector)

        self._collect_template_directory(file_path.parent, page_root, collector)

        if file_path.exists():
            self._collect_module_lists(file_path, collector)

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

        css_file = component_dir / self._COMPONENT_CSS
        if css_file.exists():
            self._register_file(css_file, logical_name, _KIND_CSS, collector)

        js_file = component_dir / self._COMPONENT_JS
        if js_file.exists():
            self._register_file(js_file, logical_name, _KIND_JS, collector)

        module_path = info.module_path
        if module_path is not None and module_path.exists():
            self._collect_module_lists(module_path, collector)

    def _collect_layout_directory(
        self,
        layout_dir: Path,
        page_root: Path | None,
        collector: StaticCollector,
    ) -> None:
        """Register ``layout.css``/``layout.js`` beside ``layout.djx``."""
        logical_name = self._logical_name_for_layout(layout_dir, page_root)
        css_file = layout_dir / self._LAYOUT_CSS
        if css_file.exists():
            self._register_file(css_file, logical_name, _KIND_CSS, collector)
        js_file = layout_dir / self._LAYOUT_JS
        if js_file.exists():
            self._register_file(js_file, logical_name, _KIND_JS, collector)

    def _collect_template_directory(
        self,
        template_dir: Path,
        page_root: Path | None,
        collector: StaticCollector,
    ) -> None:
        """Register ``template.css``/``template.js`` beside ``template.djx``."""
        logical_name = self._logical_name_for_template(template_dir, page_root)
        css_file = template_dir / self._TEMPLATE_CSS
        if css_file.exists():
            self._register_file(css_file, logical_name, _KIND_CSS, collector)
        js_file = template_dir / self._TEMPLATE_JS
        if js_file.exists():
            self._register_file(js_file, logical_name, _KIND_JS, collector)

    def _collect_module_lists(
        self,
        module_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Read ``styles`` and ``scripts`` list variables from a Python module."""
        module = _load_python_module(module_path)
        if module is None:
            return
        for url in _read_string_list(module, "styles"):
            collector.add(StaticAsset(url=url, kind=_KIND_CSS))
        for url in _read_string_list(module, "scripts"):
            collector.add(StaticAsset(url=url, kind=_KIND_JS))

    def _register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
        collector: StaticCollector,
    ) -> None:
        """Register a file with the backend and add the result to the collector."""
        backend = self._manager.default_backend
        try:
            url = backend.register_file(source_path, logical_name, kind)
        except (OSError, ValueError) as e:
            logger.debug(
                "Failed to register static asset %s as %r: %s",
                source_path,
                logical_name,
                e,
            )
            return
        collector.add(
            StaticAsset(url=url, kind=kind, source_path=source_path.resolve())
        )

    def _component_directory(self, info: ComponentInfo) -> Path | None:
        """Return the directory that contains a composite component, or ``None``."""
        if info.is_simple:
            return None
        if info.template_path is not None:
            return info.template_path.parent
        if info.module_path is not None:
            return info.module_path.parent
        return None

    def _find_page_root(self, file_path: Path) -> Path | None:
        """Return the page tree root that contains ``file_path``, if any."""
        resolved_parent = file_path.parent.resolve()
        for root in self._page_roots():
            with contextlib.suppress(ValueError):
                resolved_parent.relative_to(root)
                return root
        return None

    def _find_layout_directories(
        self,
        file_path: Path,
        page_root: Path | None,
    ) -> list[Path]:
        """Walk up from the page directory and return layout dirs outermost first."""
        directories: list[Path] = []
        current_dir = file_path.parent
        stop_at = page_root.resolve() if page_root is not None else None
        while True:
            if (current_dir / "layout.djx").exists():
                directories.append(current_dir)
            if stop_at is not None and current_dir.resolve() == stop_at:
                break
            parent = current_dir.parent
            if parent == current_dir:
                break
            current_dir = parent
        return list(reversed(directories))

    def _logical_name_for_template(
        self,
        template_dir: Path,
        page_root: Path | None,
    ) -> str:
        """Return the logical URL name for a page template directory."""
        if page_root is None:
            return self._fallback_logical_name(template_dir)
        try:
            rel = template_dir.resolve().relative_to(page_root.resolve())
        except ValueError:
            return self._fallback_logical_name(template_dir)
        parts = rel.parts
        return "/".join(parts) if parts else "index"

    def _logical_name_for_layout(
        self,
        layout_dir: Path,
        page_root: Path | None,
    ) -> str:
        """Return the logical URL name for a layout directory."""
        if page_root is None:
            return f"{self._fallback_logical_name(layout_dir)}/layout"
        try:
            rel = layout_dir.resolve().relative_to(page_root.resolve())
        except ValueError:
            return f"{self._fallback_logical_name(layout_dir)}/layout"
        parts = rel.parts
        if parts:
            return "/".join((*parts, "layout"))
        return "layout"

    def _fallback_logical_name(self, directory: Path) -> str:
        """Return a safe logical name when the page root cannot be determined."""
        return directory.name or "index"

    def _page_roots(self) -> tuple[Path, ...]:
        """Return cached absolute page tree roots from the configured page backends."""
        return self._manager._page_roots()


class StaticsFactory:
    """Build ``StaticBackend`` instances from ``DEFAULT_STATIC_BACKENDS`` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> StaticBackend:
        """Instantiate the backend class named by ``config['BACKEND']``."""
        backend_path = config.get("BACKEND", "next.static.FileStaticBackend")
        backend_class = import_class_cached(backend_path)
        if not isinstance(backend_class, type) or not issubclass(
            backend_class, StaticBackend
        ):
            msg = f"Backend {backend_path!r} is not a StaticBackend subclass"
            raise TypeError(msg)
        factory: Any = backend_class
        instance: StaticBackend = factory(config)
        return instance


class StaticManager:
    """Coordinate static backends, asset discovery, and placeholder injection.

    The manager loads backends from ``NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']``
    on first use, owns a single ``AssetDiscovery`` instance, and yields URL
    patterns contributed by each backend for ``_LazyUrlPatterns``.
    """

    def __init__(self) -> None:
        """Prepare empty backend list and discovery helper."""
        self._backends: list[StaticBackend] = []
        self._discovery: AssetDiscovery | None = None
        self._cached_page_roots: tuple[Path, ...] | None = None

    def __iter__(self) -> Iterator[URLPattern]:
        """Yield URL patterns contributed by every backend in configuration order."""
        self._ensure_backends()
        for backend in self._backends:
            yield from backend.generate_urls()

    def __len__(self) -> int:
        """Return the number of configured backends."""
        self._ensure_backends()
        return len(self._backends)

    @property
    def default_backend(self) -> StaticBackend:
        """Return the first configured backend, which is used for file registration."""
        self._ensure_backends()
        return self._backends[0]

    @property
    def discovery(self) -> AssetDiscovery:
        """Return the shared asset discovery helper."""
        if self._discovery is None:
            self._discovery = AssetDiscovery(self)
        return self._discovery

    def discover_page_assets(
        self,
        file_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Forward page asset discovery to the shared ``AssetDiscovery`` instance."""
        self._ensure_backends()
        self.discovery.discover_page_assets(file_path, collector)

    def discover_component_assets(
        self,
        info: ComponentInfo,
        collector: StaticCollector,
    ) -> None:
        """Forward component asset discovery to the shared ``AssetDiscovery``."""
        self._ensure_backends()
        self.discovery.discover_component_assets(info, collector)

    def inject(self, html: str, collector: StaticCollector) -> str:
        """Replace style and script placeholders in rendered HTML with actual tags.

        Placeholders are inserted by the ``{% collect_styles %}`` and
        ``{% collect_scripts %}`` template tags during the ``Template.render``
        pass. This method runs after rendering is complete and every asset has
        been recorded on the collector.
        """
        if STYLES_PLACEHOLDER in html:
            html = html.replace(STYLES_PLACEHOLDER, self._render_style_tags(collector))
        if SCRIPTS_PLACEHOLDER in html:
            html = html.replace(
                SCRIPTS_PLACEHOLDER, self._render_script_tags(collector)
            )
        return html

    def _render_style_tags(self, collector: StaticCollector) -> str:
        """Return concatenated CSS link tags for every collected style asset."""
        self._ensure_backends()
        backend = self._backends[0]
        return "\n".join(
            backend.render_link_tag(asset.url) for asset in collector.styles()
        )

    def _render_script_tags(self, collector: StaticCollector) -> str:
        """Return concatenated JS script tags for every collected script asset."""
        self._ensure_backends()
        backend = self._backends[0]
        return "\n".join(
            backend.render_script_tag(asset.url) for asset in collector.scripts()
        )

    def _ensure_backends(self) -> None:
        """Load configured backends on first access."""
        if not self._backends:
            self._reload_config()

    def _reload_config(self) -> None:
        """Rebuild the backend list from the merged framework settings."""
        self._backends.clear()
        self._discovery = None
        self._cached_page_roots = None
        configs = next_framework_settings.DEFAULT_STATIC_BACKENDS
        if not isinstance(configs, list):
            configs = []
        for config in configs:
            if not isinstance(config, dict):
                continue
            try:
                backend = StaticsFactory.create_backend(config)
            except Exception:
                logger.exception("Error creating static backend from config %s", config)
                continue
            self._backends.append(backend)
        if not self._backends:
            self._backends.append(FileStaticBackend())

    def _page_roots(self) -> tuple[Path, ...]:
        """Return absolute page tree roots from configured page backends."""
        if self._cached_page_roots is not None:
            return self._cached_page_roots
        roots: list[Path] = []
        try:
            from .pages import get_pages_directories_for_watch  # noqa: PLC0415
        except ImportError:
            self._cached_page_roots = ()
            return self._cached_page_roots
        for root in get_pages_directories_for_watch():
            with contextlib.suppress(OSError):
                roots.append(root.resolve())
        self._cached_page_roots = tuple(roots)
        return self._cached_page_roots


static_manager = StaticManager()


def static_serve_view(
    request: HttpRequest,
    file_path: str,
) -> HttpResponseBase:
    """Serve a co-located static file registered by the ``FileStaticBackend``.

    The registered filesystem path is handed to ``django.views.static.serve``
    which handles streaming, Content-Type detection, and conditional GET via
    Last-Modified and If-Modified-Since headers. A 404 response is returned
    when the served path is not in the backend registry.
    """
    backend = static_manager.default_backend
    if not isinstance(backend, FileStaticBackend):
        return HttpResponseNotFound()
    entry = backend.lookup(file_path)
    if entry is None:
        return HttpResponseNotFound()
    source = entry.source_path
    return django_serve(
        request,
        path=source.name,
        document_root=str(source.parent),
    )
