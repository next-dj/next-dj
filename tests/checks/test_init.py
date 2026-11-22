import ast
import collections
import inspect
import pathlib
import re

import pytest

import next.checks as checks_package


_NEXT_ROOT = pathlib.Path(inspect.getfile(checks_package)).parent.parent
_CHECK_ID = re.compile(r"^next\.[EWI]\d+$")


def _check_id_owners() -> dict[str, set[str]]:
    """Map every `next.*` check id in the tree to the modules that emit it."""
    owners: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(_NEXT_ROOT.rglob("*.py")):
        module = path.relative_to(_NEXT_ROOT.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.keyword) or node.arg != "id":
                continue
            value = node.value
            if isinstance(value, ast.Constant) and _CHECK_ID.match(str(value.value)):
                owners[value.value].add(module)
    return dict(owners)


_CHECK_ID_OWNERS = _check_id_owners()


def _check_id_literals() -> dict[str, set[str]]:
    """Map every string literal that is a whole check id to the modules holding it.

    This also reads the ids a module binds to a constant before passing it as `id=`.
    """
    owners: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(_NEXT_ROOT.rglob("*.py")):
        module = path.relative_to(_NEXT_ROOT.parent).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _CHECK_ID.match(node.value)
            ):
                owners[node.value].add(module)
    return dict(owners)


_CHECK_ID_LITERALS = _check_id_literals()


def _dotted(module: str) -> str:
    return module.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def _check_id_mentions() -> dict[str, set[str]]:
    """Map every check id to the modules that hold it or read a constant bound to it.

    A `codes` module only names the ids, and the modules reading its constants emit.
    """
    trees = {
        _dotted(path.relative_to(_NEXT_ROOT.parent).as_posix()): ast.parse(
            path.read_text(encoding="utf-8")
        )
        for path in sorted(_NEXT_ROOT.rglob("*.py"))
    }
    constants: dict[str, str] = {}
    for tree in trees.values():
        for node in tree.body:
            if (
                isinstance(node, ast.AnnAssign | ast.Assign)
                and isinstance(node.value, ast.Constant)
                and _CHECK_ID.match(str(node.value.value))
            ):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = str(node.value.value)
    mentions: dict[str, set[str]] = collections.defaultdict(set)
    for check_id, modules in _CHECK_ID_LITERALS.items():
        mentions[check_id].update(_dotted(module) for module in modules)
    for module, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in constants:
                mentions[constants[node.id]].add(module)
    return dict(mentions)


_CHECK_ID_MENTIONS = _check_id_mentions()
_ID_HELPERS = frozenset({"next.checks.common", "next.conf.checks"})
"""The modules that build the message of an id for the check modules calling them."""

_RETIRED_IDS = frozenset(
    {"next.E001", "next.E014", "next.E023", "next.E026", "next.E064", "next.E066"}
    | {"next.E081", "next.E091", "next.E109"}
    | {f"next.W{number:03d}" for number in range(3, 30)}
    | {f"next.W{number:03d}" for number in range(32, 42)}
    | {"next.W044", "next.W045"}
    | {f"next.W{number:03d}" for number in range(47, 54)}
    | {f"next.W{number:03d}" for number in range(64, 67)}
    | {"next.W073", "next.W161"}
)

_CHECKS_REFERENCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs"
    / "content"
    / "ref"
    / "system-checks.rst"
)
_REFERENCE_ROW = re.compile(r"^   \* - ``(next\.[EWI]\d{3})``$", re.MULTILINE)
_EMITTED_BY = re.compile(
    r"^   \* - ``(next\.[EWI]\d{3})``\n(?:     - .*\n(?:       .*\n)*)     - (.*)$",
    re.MULTILINE,
)


def _named(module: str, cell: set[str]) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in cell)


class TestLeafPackage:
    """`next.checks` holds the shared tags and imports nothing of the framework."""

    def test_all_is_the_two_tags(self) -> None:
        assert checks_package.__all__ == ["NEXT", "SEO"]

    def test_tags_keep_their_values(self) -> None:
        assert (checks_package.NEXT, checks_package.SEO) == ("next", "seo")

    def test_package_imports_no_framework_module(self) -> None:
        tree = ast.parse(pathlib.Path(inspect.getfile(checks_package)).read_text())
        imported = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert imported == []

    def test_check_functions_no_longer_resolve_off_the_package(self) -> None:
        assert not hasattr(checks_package, "check_next_pages_configuration")


class TestCheckIdAllocation:
    """Every `next.*` check id is allocated to one module across the framework.

    Each area asserting its own id hides two areas taking the same number.
    """

    def test_the_tree_allocates_check_ids(self) -> None:
        assert _CHECK_ID_OWNERS

    def test_no_id_is_emitted_from_two_modules(self) -> None:
        shared = {
            check_id: sorted(modules)
            for check_id, modules in _CHECK_ID_OWNERS.items()
            if len(modules) > 1
        }
        assert shared == {}

    def test_the_app_directories_finder_owns_its_own_id(self) -> None:
        assert _CHECK_ID_OWNERS["next.E083"] == {"next/static/checks.py"}

    def test_no_id_literal_is_held_by_two_modules(self) -> None:
        shared = {
            check_id: sorted(modules)
            for check_id, modules in _CHECK_ID_LITERALS.items()
            if len(modules) > 1
        }
        assert shared == {}

    def test_no_retired_id_is_emitted_again(self) -> None:
        assert sorted(_RETIRED_IDS & set(_CHECK_ID_LITERALS)) == []

    @pytest.mark.parametrize("kind", ["E", "W"])
    def test_every_number_below_the_highest_is_allocated_or_retired(
        self, kind: str
    ) -> None:
        prefix = f"next.{kind}"
        numbers = [
            int(check_id.removeprefix(prefix))
            for check_id in _CHECK_ID_LITERALS
            if check_id.startswith(prefix)
        ]
        expected = {f"{prefix}{number:03d}" for number in range(1, max(numbers) + 1)}
        assert sorted(expected - set(_CHECK_ID_LITERALS) - _RETIRED_IDS) == []

    @pytest.mark.skipif(
        not _CHECKS_REFERENCE.is_file(), reason="the documentation is not checked out"
    )
    def test_the_reference_table_lists_every_id_once(self) -> None:
        rows = _REFERENCE_ROW.findall(_CHECKS_REFERENCE.read_text(encoding="utf-8"))
        assert len(rows) == len(set(rows))
        assert sorted(rows) == sorted(_CHECK_ID_LITERALS)

    @pytest.mark.skipif(
        not _CHECKS_REFERENCE.is_file(), reason="the documentation is not checked out"
    )
    def test_the_reference_table_names_the_modules_that_emit_each_id(self) -> None:
        text = _CHECKS_REFERENCE.read_text(encoding="utf-8")
        cells = {
            check_id: set(re.findall(r"``([\w.]+)``", cell))
            for check_id, cell in _EMITTED_BY.findall(text)
        }
        modules = {
            _dotted(path.relative_to(_NEXT_ROOT.parent).as_posix())
            for path in _NEXT_ROOT.rglob("*.py")
        }
        unknown = {
            check_id: sorted(cell - modules)
            for check_id, cell in cells.items()
            if cell - modules
        }
        unnamed = {
            check_id: sorted(missing)
            for check_id, found in _CHECK_ID_MENTIONS.items()
            if (
                missing := {
                    module
                    for module in found - _ID_HELPERS
                    if not module.endswith(".codes")
                    and not _named(module, cells.get(check_id, set()))
                }
            )
        }
        assert (unknown, unnamed) == ({}, {})
