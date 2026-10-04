"""Discovery of the `scripts.py` at the top of every routed page tree.

Each source loads through its own loader, so a failing one never marks a page broken.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from next.utils import load_tree_source, stat_mtime_ns

from .errors import ScriptsSourceImportError
from .markers import Script


SCRIPTS_MODULE: Final = "scripts.py"
"""The file at the top of a page tree that declares its third-party scripts."""


@dataclass(frozen=True, slots=True)
class ScriptsSource:
    """One `scripts.py` with the scripts it declares, or why it declares none.

    `problem` describes a `scripts` value that is not an iterable of `Script`.
    """

    root: Path
    path: Path
    stamp: int | None
    scripts: tuple[Script, ...] = ()
    error: ScriptsSourceImportError | None = None
    problem: str | None = None


def _declared(value: object) -> tuple[tuple[Script, ...], str | None]:
    """Return the scripts a `scripts` value holds and what is wrong with it, if any."""
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | Script):
        return (), f"scripts is {type(value).__name__!r}, not an iterable of Script"
    items = list(value)
    scripts = tuple(item for item in items if isinstance(item, Script))
    strays = [type(item).__name__ for item in items if not isinstance(item, Script)]
    if strays:
        return scripts, f"scripts holds {', '.join(map(repr, strays))}, not Script"
    return scripts, None


def load_scripts(root: Path) -> ScriptsSource | None:
    """Execute the `scripts.py` of the tree at `root`, `None` when it holds none.

    A failure is recorded on the source for the checks, and the pages still render.
    """
    loaded = load_tree_source(
        root / SCRIPTS_MODULE, "next_scripts_source", ScriptsSourceImportError
    )
    if loaded is None:
        return None
    if loaded.error is not None:
        return ScriptsSource(root, loaded.path, loaded.stamp, error=loaded.error)
    scripts, problem = _declared(getattr(loaded.module, "scripts", ()))
    return ScriptsSource(root, loaded.path, loaded.stamp, scripts, problem=problem)


def source_stale(source: ScriptsSource | None, root: Path) -> bool:
    """Whether the `scripts.py` of `root` changed since `source` was read."""
    held = None if source is None else source.stamp
    return stat_mtime_ns(root / SCRIPTS_MODULE) != held


__all__ = ["SCRIPTS_MODULE", "ScriptsSource", "load_scripts", "source_stale"]
