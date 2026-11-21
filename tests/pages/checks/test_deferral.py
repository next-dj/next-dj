from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.checks import run_checks

from next.checks import NEXT
from next.components.deferral import defer_declarations
from next.components.loading import ModuleLoader
from next.pages.checks.deferral import check_boundary_declarations
from next.pages.compression import compress_declarations
from next.pages.deferral import page_defer_declarations
from next.pages.loaders import load_python_module_memo


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    page_defer_declarations.reset()
    defer_declarations.reset()
    compress_declarations.reset()
    yield
    page_defer_declarations.reset()
    defer_declarations.reset()
    compress_declarations.reset()


def _page_module(directory: Path, body: str, name: str = "page.py") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / name
    file_path.write_text(f"from next.pages import page\n\n{body}\n")
    load_python_module_memo(file_path)
    return file_path


def _component_module(directory: Path, body: str, name: str = "component.py") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    file_path = directory / name
    file_path.write_text(f"from next.components import component\n\n{body}\n")
    ModuleLoader().load(file_path)
    return file_path


class TestBoundaryDeclarations:
    """`check_boundary_declarations` reports a boundary declaration that applies nothing."""

    def test_silent_on_usable_declarations(self, tmp_path: Path) -> None:
        _page_module(tmp_path / "page", "page.defer(timeout='2s')")
        _component_module(tmp_path / "card", "component.defer('visible')")
        assert check_boundary_declarations() == []

    def test_an_unusable_page_declaration_yields_e222(self, tmp_path: Path) -> None:
        page_file = _page_module(tmp_path, "page.defer(timeout=0)")
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg == (
            f"page.defer() in {page_file} declares nothing, since timeout must be "
            "above zero, None leaves the boundary unbounded. The page keeps the "
            "default options of its boundary."
        )

    @pytest.mark.parametrize("name", ["component.py", "helpers.py"])
    def test_a_page_call_outside_a_page_module_yields_e222(
        self, tmp_path: Path, name: str
    ) -> None:
        source = _page_module(tmp_path, "page.defer()", name=name)
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg == (
            f"page.defer() in {source} declares nothing, since the call sits outside "
            "a page.py and so names no page."
        )
        assert (
            message.hint == "Call it at the top of the page.py of the page it is for."
        )

    def test_a_usable_compress_call_is_silent(self, tmp_path: Path) -> None:
        _page_module(tmp_path, "page.compress(False)")
        assert check_boundary_declarations() == []

    def test_an_unusable_compress_call_yields_e222(self, tmp_path: Path) -> None:
        page_file = _page_module(tmp_path, "page.compress('off')")
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg == (
            f"page.compress() in {page_file} declares nothing, since enabled is "
            "'off', which is no bool. The page is compressed as COMPRESSION decides."
        )

    def test_a_compress_call_outside_a_page_module_yields_e222(
        self, tmp_path: Path
    ) -> None:
        source = _page_module(tmp_path, "page.compress(False)", name="helpers.py")
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg.startswith(f"page.compress() in {source} declares nothing")

    def test_an_unusable_component_declaration_yields_e222(
        self, tmp_path: Path
    ) -> None:
        module_path = _component_module(tmp_path / "card", "component.props('class')")
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg == (
            f"The boundary declaration in {module_path} declares nothing, since a "
            "prop is named by a Python identifier, got 'class'. The component keeps "
            "the boundary its files give it."
        )

    def test_a_component_call_outside_a_component_module_yields_e222(
        self, tmp_path: Path
    ) -> None:
        source = _component_module(tmp_path, "component.defer()", name="helpers.py")
        [message] = check_boundary_declarations()
        assert message.id == "next.E222"
        assert message.msg == (
            f"component.defer() in {source} declares nothing, since the call sits "
            "outside a component.py and so names no component."
        )

    def test_registered_check_reaches_manage_py_check_path(
        self, tmp_path: Path
    ) -> None:
        _page_module(tmp_path, "page.defer(parallel=1)")
        messages = run_checks(tags=[NEXT])
        assert any(m.id == "next.E222" for m in messages)
