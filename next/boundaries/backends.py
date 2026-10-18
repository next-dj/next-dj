"""The import graph backends, grimp when it is installed and an `ast` reader always.

`NEXT_FRAMEWORK["BOUNDARIES_IMPORT_GRAPH"]` names one by its class, and `None` picks.
"""

import ast
import importlib.util
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from next.backends import resolve_setting_class
from next.conf.defaults import USER_SETTING

from . import imports
from .errors import ImportGraphError, ImportScanError
from .imports import Import, ImportScan
from .sources import Project, absolute_from


_SETTING: Final = "BOUNDARIES_IMPORT_GRAPH"

_INIT: Final = "__init__"
_INIT_FILE: Final = "__init__.py"
_SUFFIX: Final = ".py"
_ESCAPING: Final = "The relative import reaches above its package."


def project_packages(project: Project) -> tuple[str, ...]:
    """Return the top level package of every project app, each once and sorted."""
    return tuple(sorted({app.name.partition(".")[0] for app in project.apps}))


class ImportGraphBackend(ABC):
    """Reads the modules of the project packages and the imports between them."""

    @abstractmethod
    def scan(self, project: Project) -> ImportScan:
        """Return the modules of the top packages of the apps and their imports."""


class GrimpImportGraph(ImportGraphBackend):
    """Reads the import graph with grimp, the faster reader the extra installs."""

    def __init__(self) -> None:
        """Refuse to stand in for grimp when the environment does not install it."""
        if not imports.AVAILABLE:
            reason = (
                "names GrimpImportGraph, but grimp is not installed. Install the "
                "next.dj[boundaries] extra, or leave the key unset."
            )
            raise ImportGraphError(reason)

    @override
    def scan(self, project: Project) -> ImportScan:
        """Return what grimp reads of the packages.

        grimp reads UTF-8 alone and panics on any other text, so a panic names the file.
        """
        packages = project_packages(project)
        try:
            return imports.package_imports(packages)
        except ImportScanError as exc:
            if not exc.reason.startswith(imports.PANIC):
                raise
            foreign = _not_utf8(packages)
            if foreign is None:
                raise
            reason = (
                "grimp reads only UTF-8 sources, and this one is not. Save it as "
                "UTF-8, or name next.boundaries.AstImportGraph in "
                "BOUNDARIES_IMPORT_GRAPH."
            )
            raise ImportScanError(reason, project.relative(foreign), 0) from exc


@dataclass(frozen=True, slots=True)
class _Module:
    """One module file, the dotted name it imports under and the package it is in."""

    name: str
    path: Path
    package: str


class AstImportGraph(ImportGraphBackend):
    """Reads the import graph from the syntax of each module, the way grimp does.

    A module is a file of a regular package, and `x.py` wins over a package `x/` by it.
    """

    @override
    def scan(self, project: Project) -> ImportScan:
        """Return the modules of the packages and the import each statement makes."""
        files: dict[str, _Module] = {}
        names: set[str] = set()
        for package in project_packages(project):
            for directory in _package_dirs(package):
                found, spaces = _walk(package, directory)
                for module in found:
                    files.setdefault(module.name, module)
                names.update(spaces)
        names.update(files)
        found_imports: set[Import] = set()
        for module in files.values():
            found_imports.update(_module_imports(module, project, names))
        return ImportScan(frozenset(names), tuple(sorted(found_imports)))


def _package_dirs(package: str) -> tuple[Path, ...]:
    """Return the folders of a top level package, every portion of a namespace one."""
    spec = importlib.util.find_spec(package)
    locations = None if spec is None else spec.submodule_search_locations
    if locations is None:
        reason = f"The package {package} is no package on the Python path."
        raise ImportScanError(reason)
    return tuple(Path(location) for location in locations)


def _python_file(name: str) -> bool:
    """Return whether a file name is a module grimp reads, one dot and no hidden one."""
    return not name.startswith(".") and name.endswith(_SUFFIX) and name.count(".") == 1


def _walk(package: str, directory: Path) -> tuple[list[_Module], set[str]]:
    """Return the module files below a package folder and its namespace packages.

    A folder without `__init__.py` inside a regular package stops the walk there.
    """
    found: list[_Module] = []
    portions: list[Path] = []
    candidates: list[Path] = []
    for folder, dirnames, filenames in directory.walk():
        inside = any(folder.is_relative_to(portion) for portion in portions)
        if inside and _INIT_FILE not in filenames:
            dirnames.clear()
            continue
        if _INIT_FILE in filenames:
            if not inside:
                portions.append(folder)
        else:
            candidates.append(folder)
        dirnames[:] = sorted(name for name in dirnames if name.isidentifier())
        found.extend(
            _module(package, directory, folder / name)
            for name in sorted(filenames)
            if _python_file(name)
        )
    spaces = {
        ".".join([package, *candidate.relative_to(directory).parts])
        for candidate in candidates
        if any(module.path.is_relative_to(candidate) for module in found)
    }
    return found, spaces


def _module(package: str, directory: Path, path: Path) -> _Module:
    """Return the module a file below the package folder is, and its package."""
    parts = [package, *path.relative_to(directory).with_suffix("").parts]
    if parts[-1] == _INIT:
        parts.pop()
        name = ".".join(parts)
        return _Module(name, path, name)
    name = ".".join(parts)
    return _Module(name, path, name.rpartition(".")[0])


def _resolved(candidate: str, names: set[str]) -> str | None:
    """Return the module `candidate` names, or the one it is a name in, as grimp does.

    grimp looks one level up for a name inside a module, and drops anything deeper.
    """
    if candidate in names:
        return candidate
    parent = candidate.rpartition(".")[0]
    return parent if parent in names else None


def _module_imports(
    module: _Module, project: Project, names: set[str]
) -> Iterator[Import]:
    """Yield the import of a module each statement makes into the packages."""
    syntax = project.syntax(module.path)
    if syntax is None:
        error = project.syntax_error(module.path)
        line, reason = error if error is not None else (0, "It cannot be read.")
        raise ImportScanError(reason, project.relative(module.path), line)
    for node in ast.walk(syntax):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for candidate in _candidates(node, module, project):
            imported = _resolved(candidate, names)
            if imported is not None:
                yield Import(module.name, imported, node.lineno)


def _candidates(
    node: ast.Import | ast.ImportFrom, module: _Module, project: Project
) -> Iterator[str]:
    """Yield the dotted module each name of one import statement reaches for."""
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    base = absolute_from(node, module.package)
    if base is None:
        raise ImportScanError(_ESCAPING, project.relative(module.path), node.lineno)
    for alias in node.names:
        yield f"{base}.{alias.name}"


def _not_utf8(packages: tuple[str, ...]) -> Path | None:
    """Return the first module file of the packages whose bytes are no UTF-8 text."""
    for package in packages:
        for directory in _package_dirs(package):
            found, _spaces = _walk(package, directory)
            for module in found:
                try:
                    module.path.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    return module.path
    return None


def _shipped() -> type[ImportGraphBackend]:
    """Return the backend an unset key picks, grimp when it is installed."""
    return GrimpImportGraph if imports.AVAILABLE else AstImportGraph


def import_graph() -> ImportGraphBackend:
    """Return the import graph backend `BOUNDARIES_IMPORT_GRAPH` names.

    A value that is no path, does not import or names no backend is `ImportGraphError`.
    """
    raw = getattr(settings, USER_SETTING, None)
    value = raw.get(_SETTING) if isinstance(raw, dict) else None
    if value is not None and not isinstance(value, str):
        reason = f"is {value!r}, which is no dotted path to a class."
        raise ImportGraphError(reason)
    try:
        klass = resolve_setting_class(
            _SETTING,
            base=ImportGraphBackend,
            shipped=_shipped(),
            base_path="next.boundaries.ImportGraphBackend",
        )
    except ImproperlyConfigured as exc:
        reason = f"cannot be used. {exc}"
        raise ImportGraphError(reason) from exc
    return klass()


__all__ = [
    "AstImportGraph",
    "GrimpImportGraph",
    "ImportGraphBackend",
    "import_graph",
    "project_packages",
]
