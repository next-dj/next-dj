from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from next.components.defer import defer_declarations
from next.components.loading import ModuleLoader
from next.pages.checks.declarations import check_boundary_declarations
from next.pages.loaders import load_python_module_memo


@pytest.fixture(autouse=True)
def _fresh_declarations(fresh_declarations: None) -> Iterator[None]:
    defer_declarations.reset()
    yield
    defer_declarations.reset()


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


class TestPageDeferDeclarations:
    """`check_boundary_declarations` reports a boundary declaration that applies nothing."""

    def test_silent_on_usable_declarations(self, tmp_path: Path) -> None:
        _page_module(tmp_path / "page", "page.defer(timeout='2s')")
        _component_module(tmp_path / "card", "component.defer('visible')")
        assert check_boundary_declarations() == []

    @pytest.mark.parametrize(
        ("write", "directory", "body", "name", "message"),
        [
            pytest.param(
                _page_module,
                "",
                "page.defer(timeout=0)",
                "page.py",
                "page.defer() in {file} declares nothing, since timeout must be "
                "above zero, None leaves the boundary unbounded. The page keeps the "
                "default options of its boundary.",
                id="page-unusable",
            ),
            pytest.param(
                _page_module,
                "",
                "page.compress('off')",
                "page.py",
                "page.compress() in {file} declares nothing, since enabled is "
                "'off', which is no bool. The page is compressed as COMPRESSION decides.",
                id="compress-unusable",
            ),
            pytest.param(
                _component_module,
                "card",
                "component.props('class')",
                "component.py",
                "The boundary declaration in {file} declares nothing, since a "
                "prop is named by a Python identifier, got 'class'. The component keeps "
                "the boundary its files give it.",
                id="component-unusable",
            ),
            pytest.param(
                _page_module,
                "",
                "page.defer()",
                "component.py",
                "page.defer() in {file} declares nothing, since the call sits outside "
                "a page.py and so names no page.",
                id="page-outside-component-module",
            ),
            pytest.param(
                _page_module,
                "",
                "page.defer()",
                "helpers.py",
                "page.defer() in {file} declares nothing, since the call sits outside "
                "a page.py and so names no page.",
                id="page-outside-helper",
            ),
            pytest.param(
                _page_module,
                "",
                "page.compress(False)",
                "helpers.py",
                "page.compress() in {file} declares nothing, since the call sits "
                "outside a page.py and so names no page.",
                id="compress-outside",
            ),
            pytest.param(
                _component_module,
                "",
                "component.defer()",
                "helpers.py",
                "component.defer() in {file} declares nothing, since the call sits "
                "outside a component.py and so names no component.",
                id="component-outside",
            ),
        ],
    )
    def test_a_declaration_that_applies_nothing_yields_e222(
        self,
        tmp_path: Path,
        write: Callable[..., Path],
        directory: str,
        body: str,
        name: str,
        message: str,
    ) -> None:
        source = write(tmp_path / directory, body, name=name)
        [found] = check_boundary_declarations()
        assert found.id == "next.E222"
        assert found.msg == message.format(file=source)

    def test_a_page_call_outside_a_page_module_hints_at_the_page(
        self, tmp_path: Path
    ) -> None:
        _page_module(tmp_path, "page.defer()", name="helpers.py")
        [message] = check_boundary_declarations()
        assert message.hint == (
            "Call page.defer() at the top of the page.py of the page it is for."
        )

    def test_a_usable_compress_call_is_silent(self, tmp_path: Path) -> None:
        _page_module(tmp_path, "page.compress(False)")
        assert check_boundary_declarations() == []
