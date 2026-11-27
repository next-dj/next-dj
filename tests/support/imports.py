import ast
import importlib.util
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path


class Kind(StrEnum):
    """When an import statement runs, which decides the contracts it answers to."""

    RUNTIME = "runtime"
    TYPING = "typing"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class Import:
    """One `import` statement of a framework module, its target resolved."""

    source: str
    target: str
    file: str
    line: int
    kind: Kind

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@cache
def package_root() -> Path:
    """Locate the imported `next` package, the installed wheel under CI.

    The test matrix checks out no source tree, so the graph reads the package itself.
    """
    spec = importlib.util.find_spec("next")
    assert spec is not None
    assert spec.origin is not None
    return Path(spec.origin).parent


@cache
def module_files() -> dict[str, Path]:
    """Map every dotted module name of the package to its source file."""
    root = package_root()
    files = {}
    for file in sorted(root.rglob("*.py")):
        parts = ("next", *file.relative_to(root).with_suffix("").parts)
        files[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = file
    return files


def module_names() -> list[str]:
    """Name every module of the package in a stable order."""
    return list(module_files())


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


_BLOCKS = (ast.stmt, ast.excepthandler, ast.match_case)


def _absolute(node: ast.ImportFrom, module: str, *, package: bool) -> str:
    """Spell the module an `ImportFrom` names in full, resolving its dots."""
    if not node.level:
        return node.module or ""
    base = module.split(".")
    keep = len(base) - node.level + (1 if package else 0)
    return ".".join([*base[:keep], *([node.module] if node.module else [])])


def _walk(
    nodes: list[ast.AST], kind: Kind, module: str, *, package: bool
) -> list[tuple[str, int, Kind]]:
    """Collect the imports among statements, entering blocks but never expressions."""
    found = []
    for node in nodes:
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno, kind) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = _absolute(node, module, package=package)
            found.extend((f"{source}.{a.name}", node.lineno, kind) for a in node.names)
        elif isinstance(node, ast.If) and _is_type_checking(node.test):
            inner = Kind.DEFERRED if kind is Kind.DEFERRED else Kind.TYPING
            found += _walk([*node.body], inner, module, package=package)
            found += _walk([*node.orelse], kind, module, package=package)
        else:
            inner = kind
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = Kind.DEFERRED
            children = [c for c in ast.iter_child_nodes(node) if isinstance(c, _BLOCKS)]
            found += _walk(children, inner, module, package=package)
    return found


def _resolve(dotted: str, known: dict[str, Path]) -> str | None:
    """Return the deepest framework module `dotted` names, or `None` outside it."""
    while dotted:
        if dotted in known:
            return dotted
        dotted, _, _ = dotted.rpartition(".")
    return None


def parse_imports(
    source: str, module: str, *, package: bool = False
) -> list[tuple[str, int, Kind]]:
    """Return each name `source` imports, relative ones made absolute, with line and kind."""
    return _walk([*ast.parse(source).body], Kind.RUNTIME, module, package=package)


@cache
def statements() -> tuple[tuple[str, str, int, Kind], ...]:
    """Return every import statement as module, imported name, line and kind."""
    found = []
    for module, file in module_files().items():
        text = file.read_text(encoding="utf-8")
        parsed = parse_imports(text, module, package=file.name == "__init__.py")
        found.extend((module, name, line, kind) for name, line, kind in parsed)
    return tuple(found)


@cache
def framework_imports() -> tuple[Import, ...]:
    """Return every import of one framework module by another, deduplicated."""
    files = module_files()
    root = package_root().parent
    shown = {
        module: file.relative_to(root).as_posix() for module, file in files.items()
    }
    edges = {}
    for module, name, line, kind in statements():
        target = _resolve(name, files)
        if target is None or target == module:
            continue
        file = shown[module]
        edges[module, target, line, kind] = Import(module, target, file, line, kind)
    return tuple(edges.values())
