"""The imports between the modules of the project packages, read by grimp.

grimp is optional, so the module records whether it loaded rather than requiring it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from .errors import BoundariesUnavailableError, ImportScanError


try:
    import grimp
    from grimp.exceptions import GrimpException
except ImportError:
    AVAILABLE = False
else:
    AVAILABLE = True

_PANIC_MODULE: Final = "pyo3_runtime"


@dataclass(frozen=True, slots=True)
class Import:
    """One import statement of `importer`, which reads the module `imported`."""

    importer: str
    imported: str
    line: int


@dataclass(frozen=True, slots=True)
class ImportScan:
    """The modules grimp found in the packages and the imports between them."""

    modules: frozenset[str]
    imports: tuple[Import, ...]


def package_imports(packages: Iterable[str]) -> ImportScan:
    """Return the modules of `packages` and every import between them.

    It writes no cache to disk, and a panic of grimp stops the scan, not the check run.
    """
    if not AVAILABLE:
        raise BoundariesUnavailableError
    names = sorted(set(packages))
    if not names:
        return ImportScan(frozenset(), ())
    try:
        graph = grimp.build_graph(*names, cache_dir=None)
    except GrimpException as exc:
        raise ImportScanError(str(exc)) from exc
    except BaseException as exc:
        if type(exc).__module__ != _PANIC_MODULE:
            raise
        reason = f"grimp panicked: {exc}"
        raise ImportScanError(reason) from exc
    found: list[Import] = []
    for importer in sorted(graph.modules):
        for imported in sorted(graph.find_modules_directly_imported_by(importer)):
            details = graph.get_import_details(importer=importer, imported=imported)
            lines = sorted({detail["line_number"] for detail in details}) or [0]
            found.extend(Import(importer, imported, line) for line in lines)
    return ImportScan(frozenset(graph.modules), tuple(found))


__all__ = ["AVAILABLE", "Import", "ImportScan", "package_imports"]
