"""Synthetic scaffolding for performance benchmarks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components.info import ComponentInfo


if TYPE_CHECKING:
    from pathlib import Path


_DEFAULT_LEAF_BODY = "def render(request):\n    return 'x'\n"


def build_pages_tree(
    root: Path,
    depth: int,
    fanout: int,
    *,
    leaf: str = "page.py",
    leaf_body: str = _DEFAULT_LEAF_BODY,
) -> None:
    """Materialise a synthetic page tree of ``depth`` * ``fanout`` under ``root``."""
    if depth == 0:
        (root / leaf).write_text(leaf_body)
        return
    for i in range(fanout):
        child = root / f"n_{i}"
        child.mkdir()
        build_pages_tree(
            child,
            depth - 1,
            fanout,
            leaf=leaf,
            leaf_body=leaf_body,
        )


def build_component_djx_dir(root: Path, count: int) -> None:
    """Write ``count`` simple ``.djx`` components into ``root`` (flat)."""
    for i in range(count):
        (root / f"comp_{i}.djx").write_text(f"<div>c{i}</div>")


def noop_form_handler(**_: object) -> None:  # pragma: no cover - bench stub
    """Stub action handler for form-action benchmarks."""


def noop_signal_receiver(sender: object, **_: object) -> None:  # pragma: no cover
    """No-op Django signal receiver for signal-overhead benchmarks."""
    del sender


def build_component_info(root: Path, name: str = "c") -> ComponentInfo:
    """Create one ``ComponentInfo`` without writing to the filesystem."""
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=root / f"{name}.djx",
        module_path=None,
        is_simple=True,
    )


def build_component_info_list(root: Path, count: int) -> list[ComponentInfo]:
    """Create ``count`` ``ComponentInfo`` objects without writing to the filesystem."""
    return [build_component_info(root, f"c_{i}") for i in range(count)]
