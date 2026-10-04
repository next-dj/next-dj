import ast
import collections
import inspect
import pathlib
import re

import pytest

import next.checks as checks_package
from next.checks import _LAZY_ATTRIBUTES, _LAZY_SOURCES_BY_MODULE


_EAGER = frozenset({"NEXT", "SEO", "register_all", "reset_check_caches"})
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

_RETIRED_IDS = frozenset(
    {"next.E001", "next.E091"}
    | {f"next.W{number:03d}" for number in range(3, 30)}
    | {f"next.W{number:03d}" for number in range(32, 42)}
    | {"next.W044", "next.W045"}
    | {f"next.W{number:03d}" for number in range(47, 54)}
    | {f"next.W{number:03d}" for number in range(64, 67)}
    | {"next.W073"}
)

_CHECKS_REFERENCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docs"
    / "content"
    / "ref"
    / "system-checks.rst"
)
_REFERENCE_ROW = re.compile(r"^   \* - ``(next\.[EW]\d{3})``$", re.MULTILINE)


def _defined_check_names(module_path: pathlib.Path) -> frozenset[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("check_")
    )


def _area_check_sources() -> dict[str, tuple[pathlib.Path, ...]]:
    """Map each area's `checks` address to the files that define its checks.

    A package `__init__` only re-exports, so the names live in the submodules below it.
    """
    flat = {
        f"next.{path.parent.name}.checks": (path,)
        for path in sorted(_NEXT_ROOT.glob("*/checks.py"))
    }
    packages = {
        f"next.{path.parent.parent.name}.checks": tuple(
            sorted(path.parent.rglob("*.py"))
        )
        for path in sorted(_NEXT_ROOT.glob("*/checks/__init__.py"))
    }
    return flat | packages


def _checks_by_area_module() -> dict[str, frozenset[str]]:
    found = {
        module: frozenset[str]().union(*(_defined_check_names(p) for p in paths))
        for module, paths in _area_check_sources().items()
    }
    return {module: names for module, names in found.items() if names}


def _type_checking_imports() -> dict[str, frozenset[str]]:
    tree = ast.parse(
        (_NEXT_ROOT / "checks" / "__init__.py").read_text(encoding="utf-8")
    )
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    ]
    return {
        node.module: frozenset(alias.name for alias in node.names)
        for guard in guards
        for node in guard.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


class TestPublicSurface:
    """`__all__` and `__dir__` describe the same names, eager plus lazy."""

    def test_all_matches_dir(self) -> None:
        assert checks_package.__dir__() == sorted(checks_package.__all__)

    def test_all_is_sorted_and_unique(self) -> None:
        listed = checks_package.__all__
        assert listed == sorted(listed)
        assert len(listed) == len(set(listed))

    def test_all_is_the_eager_names_plus_the_lazy_map(self) -> None:
        assert set(checks_package.__all__) == _EAGER | set(_LAZY_ATTRIBUTES)

    def test_lazy_map_covers_every_area_checks_module(self) -> None:
        assert dict(_checks_by_area_module()) == {
            module: frozenset(names)
            for module, names in _LAZY_SOURCES_BY_MODULE.items()
        }

    def test_type_checking_block_mirrors_the_lazy_map(self) -> None:
        assert _type_checking_imports() == {
            module: frozenset(names)
            for module, names in _LAZY_SOURCES_BY_MODULE.items()
        }

    def test_lazy_module_keys_and_names_are_sorted(self) -> None:
        modules = list(_LAZY_SOURCES_BY_MODULE)
        assert modules == sorted(modules)
        assert all(
            list(names) == sorted(set(names))
            for names in _LAZY_SOURCES_BY_MODULE.values()
        )

    def test_all_lists_no_private_name(self) -> None:
        assert not [name for name in checks_package.__all__ if name.startswith("_")]

    def test_lazy_map_declares_only_public_names(self) -> None:
        assert not [name for name in _LAZY_ATTRIBUTES if name.startswith("_")]

    def test_lazy_map_names_only_checks_modules(self) -> None:
        assert all(module.endswith(".checks") for module in _LAZY_ATTRIBUTES.values())

    @pytest.mark.parametrize("name", sorted(_LAZY_ATTRIBUTES))
    def test_every_lazy_name_resolves(self, name: str) -> None:
        assert getattr(checks_package, name) is not None

    @pytest.mark.parametrize("name", sorted(_EAGER))
    def test_every_eager_name_resolves(self, name: str) -> None:
        assert getattr(checks_package, name) is not None

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'nonexistent'"):
            checks_package.__getattr__("nonexistent")


class TestRetiredReExports:
    """Private page helpers no longer resolve off the checks facade."""

    @pytest.mark.parametrize(
        "name",
        ["_has_template_or_djx", "_load_python_module", "_load_python_module_memo"],
    )
    def test_private_page_helper_is_gone(self, name: str) -> None:
        assert not hasattr(checks_package, name)


class TestCheckIdAllocation:
    """Every `next.*` check id is allocated to one module across the framework.

    Two areas reaching for the same free number is invisible when each area asserts
    its own id in isolation, so the whole tree is read in one pass here.
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
