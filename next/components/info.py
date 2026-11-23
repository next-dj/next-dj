"""`ComponentInfo` value object and helpers for component filesystem paths."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, override


if TYPE_CHECKING:
    from pathlib import Path

    from next.defer import DeferMode


LOADING_FILE: Final = "loading.djx"
ERROR_FILE: Final = "error.djx"


@dataclass(frozen=True, slots=True)
class BoundaryFiles:
    """The `loading.djx` and `error.djx` beside a component, `None` for one missing."""

    loading: Path | None
    error: Path | None


def boundary_files(directory: Path) -> BoundaryFiles | None:
    """Return the boundary files inside `directory`, `None` when it holds neither."""
    loading = directory / LOADING_FILE
    error = directory / ERROR_FILE
    found = BoundaryFiles(
        loading if loading.is_file() else None, error if error.is_file() else None
    )
    if found.loading is None and found.error is None:
        return None
    return found


@dataclass(frozen=True, slots=True)
class CallRoute:
    """How a call that names no delivery mode renders, decided at the `stamp` it holds.

    The stamp names the declaration stores and their versions the decision read.
    """

    stamp: tuple[object, ...]
    mode: DeferMode | None
    ruled: bool


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    """What we know about one component after scanning the filesystem."""

    name: str
    scope_root: Path
    scope_relative: str
    template_path: Path | None
    module_path: Path | None
    is_simple: bool
    files: BoundaryFiles | None = None
    resolved_scope_root: Path = field(init=False, repr=False, compare=False)
    route: CallRoute | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Freeze the resolved form of `scope_root` once at construction."""
        try:
            resolved = self.scope_root.resolve()
        except OSError:
            resolved = self.scope_root
        object.__setattr__(self, "resolved_scope_root", resolved)

    @override
    def __str__(self) -> str:
        """Name the component as a log reads it, its module beside its name."""
        return f"component {self.name!r} ({self.module_path})"

    def hold_route(self, route: CallRoute) -> None:
        """Keep how a call naming no delivery mode renders, until `route` goes stale."""
        object.__setattr__(self, "route", route)

    def current_files(self) -> BoundaryFiles | None:
        """Return the boundary files on disk now, `None` for a single-file component."""
        if self.is_simple:
            return None
        source = self.module_path or self.template_path
        return None if source is None else boundary_files(source.parent)


def paths_from_component_info(info: ComponentInfo) -> set[Path]:
    """Return resolved filesystem paths that define one component."""
    out: set[Path] = set()
    if info.template_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.template_path.resolve())
    if info.module_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.module_path.resolve())
    if info.files is not None:
        for boundary in (info.files.loading, info.files.error):
            if boundary is not None:
                with contextlib.suppress(OSError):
                    out.add(boundary.resolve())
    return out


__all__ = [
    "ERROR_FILE",
    "LOADING_FILE",
    "BoundaryFiles",
    "CallRoute",
    "ComponentInfo",
    "boundary_files",
]
