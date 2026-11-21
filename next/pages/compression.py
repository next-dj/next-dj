"""Whether next.dj compresses a page, as its `page.py` declares by `page.compress`."""

import sys
from dataclasses import dataclass
from pathlib import Path

from next.caches import PageCache
from next.introspect import registering_site
from next.utils import template_edits_watched

from .declarations import ModuleDeclarations
from .loaders import (
    PAGE_MODULE,
    load_python_module_memo,
    module_generation,
    module_version,
)


@dataclass(frozen=True, slots=True, eq=False)
class CompressDeclaration:
    """One `page.compress` call as written, with the namespace of the run that made it.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    enabled: object
    namespace: dict[str, object]
    top_level: bool

    def problem(self) -> str | None:
        """Return why the call declares nothing, `None` when it declares a choice."""
        if not self.top_level:
            return "it runs inside a function rather than at the top of the module"
        if not isinstance(self.enabled, bool):
            return f"enabled is {self.enabled!r}, which is no bool"
        return None

    def choice(self) -> bool | None:
        """Return the choice the call declares, `None` for one E222 reports."""
        if self.problem() is not None:
            return None
        return bool(self.enabled)


class CompressDeclarations(ModuleDeclarations[CompressDeclaration]):
    """Hold the latest `page.compress` call of each `page.py`, the last call winning."""

    __slots__ = ()


compress_declarations = CompressDeclarations()


class CompressionDeclarations:
    """The `page.compress` declaration of the `Page` manager."""

    def compress(self, enabled: object, /) -> None:
        """Let next.dj compress this page with `True`, or keep it plain with `False`.

        A page that renders a secret beside reflected input opts out against BREACH.
        """
        file_path, top_level = registering_site()
        if file_path.name != PAGE_MODULE:
            compress_declarations.misplace(file_path)
            return
        namespace = sys._getframe(1).f_globals
        compress_declarations.declare(
            file_path, CompressDeclaration(enabled, namespace, top_level)
        )


type _Stamp = tuple[int, int, int]

_HELD: PageCache[Path, tuple[_Stamp, bool | None]] = PageCache()


def _read(file_path: Path) -> bool | None:
    """Return the choice the latest load of `file_path` declares."""
    module = load_python_module_memo(file_path)
    held = compress_declarations.current(file_path, module)
    return None if held is None else held.choice()


def page_compress(file_path: Path) -> bool | None:
    """Return the compression choice of the page at `file_path`, `None` for none.

    A project without a declaration answers at once, which keeps a plain `GET` free.
    """
    if not compress_declarations:
        return None
    if template_edits_watched():
        return _read(file_path)
    stamp = (module_version(), module_generation(), compress_declarations.version)
    held = _HELD.get(file_path)
    if held is None or held[0] != stamp:
        held = _HELD[file_path] = (stamp, _read(file_path))
    return held[1]


__all__ = [
    "CompressDeclaration",
    "CompressDeclarations",
    "CompressionDeclarations",
    "compress_declarations",
    "page_compress",
]
