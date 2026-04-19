"""Discover and inject co-located static assets for pages and components.

Each ``.djx`` file may have a matching ``.css`` and ``.js`` file in the same
directory. Pages and components may also declare ``styles`` and ``scripts``
list variables in their Python modules. During rendering the collector gathers
all referenced assets. After rendering, ``StaticManager.inject`` replaces the
``{% collect_styles %}`` and ``{% collect_scripts %}`` placeholders with the
actual ``<link>`` and ``<script>`` tags. Public URLs are resolved through
Django staticfiles.

The ``Next`` JavaScript object is automatically injected on every page.
``StaticManager.inject`` prepends ``next.min.js`` as the first script and
follows it with an inline init script that passes the serialized context.
Context values opt into JavaScript exposure by using ``serialize=True`` on
their ``@context`` decorator. The preload hint for ``next.min.js`` is injected
before ``</head>`` so the browser downloads the file during HTML parsing.
"""

from __future__ import annotations

import contextlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, overload

from django.contrib.staticfiles.finders import BaseFinder
from django.contrib.staticfiles.storage import staticfiles_storage
from django.contrib.staticfiles.utils import matches_patterns
from django.core.files import File
from django.core.files.storage import Storage
from django.core.serializers.json import DjangoJSONEncoder

from . import pages
from .conf import import_class_cached, next_framework_settings
from .pages import (
    get_layout_djx_paths_for_watch,
    get_pages_directories_for_watch,
    get_template_djx_paths_for_watch,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from .components import ComponentInfo


logger = logging.getLogger(__name__)


__all__ = [
    "AssetDiscovery",
    "NextScriptBuilder",
    "NextStaticFilesFinder",
    "StaticAsset",
    "StaticBackend",
    "StaticCollector",
    "StaticFilesBackend",
    "StaticManager",
    "StaticsFactory",
    "discover_colocated_static_assets",
    "static_manager",
]


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"
HEAD_CLOSE = "</head>"

_KIND_CSS = "css"
_KIND_JS = "js"


def _kind_to_extension(kind: str) -> str:
    """Return the file extension for ``css`` or ``js`` kinds."""
    if kind == _KIND_CSS:
        return ".css"
    if kind == _KIND_JS:
        return ".js"
    msg = f"Unsupported asset kind: {kind!r}"
    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StaticAsset:
    """One CSS or JS asset reference collected during page rendering.

    For co-located disk files both ``url`` and ``source_path`` are set.
    For external URLs such as CDN links only ``url`` is set.
    For block-form ``{% #use_style %}`` and ``{% #use_script %}`` assets
    ``inline`` holds the pre-rendered HTML body and ``url`` is empty.
    """

    url: str
    kind: str
    source_path: Path | None = None
    inline: str | None = None


class NextScriptBuilder:
    """Builds the preload hint, script tag, and init script for the Next object.

    Called by ``StaticManager`` during the inject phase to produce the three
    HTML fragments that wire ``next.min.js`` into the page.
    """

    _PRELOAD_TEMPLATE: ClassVar[str] = '<link rel="preload" as="script" href="{url}">'
    _SCRIPT_TAG_TEMPLATE: ClassVar[str] = '<script src="{url}"></script>'
    _INIT_TEMPLATE: ClassVar[str] = "<script>Next._init({payload});</script>"

    def __init__(self, next_js_url: str) -> None:
        """Store the URL of the compiled next.min.js asset."""
        self._url = next_js_url

    def preload_link(self) -> str:
        """Return the preload hint tag for early browser download."""
        return self._PRELOAD_TEMPLATE.format(url=self._url)

    def script_tag(self) -> str:
        """Return the blocking script tag that executes next.min.js."""
        return self._SCRIPT_TAG_TEMPLATE.format(url=self._url)

    def init_script(self, js_context: dict[str, Any]) -> str:
        """Return the inline script that passes the serialized context to Next._init."""
        payload = json.dumps(js_context, cls=DjangoJSONEncoder, separators=(",", ":"))
        return self._INIT_TEMPLATE.format(payload=payload)


class StaticCollector:
    """Accumulate static asset references during a single page render.

    URL-form assets deduplicate by URL. Inline assets deduplicate by rendered
    body so identical blocks collapse to one entry. Assets added with
    ``prepend=True`` appear before regular append entries within the same kind,
    which keeps shared third-party dependencies at the front of the cascade.
    """

    def __init__(self) -> None:
        """Return a collector ready to accept assets. All buckets start empty."""
        self._seen_urls: set[str] = set()
        self._seen_inline: set[tuple[str, str]] = set()
        self._styles: list[StaticAsset] = []
        self._scripts: list[StaticAsset] = []
        self._styles_prepend_idx: int = 0
        self._scripts_prepend_idx: int = 0
        self._js_context: dict[str, Any] = {}

    def add(self, asset: StaticAsset, *, prepend: bool = False) -> None:
        """Add an asset unless its dedup key was already recorded.

        Inline assets always append and are keyed by their rendered body.
        URL-form assets are keyed by URL. When ``prepend=True``, URL-form
        assets are inserted before existing append entries while keeping
        registration order among prepended items.
        """
        is_inline = asset.inline is not None
        if is_inline:
            key = (asset.kind, asset.inline or "")
            if key in self._seen_inline:
                return
            self._seen_inline.add(key)
        else:
            if asset.url in self._seen_urls:
                return
            self._seen_urls.add(asset.url)
        use_prepend = prepend and not is_inline
        if asset.kind == _KIND_CSS:
            self._insert(self._styles, asset, prepend=use_prepend, kind=_KIND_CSS)
        elif asset.kind == _KIND_JS:
            self._insert(self._scripts, asset, prepend=use_prepend, kind=_KIND_JS)
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
        """Return collected CSS assets in insertion order. Callers must not mutate."""
        return self._styles

    def scripts(self) -> list[StaticAsset]:
        """Return collected JS assets in insertion order. Callers must not mutate."""
        return self._scripts

    def add_js_context(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Add a key to the JavaScript context exposed via ``Next.context``.

        First registration wins. Subsequent calls with the same key are
        silently ignored so that page context always takes priority over
        component context when both register the same key.
        """
        if key not in self._js_context:
            self._js_context[key] = value

    def js_context(self) -> dict[str, Any]:
        """Return the accumulated JavaScript context. Callers must not mutate."""
        return self._js_context


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags."""

    @abstractmethod
    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Register a co-located asset file and return its public URL.

        ``logical_name`` is a path without extension, such as ``"about"``,
        ``"components/card"``, or ``"guides/layout"``. The backend appends
        the appropriate extension based on ``kind``.
        """

    @abstractmethod
    def render_link_tag(self, url: str) -> str:
        """Return an HTML link tag string for a CSS asset URL."""

    @abstractmethod
    def render_script_tag(self, url: str) -> str:
        """Return an HTML script tag string for a JS asset URL."""


class StaticFilesBackend(StaticBackend):
    """Resolve co-located asset URLs through Django staticfiles.

    All files use the ``next/`` namespace so manifest hashing, S3 storage,
    and CDN configuration from Django settings apply automatically.
    """

    _DEFAULT_CSS_TAG: ClassVar[str] = '<link rel="stylesheet" href="{url}">'
    _DEFAULT_JS_TAG: ClassVar[str] = '<script src="{url}"></script>'
    STATIC_NAMESPACE: ClassVar[str] = "next"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Build the backend from optional ``OPTIONS`` tag template overrides."""
        cfg = config or {}
        opts = cfg.get("OPTIONS") or {}
        self._css_tag = str(opts.get("css_tag") or self._DEFAULT_CSS_TAG)
        self._js_tag = str(opts.get("js_tag") or self._DEFAULT_JS_TAG)
        self._url_cache: dict[str, str] = {}

    def _logical_static_path(self, logical_name: str, kind: str) -> str:
        extension = _kind_to_extension(kind)
        return f"{self.STATIC_NAMESPACE}/{logical_name}{extension}"

    def register_file(
        self,
        _source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Return URL from Django staticfiles for ``next/<logical_name>.<ext>``."""
        path = self._logical_static_path(logical_name, kind)
        cached = self._url_cache.get(path)
        if cached is not None:
            return cached
        try:
            url = str(staticfiles_storage.url(path))
        except ValueError as e:
            msg = (
                f"Static asset {path!r} is missing from Django staticfiles manifest. "
                "Run collectstatic and ensure next static finder is enabled."
            )
            raise RuntimeError(msg) from e
        self._url_cache[path] = url
        return url

    def render_link_tag(self, url: str) -> str:
        """Return a ``<link rel="stylesheet">`` tag for the given URL."""
        return self._css_tag.format(url=url)

    def render_script_tag(self, url: str) -> str:
        """Return a ``<script>`` tag for the given URL."""
        return self._js_tag.format(url=url)


# Inverse of _kind_to_extension. Maps file extension to kind string.
_KIND_BY_EXTENSION = {
    ".css": _KIND_CSS,
    ".js": _KIND_JS,
}


def _find_page_root_for(path: Path, page_roots: tuple[Path, ...]) -> Path | None:
    parent = path.parent.resolve()
    for root in page_roots:
        if parent.is_relative_to(root):
            return root
    return None


def _logical_template_name(template_dir: Path, page_root: Path) -> str:
    rel = template_dir.resolve().relative_to(page_root)
    parts = rel.parts
    return "/".join(parts) if parts else "index"


def _logical_layout_name(layout_dir: Path, page_root: Path) -> str:
    rel = layout_dir.resolve().relative_to(page_root)
    parts = rel.parts
    if parts:
        return "/".join((*parts, "layout"))
    return "layout"


def _collect_stem_static_files(
    out: dict[str, Path],
    directory: Path,
    logical_name: str,
    stem: str,
) -> None:
    """Add {stem}.css and {stem}.js from the given directory to the output mapping."""
    for suffix, kind in _KIND_BY_EXTENSION.items():
        candidate = directory / f"{stem}{suffix}"
        if not candidate.exists():
            continue
        static_path = f"next/{logical_name}{_kind_to_extension(kind)}"
        out.setdefault(static_path, candidate.resolve())


def discover_colocated_static_assets() -> dict[str, Path]:
    """Map staticfiles logical paths to absolute source files on disk."""
    out: dict[str, Path] = {}
    page_roots = tuple(root.resolve() for root in get_pages_directories_for_watch())

    for template_path in get_template_djx_paths_for_watch():
        page_root = _find_page_root_for(template_path, page_roots)
        if page_root is None:
            continue
        template_dir = template_path.parent
        logical_name = _logical_template_name(template_dir, page_root)
        _collect_stem_static_files(out, template_dir, logical_name, "template")

    for layout_path in get_layout_djx_paths_for_watch():
        page_root = _find_page_root_for(layout_path, page_roots)
        if page_root is None:
            continue
        layout_dir = layout_path.parent
        logical_name = _logical_layout_name(layout_dir, page_root)
        _collect_stem_static_files(out, layout_dir, logical_name, "layout")

    # next.components relies on the Django app registry being ready. Importing it
    # at module level would load it before AppConfig.ready() completes, so this
    # import is deferred until the function is actually called.
    from .components import get_component_paths_for_watch  # noqa: PLC0415

    seen_component_dirs: set[Path] = set()
    for component_source in get_component_paths_for_watch():
        component_dir = component_source.parent.resolve()
        if component_dir in seen_component_dirs:
            continue
        seen_component_dirs.add(component_dir)
        logical_name = f"components/{component_dir.name}"
        _collect_stem_static_files(out, component_dir, logical_name, "component")

    return out


class _MappedSourceStorage(Storage):
    """Storage wrapper that serves files from an explicit path mapping."""

    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping

    def _resolve(self, name: str) -> Path:
        if name not in self._mapping:
            msg = f"Unknown static file: {name}"
            raise FileNotFoundError(msg)
        return self._mapping[name]

    def exists(self, name: str) -> bool:
        try:
            return self._resolve(name).exists()
        except FileNotFoundError:
            return False

    def open(self, name: str, mode: str = "rb") -> File:
        path = self._resolve(name)
        return File(path.open(mode))

    def path(self, name: str) -> str:
        return str(self._resolve(name))


class NextStaticFilesFinder(BaseFinder):
    """Finder that exposes next.dj co-located assets under ``next/`` namespace."""

    def __init__(self) -> None:
        """Initialize with empty storage until the first lookup."""
        self._mapping: dict[str, Path] = {}
        self._storage: _MappedSourceStorage = _MappedSourceStorage({})

    def _refresh(self) -> None:
        self._mapping = discover_colocated_static_assets()
        self._storage = _MappedSourceStorage(self._mapping)

    @overload
    def find(
        self, path: str, find_all: Literal[False] = ...
    ) -> str | None: ...  # pragma: no cover

    @overload
    def find(
        self, path: str, find_all: Literal[True]
    ) -> list[str]: ...  # pragma: no cover

    def find(
        self,
        path: str,
        find_all: bool = False,  # noqa: FBT001, FBT002
    ) -> str | list[str] | None:
        """Resolve ``path`` to an absolute filesystem path or list of paths."""
        self._refresh()
        source = self._mapping.get(path)
        if source is None:
            return [] if find_all else None
        resolved = str(source)
        return [resolved] if find_all else resolved

    def list(
        self,
        ignore_patterns: Iterable[str] | None,
    ) -> Iterator[tuple[str, Storage]]:
        """Yield ``(relative_path, storage)`` pairs for ``collectstatic``."""
        patterns = list(ignore_patterns) if ignore_patterns is not None else []
        self._refresh()
        for logical_path in sorted(self._mapping):
            if matches_patterns(logical_path, patterns):
                continue
            yield logical_path, self._storage


class AssetDiscovery:
    """Detect co-located CSS and JS files and module-level asset list variables."""

    _TEMPLATE_CSS: ClassVar[str] = "template.css"
    _TEMPLATE_JS: ClassVar[str] = "template.js"
    _LAYOUT_CSS: ClassVar[str] = "layout.css"
    _LAYOUT_JS: ClassVar[str] = "layout.js"
    _COMPONENT_CSS: ClassVar[str] = "component.css"
    _COMPONENT_JS: ClassVar[str] = "component.js"

    def __init__(self, backend_provider: StaticManager) -> None:
        """Accept the manager used to resolve the active backend and page roots."""
        self._manager = backend_provider
        self._module_list_cache: dict[Path, tuple[list[str], list[str]]] = {}
        self._layout_dir_cache: dict[Path, list[Path]] = {}

    def discover_page_assets(
        self,
        file_path: Path,
        collector: StaticCollector,
    ) -> None:
        """Collect layout, template, and module-level assets for a page file.

        Assets are added from the outermost layout inward, then from the
        template directory, then from the ``styles`` and ``scripts`` lists
        declared in ``page.py``.
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
        """Register layout.css and layout.js found in the given directory."""
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
        """Register template.css and template.js found in the given directory."""
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
        cached = self._module_list_cache.get(module_path)
        if cached is None:
            module = pages._load_python_module(module_path)
            if module is None:
                self._module_list_cache[module_path] = ([], [])
                return
            styles = pages._read_string_list(module, "styles")
            scripts = pages._read_string_list(module, "scripts")
            self._module_list_cache[module_path] = (styles, scripts)
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
            if resolved_parent.is_relative_to(root):
                return root
        return None

    def _find_layout_directories(
        self,
        file_path: Path,
        page_root: Path | None,
    ) -> list[Path]:
        """Walk up from the page directory and return layout dirs outermost first."""
        cached = self._layout_dir_cache.get(file_path)
        if cached is not None:
            return cached
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
        result = list(reversed(directories))
        self._layout_dir_cache[file_path] = result
        return result

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
        return self._manager.page_roots()


class StaticsFactory:
    """Build ``StaticBackend`` instances from ``DEFAULT_STATIC_BACKENDS`` entries."""

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> StaticBackend:
        """Instantiate the backend class named by ``config['BACKEND']``."""
        backend_path = config.get("BACKEND", "next.static.StaticFilesBackend")
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

    Backends are loaded lazily from ``NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']``
    on first access. URL resolution is handled by ``StaticFilesBackend`` by
    default, which delegates to Django staticfiles.
    """

    def __init__(self) -> None:
        """Return a manager in an unloaded state.

        Backends are loaded on first access.
        """
        self._backends: list[StaticBackend] = []
        self._discovery: AssetDiscovery | None = None
        self._cached_page_roots: tuple[Path, ...] | None = None

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
        """Replace style and script placeholders in ``html`` with rendered tags.

        Placeholders are emitted by ``{% collect_styles %}`` and
        ``{% collect_scripts %}`` during template rendering. A missing
        placeholder is left unchanged. An empty collector replaces the
        placeholder with an empty string. The ``next.min.js`` script and its
        context init script are prepended before all user-collected scripts.
        The preload hint for ``next.min.js`` is injected before ``</head>``.
        """
        if STYLES_PLACEHOLDER in html:
            html = html.replace(STYLES_PLACEHOLDER, self._render_style_tags(collector))
        if SCRIPTS_PLACEHOLDER in html:
            next_scripts = self._render_next_scripts(collector)
            user_scripts = self._render_script_tags(collector)
            combined = next_scripts + user_scripts if user_scripts else next_scripts
            html = html.replace(SCRIPTS_PLACEHOLDER, combined)
        return self._inject_preload_hint(html)

    def _next_script_builder(self) -> NextScriptBuilder:
        url = staticfiles_storage.url("next/next.min.js")
        return NextScriptBuilder(url)

    def _render_next_scripts(self, collector: StaticCollector) -> str:
        """Return the framework script tag followed by the context init script."""
        builder = self._next_script_builder()
        parts = [builder.script_tag(), builder.init_script(collector.js_context())]
        return "\n".join(parts) + "\n"

    def _inject_preload_hint(self, html: str) -> str:
        """Insert the preload link for next.min.js immediately before </head>."""
        if HEAD_CLOSE not in html:
            return html
        builder = self._next_script_builder()
        return html.replace(HEAD_CLOSE, builder.preload_link() + "\n" + HEAD_CLOSE, 1)

    def _render_tags(
        self,
        assets: list[StaticAsset],
        render_url: Callable[[str], str],
    ) -> str:
        """Return newline-joined HTML tags for the given assets.

        Inline assets emit their body verbatim. URL-form assets are passed
        to ``render_url`` to produce the appropriate tag string.
        """
        return "\n".join(
            asset.inline if asset.inline is not None else render_url(asset.url)
            for asset in assets
        )

    def _render_style_tags(self, collector: StaticCollector) -> str:
        """Return CSS link tags and inline style bodies for all collected styles."""
        backend = self.default_backend
        return self._render_tags(collector.styles(), backend.render_link_tag)

    def _render_script_tags(self, collector: StaticCollector) -> str:
        """Return script tags and inline script bodies for all collected scripts."""
        backend = self.default_backend
        return self._render_tags(collector.scripts(), backend.render_script_tag)

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
            self._backends.append(StaticFilesBackend())

    def page_roots(self) -> tuple[Path, ...]:
        """Return absolute page tree roots from configured page backends."""
        if self._cached_page_roots is not None:
            return self._cached_page_roots
        roots: list[Path] = []
        # Imported here rather than at module level so that a missing attribute
        # on next.pages (e.g. during partial test teardown) raises ImportError,
        # which is the signal used to return an empty tuple instead of crashing.
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
