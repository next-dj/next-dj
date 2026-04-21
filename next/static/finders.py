"""Django staticfiles finder that exposes next-dj co-located assets.

The finder surfaces every `template.css`, `layout.js`, and
`component.css` plus any stems registered on the stem registry under
the `next/` staticfiles namespace. The usual `{% static "next/about.css" %}`
call works without the user configuring anything.

The logical-path and source-file mapping is computed by the
co-located asset discovery helper below, which shares the same
`PathResolver` used at request-time discovery. The two layers agree on
every URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from django.contrib.staticfiles.finders import BaseFinder
from django.contrib.staticfiles.utils import matches_patterns
from django.core.files import File
from django.core.files.storage import Storage

from .assets import StaticNamespace, default_kinds
from .discovery import PathResolver, default_stems


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from .discovery import StemRegistry


def _collect_stem_static_files(
    out: dict[str, Path],
    directory: Path,
    logical_name: str,
    role: str,
    stems: StemRegistry,
) -> None:
    """Add `{stem}.<kind>` files found in the directory to the output map.

    Every registered kind is probed for each stem of the given role.
    """
    kinds = default_kinds.kinds()
    for stem in stems.stems(role):
        for kind in kinds:
            suffix = default_kinds.extension(kind)
            candidate = directory / f"{stem}{suffix}"
            if not candidate.exists():  # pragma: no cover
                continue
            static_path = f"{StaticNamespace.NEXT}/{logical_name}{suffix}"
            out.setdefault(static_path, candidate.resolve())


def discover_colocated_static_assets() -> dict[str, Path]:
    """Map staticfiles logical paths to absolute source files on disk.

    The helper scans every configured page-backend tree plus registered
    components. It honors the process-wide stem and kind registries, so
    custom stems registered during `AppConfig.ready` are picked up.
    """
    from next.pages import (  # noqa: PLC0415
        get_layout_djx_paths_for_watch,
        get_pages_directories_for_watch,
        get_template_djx_paths_for_watch,
    )

    out: dict[str, Path] = {}
    page_roots = tuple(root.resolve() for root in get_pages_directories_for_watch())
    resolver = PathResolver(lambda: page_roots)

    for template_path in get_template_djx_paths_for_watch():
        page_root = resolver.find_page_root(template_path)
        if page_root is None:
            continue
        template_dir = template_path.parent.resolve()
        logical_name = resolver.logical_name_for_template(template_dir, page_root)
        _collect_stem_static_files(
            out, template_dir, logical_name, "template", default_stems
        )

    for layout_path in get_layout_djx_paths_for_watch():
        page_root = resolver.find_page_root(layout_path)
        if page_root is None:  # pragma: no cover
            continue
        layout_dir = layout_path.parent.resolve()
        logical_name = resolver.logical_name_for_layout(layout_dir, page_root)
        _collect_stem_static_files(
            out, layout_dir, logical_name, "layout", default_stems
        )

    # next.components relies on the Django app registry being ready. A top-level
    # import would load it before AppConfig.ready() completes, so we defer here.
    from next.components import get_component_paths_for_watch  # noqa: PLC0415

    seen_component_dirs: set[Path] = set()
    for component_source in get_component_paths_for_watch():  # pragma: no cover
        component_dir = component_source.parent.resolve()
        if component_dir in seen_component_dirs:
            continue
        seen_component_dirs.add(component_dir)
        logical_name = f"components/{component_dir.name}"
        _collect_stem_static_files(
            out, component_dir, logical_name, "component", default_stems
        )

    return out


class _MappedSourceStorage(Storage):
    """Storage wrapper that serves files from an explicit path mapping."""

    def __init__(self, mapping: dict[str, Path]) -> None:
        """Store the explicit logical-path to absolute-path mapping."""
        self._mapping = mapping

    def _resolve(self, name: str) -> Path:
        if name not in self._mapping:
            msg = f"Unknown static file: {name}"
            raise FileNotFoundError(msg)
        return self._mapping[name]

    def exists(self, name: str) -> bool:
        """Return True when the logical name has a mapping and the file exists."""
        try:
            return self._resolve(name).exists()
        except FileNotFoundError:
            return False

    def open(self, name: str, mode: str = "rb") -> File:
        """Open the file behind the logical name for reading."""
        path = self._resolve(name)
        return File(path.open(mode))

    def path(self, name: str) -> str:
        """Return the absolute filesystem path behind the logical name."""
        return str(self._resolve(name))


class NextStaticFilesFinder(BaseFinder):
    """Expose next-dj co-located assets under the `next/` staticfiles namespace."""

    def __init__(self) -> None:
        """Initialise an empty mapping and storage, populated lazily on first lookup."""
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
        """Resolve the logical path to an absolute filesystem path or list."""
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
        """Yield logical-path and storage pairs for `collectstatic`."""
        patterns = list(ignore_patterns) if ignore_patterns is not None else []
        self._refresh()
        for logical_path in sorted(self._mapping):
            if matches_patterns(logical_path, patterns):
                continue
            yield logical_path, self._storage
