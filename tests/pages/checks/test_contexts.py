from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

from next.deps import Depends
from next.pages.checks.contexts import (
    annotation_mismatch,
    check_async_context_reads,
    check_async_dependency_cycles,
    check_context_registration_files,
)
from next.pages.loaders import load_python_module_memo
from tests.support import (
    bound_dependency,
    patch_checks_route_manager,
    routed,
    write_page,
)
from tests.support.access import page_source


_ORG = 'from next.pages import context\n\n@context("org")\ndef org():\n    return 1\n'
_LOOP_A = Depends("loop_a")
_LOOP_B = Depends("loop_b")


def _returns_dict() -> dict:
    return {}


def _returns_mapping() -> dict[str, int]:
    return {}


def _returns_str() -> str:
    return ""


def _unannotated():
    return {}


def _returns_literal() -> Literal["x"]:
    return "x"


class _UnanswerableCheck(type):
    def __subclasscheck__(cls, subclass) -> bool:
        raise TypeError


class _UnanswerableMapping(metaclass=_UnanswerableCheck):
    pass


class TestReturnAnnotationHelpers:
    """The return-shape probe both the context and the metadata checks share."""

    @pytest.mark.parametrize(
        ("func", "expected"),
        [
            (_returns_dict, None),
            (_returns_mapping, None),
            (_returns_str, "str"),
            (_unannotated, None),
            (_returns_literal, "Literal"),
        ],
        ids=["dict", "generic_dict", "str", "unannotated", "non_type"],
    )
    def test_the_mismatch_is_read_off_the_resolved_hint(self, func, expected) -> None:
        assert annotation_mismatch(func) == expected

    def test_a_subclass_probe_that_raises_reads_as_a_mismatch(self) -> None:
        # A third-party ABC hook may raise TypeError from inside issubclass.
        with patch("next.pages.checks.contexts.Mapping", _UnanswerableMapping):
            assert annotation_mismatch(_returns_dict) == "dict"


class TestStrayContexts:
    """next.E074 flags a `@context` outside a `page.py`, which no render collects."""

    def test_a_helper_context_is_flagged(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_page(root, "a", page_source())
        declaring = root / "a" / "helpers.py"
        declaring.write_text(_ORG)
        load_python_module_memo(declaring)

        with routed(root):
            messages = check_context_registration_files()

        assert [(m.id, m.obj) for m in messages] == [("next.E074", str(declaring))]
        assert "is not a page.py" in messages[0].msg


_SERIAL = (
    "from next.pages import context\n\n"
    "@context('a_rows')\n"
    "async def a_rows():\n    return [1]\n\n"
    "@context('b_count')\n"
    "def b_count(a_rows):\n    return len(a_rows)\n\n"
    "@context('c_free')\n"
    "def c_free():\n    return 0\n\n"
    "template = 'ok'\n"
)

_SERIALISING = _SERIAL + ("\n@context('a_more')\nasync def a_more():\n    return 2\n")

_INDIRECT = (
    "from next.pages import Context, context\n\n"
    "@context\n"
    "async def a_spread() -> dict:\n    return {'x': 1}\n\n"
    "@context('a_rows')\n"
    "async def a_rows():\n    return [1]\n\n"
    "@context('b_spread')\n"
    "def b_spread(x=None):\n    return x\n\n"
    "@context('b_count')\n"
    "def b_count(total=Context(lambda a_rows: len(a_rows))):\n    return total\n\n"
    "template = 'ok'\n"
)

_ASYNC_ONLY = (
    "from next.pages import context\n\n"
    "@context('a_rows')\n"
    "async def a_rows():\n    return [1]\n\n"
    "@context('b_count')\n"
    "async def b_count(a_rows):\n    return len(a_rows)\n\n"
    "template = 'ok'\n"
)

_CYCLING = (
    "from next.deps import Depends\n"
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "async def rows(value=Depends('loop_a')):\n    return value\n\n"
    "@context('plain')\n"
    "def plain():\n    return 1\n\n"
    "template = 'ok'\n"
)


async def _loop_a(value=_LOOP_B) -> object:
    return value


async def _loop_b(value=_LOOP_A) -> object:
    return value


async def _leaf() -> int:
    return 1


@pytest.mark.usefixtures("framework_providers")
class TestAsyncContextReads:
    """next.W163 flags a sync `@context` waiting on an async one it reads by name."""

    def test_a_sync_reader_of_an_async_key_is_flagged(self, tmp_path: Path) -> None:
        page_file = write_page(tmp_path, "", _SERIALISING)

        with patch_checks_route_manager(pages_directory=tmp_path):
            messages = check_async_context_reads()

        assert [(m.id, m.obj) for m in messages] == [("next.W163", str(page_file))]
        assert "'b_count'" in messages[0].msg
        assert "a_rows" in messages[0].msg

    @pytest.mark.parametrize("source", [_SERIAL, _INDIRECT], ids=["serial", "indirect"])
    def test_a_read_that_loses_no_overlap_is_not_flagged(
        self, tmp_path: Path, source: str
    ) -> None:
        write_page(tmp_path, "", source)

        with patch_checks_route_manager(pages_directory=tmp_path):
            assert check_async_context_reads() == []

    def test_an_async_reader_is_not_flagged(self, tmp_path: Path) -> None:
        write_page(tmp_path, "", _ASYNC_ONLY)

        with patch_checks_route_manager(pages_directory=tmp_path):
            assert check_async_context_reads() == []

    def test_a_sync_page_is_not_flagged(self, tmp_path: Path) -> None:
        write_page(tmp_path, "", _ORG + "\ntemplate = 'ok'\n")

        with patch_checks_route_manager(pages_directory=tmp_path):
            assert check_async_context_reads() == []


class TestAsyncDependencyCycles:
    """next.W168 flags an async `@context` reaching a cycle of named dependencies."""

    def test_a_cycle_is_flagged_without_running_anything(self, tmp_path: Path) -> None:
        page_file = write_page(tmp_path, "", _CYCLING)

        with (
            bound_dependency("loop_a", _loop_a),
            bound_dependency("loop_b", _loop_b),
            patch_checks_route_manager(pages_directory=tmp_path),
        ):
            messages = check_async_dependency_cycles()

        assert [(m.id, m.obj) for m in messages] == [("next.W168", str(page_file))]
        assert "'rows'" in messages[0].msg

    def test_an_acyclic_dependency_is_not_flagged(self, tmp_path: Path) -> None:
        write_page(tmp_path, "", _CYCLING)

        with (
            bound_dependency("loop_a", _leaf),
            patch_checks_route_manager(pages_directory=tmp_path),
        ):
            assert check_async_dependency_cycles() == []
