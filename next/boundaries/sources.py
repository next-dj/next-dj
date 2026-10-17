"""The Python files of each project application, their syntax and their page trees.

Files are parsed as text, and only the zone owners load the page modules of a tree.
"""

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from next.conf import next_framework_settings
from next.discovery import get_pages_directories, get_router_manager

from .graph import App, module_owner, path_owner


_EXCLUDED_ROOTS: Final = frozenset({"tests", "tests.py", "migrations"})
_TEMPLATE_SUFFIXES: Final = frozenset({".djx", ".html"})
_SKIPPED_DIRS: Final = frozenset({"__pycache__", "node_modules"})
_INIT: Final = "__init__"
_INIT_FILE: Final = "__init__.py"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One Python file of an application and the dotted name it imports under.

    A relative import resolves against `package`, and grimp reads a `packaged` file.
    """

    app: App
    path: Path
    module: str
    package: str
    tree: bool
    packaged: bool


@dataclass(frozen=True, slots=True)
class Project:
    """The project applications with their files, templates and parsed sources."""

    base_dir: Path
    apps: tuple[App, ...]
    files: tuple[SourceFile, ...]
    modules: Mapping[str, SourceFile]
    templates: tuple[tuple[App, Path], ...]
    _syntax: dict[Path, ast.Module | None] = field(
        default_factory=dict, compare=False, repr=False
    )
    _errors: dict[Path, tuple[int, str]] = field(
        default_factory=dict, compare=False, repr=False
    )
    _names: dict[Path, dict[str, str]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def app_of(self, path: Path) -> App | None:
        """Return the application whose directory holds `path`."""
        return path_owner(self.apps, path)

    def app_of_module(self, module: str) -> App | None:
        """Return the application whose package holds the dotted `module`."""
        return module_owner(self.apps, module)

    def app_by_label(self, label: str) -> App | None:
        """Return the application of `label`, `None` for no project application."""
        return next((app for app in self.apps if app.label == label), None)

    @property
    def labels(self) -> frozenset[str]:
        """Return the label of every project application."""
        return frozenset(app.label for app in self.apps)

    def foreign_label(self, perm: str, app: App) -> App | None:
        """Return the other application the label of `app_label.codename` names."""
        label, dot, _codename = perm.partition(".")
        owner = self.app_by_label(label) if dot else None
        return None if owner == app else owner

    def in_tree(self, module: str) -> bool:
        """Return whether the dotted `module` is a file of a page or component tree."""
        found = self.modules.get(module)
        return found is not None and found.tree

    def relative(self, path: Path) -> str:
        """Return `path` relative to `BASE_DIR` with forward slashes."""
        if path.is_relative_to(self.base_dir):
            return path.relative_to(self.base_dir).as_posix()
        return path.as_posix()

    def syntax(self, path: Path) -> ast.Module | None:
        """Return the parsed source of `path`, `None` when it does not parse."""
        if path not in self._syntax:
            self._syntax[path], error = _parse(path)
            if error is not None:
                self._errors[path] = error
        return self._syntax[path]

    def syntax_error(self, path: Path) -> tuple[int, str] | None:
        """Return the line and the reason the source of `path` does not parse."""
        self.syntax(path)
        return self._errors.get(path)

    def names(self, source: SourceFile, syntax: ast.Module) -> dict[str, str]:
        """Return the names the imports of a parsed file bind, read once per file."""
        if source.path not in self._names:
            self._names[source.path] = imported_names(syntax, source)
        return self._names[source.path]

    def files_of(self, label: str) -> tuple[SourceFile, ...]:
        """Return the files of the application `label`."""
        return tuple(source for source in self.files if source.app.label == label)


def _parse(path: Path) -> tuple[ast.Module | None, tuple[int, str] | None]:
    """Parse one file, keeping the reason a broken one does not parse."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    try:
        return ast.parse(text, filename=str(path)), None
    except SyntaxError as exc:
        return None, (exc.lineno or 0, exc.msg)


def excluded(app: App, path: Path) -> bool:
    """Return whether `path` lies in the tests or migrations of `app`."""
    return path.relative_to(app.path).parts[0] in _EXCLUDED_ROOTS


def within(app: App, path: Path, name: str) -> bool:
    """Return whether `path` is the module `name` of `app` or lies in that package."""
    if not path.is_relative_to(app.path):
        return False
    parts = path.relative_to(app.path).parts
    return parts == (f"{name}.py",) or (len(parts) > 1 and parts[0] == name)


def _walk(app: App, others: frozenset[Path]) -> Iterator[Path]:
    """Yield every Python and template file of `app`, leaving other apps out."""
    for directory, dirnames, filenames in app.path.walk():
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SKIPPED_DIRS
            and not name.startswith(".")
            and directory / name not in others
            and not excluded(app, directory / name)
        )
        for name in sorted(filenames):
            path = directory / name
            if not excluded(app, path):
                yield path


def _packaged(app: App, path: Path) -> bool:
    """Return whether every folder between the app and the file is a regular package."""
    folder = path.parent
    while folder != app.path:
        if not (folder / _INIT_FILE).is_file():
            return False
        folder = folder.parent
    return True


def _source(app: App, path: Path, trees: tuple[Path, ...]) -> SourceFile:
    """Return the file at `path` with the dotted name it imports under."""
    parts = list(path.relative_to(app.path).with_suffix("").parts)
    package = parts[-1] == _INIT
    if package:
        parts.pop()
    module = ".".join([app.name, *parts])
    return SourceFile(
        app=app,
        path=path,
        module=module,
        package=module if package else module.rpartition(".")[0],
        tree=any(path.is_relative_to(root) for root in trees),
        packaged=_packaged(app, path),
    )


def _components_dir_names() -> frozenset[str]:
    """Return the component folder names the component backends read."""
    names: set[str] = set()
    for entry in next_framework_settings.COMPONENT_BACKENDS:
        name = entry.get("COMPONENTS_DIR") if isinstance(entry, Mapping) else None
        if isinstance(name, str) and name:
            names.add(name)
    return frozenset(names)


def page_trees(apps: tuple[App, ...]) -> tuple[Path, ...]:
    """Return each page tree the routers walk and each component folder of an app."""
    found: list[Path] = []
    router_manager, _errors = get_router_manager()
    if router_manager is not None:
        for router in router_manager.backends:
            found.extend(get_pages_directories(router))
    found.extend(app.path / name for app in apps for name in _components_dir_names())
    owned = {
        resolved
        for directory in found
        if (resolved := directory.resolve()).is_dir()
        and path_owner(apps, resolved) is not None
    }
    return tuple(sorted(owned))


def load_project(base_dir: Path, apps: tuple[App, ...]) -> Project:
    """Return the files and templates of `apps`, parsed lazily on first read."""
    trees = page_trees(apps)
    roots = frozenset(app.path for app in apps)
    walked = [(app, path) for app in apps for path in _walk(app, roots - {app.path})]
    files = tuple(
        _source(app, path, trees) for app, path in walked if path.suffix == ".py"
    )
    return Project(
        base_dir=base_dir.resolve(),
        apps=apps,
        files=files,
        modules={source.module: source for source in files},
        templates=tuple(
            (app, path) for app, path in walked if path.suffix in _TEMPLATE_SUFFIXES
        ),
    )


def imported_names(syntax: ast.Module, source: SourceFile) -> dict[str, str]:
    """Map each name a module binds by an import to the dotted path it stands for."""
    names: dict[str, str] = {}
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    names[alias.asname] = alias.name
                else:
                    head = alias.name.partition(".")[0]
                    names[head] = head
        elif isinstance(node, ast.ImportFrom):
            base = absolute_module(node, source)
            for alias in node.names:
                if base is not None and alias.name != "*":
                    names[alias.asname or alias.name] = f"{base}.{alias.name}"
    return names


def absolute_module(node: ast.ImportFrom, source: SourceFile) -> str | None:
    """Return the module a `from` import reads, a relative one resolved, or `None`."""
    if node.level == 0:
        return node.module
    parts = source.package.split(".")
    if node.level - 1 >= len(parts):
        return None
    base = ".".join(parts[: len(parts) - (node.level - 1)])
    return f"{base}.{node.module}" if node.module else base


def dotted(node: ast.expr, names: Mapping[str, str], module: str) -> str | None:
    """Return the dotted path an expression of names and attributes stands for.

    A name the module does not import resolves inside the module itself.
    """
    if isinstance(node, ast.Name):
        return names.get(node.id, f"{module}.{node.id}")
    if isinstance(node, ast.Attribute):
        head = dotted(node.value, names, module)
        return None if head is None else f"{head}.{node.attr}"
    return None


def last_name(node: ast.expr) -> str | None:
    """Return the last name an expression spells, `models` for `django.db.models`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def call_name(node: ast.Call) -> str | None:
    """Return the last name of the callee, `receiver` for `dispatch.receiver(...)`."""
    return last_name(node.func)


def assign_targets(node: ast.AST) -> list[ast.expr]:
    """Return what an assignment statement assigns to, nothing for any other node."""
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return [node.target]
    return []


def assigns(node: ast.AST, name: str) -> bool:
    """Return whether a statement assigns to the plain name `name`."""
    return any(
        isinstance(target, ast.Name) and target.id == name
        for target in assign_targets(node)
    )


def argument(call: ast.Call, keyword: str, index: int | None = None) -> ast.expr | None:
    """Return the argument at `index`, or the one passed as `keyword`."""
    if index is not None and len(call.args) > index:
        return call.args[index]
    return next((item.value for item in call.keywords if item.arg == keyword), None)


def string(node: ast.expr | None) -> str | None:
    """Return the value of a string literal, `None` for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


__all__ = [
    "Project",
    "SourceFile",
    "absolute_module",
    "argument",
    "assign_targets",
    "assigns",
    "call_name",
    "dotted",
    "excluded",
    "imported_names",
    "last_name",
    "load_project",
    "page_trees",
    "string",
    "within",
]
