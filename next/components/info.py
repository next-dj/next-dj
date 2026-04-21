"""`ComponentInfo` value object and helpers for component filesystem paths."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    """What we know about one component after scanning the filesystem."""

    name: str
    scope_root: Path
    scope_relative: str
    template_path: Path | None
    module_path: Path | None
    is_simple: bool
    resolved_scope_root: Path = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Freeze the resolved form of `scope_root` once at construction."""
        try:
            resolved = self.scope_root.resolve()
        except OSError:
            resolved = self.scope_root
        object.__setattr__(self, "resolved_scope_root", resolved)

    @property
    def scope_key(self) -> tuple[str, Path, str]:
        """Stable tuple for grouping by name and scope (ignores template paths)."""
        return (self.name, self.scope_root, self.scope_relative)


def _paths_from_component_info(info: ComponentInfo) -> set[Path]:
    """Return resolved filesystem paths that define one component."""
    out: set[Path] = set()
    if info.template_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.template_path.resolve())
    if info.module_path is not None:
        with contextlib.suppress(OSError):
            out.add(info.module_path.resolve())
    return out


__all__ = ["ComponentInfo"]
