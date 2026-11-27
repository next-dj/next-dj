from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from next.components.defer import defer_declarations
from next.components.modules import ModuleLoader
from next.defer.checks.declarations import (
    check_boundary_declarations,
    check_idle_page_defer,
)
from next.pages.declarations import page_declarations
from next.pages.loaders import load_python_module_memo
from tests.support.checks import run_check, write_files


@pytest.fixture(autouse=True)
def _fresh_declarations() -> Iterator[None]:
    page_declarations.reset()
    defer_declarations.reset()
    yield
    page_declarations.reset()
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


class TestBoundaryDeclarations:
    """`check_boundary_declarations` reports a boundary declaration off its module."""

    def test_silent_on_usable_declarations(self, tmp_path: Path) -> None:
        _page_module(tmp_path / "page", "page.defer(timeout='2s')")
        _component_module(tmp_path / "card", "component.defer('visible')")
        assert check_boundary_declarations() == []

    def test_a_misplaced_compress_call_is_left_to_compression(
        self, tmp_path: Path
    ) -> None:
        _page_module(tmp_path, "page.compress(enabled=False)", name="helpers.py")
        assert check_boundary_declarations() == []

    @pytest.mark.parametrize(
        ("write", "body", "name", "message"),
        [
            pytest.param(
                _page_module,
                "page.defer()",
                "component.py",
                "page.defer() in {file} declares nothing, since the call sits outside "
                "a page.py and so names no page.",
                id="page-outside-component-module",
            ),
            pytest.param(
                _page_module,
                "page.defer()",
                "helpers.py",
                "page.defer() in {file} declares nothing, since the call sits outside "
                "a page.py and so names no page.",
                id="page-outside-helper",
            ),
            pytest.param(
                _component_module,
                "component.defer()",
                "helpers.py",
                "component.defer() in {file} declares nothing, since the call sits "
                "outside a component.py and so names no component.",
                id="component-outside",
            ),
        ],
    )
    def test_a_misplaced_declaration_yields_e222(
        self,
        tmp_path: Path,
        write: Callable[..., Path],
        body: str,
        name: str,
        message: str,
    ) -> None:
        source = write(tmp_path, body, name=name)
        [found] = check_boundary_declarations()
        assert found.id == "next.E222"
        assert found.msg == message.format(file=source)
        assert found.obj == str(source)

    def test_a_page_call_outside_a_page_module_hints_at_the_page(
        self, tmp_path: Path
    ) -> None:
        _page_module(tmp_path, "page.defer()", name="helpers.py")
        [message] = check_boundary_declarations()
        assert message.hint == (
            "Call page.defer() at the top of the page.py of the page it is for."
        )


_DEFERRED_PAGE = "from next import page\n\npage.public()\npage.defer(timeout='2s')\n"


class TestIdlePageDefer:
    """`next.W190` warns about a `page.defer()` no `loading.djx` puts to use."""

    @pytest.mark.parametrize(
        ("files", "flagged"),
        [
            pytest.param({}, True, id="no-loading-ui"),
            pytest.param({"home/loading.djx": "<i>wait</i>"}, False, id="own"),
            pytest.param({"loading.djx": "<i>wait</i>"}, False, id="above"),
            pytest.param(
                {"home/page.py": "from next import page\n\npage.public()\n"},
                False,
                id="no-call",
            ),
        ],
    )
    def test_a_page_defer_needs_a_loading_ui(
        self, tmp_path: Path, files: dict[str, str], *, flagged: bool
    ) -> None:
        root = tmp_path / "pages"
        write_files(
            root,
            {
                "layout.djx": "<html><body>{% template %}</body></html>",
                "home/page.py": _DEFERRED_PAGE,
                "home/template.djx": "<p>home</p>",
                **files,
            },
        )
        messages = run_check(check_idle_page_defer, root)
        expected = [("next.W190", str(root / "home" / "page.py"))] if flagged else []
        assert [(m.id, m.obj) for m in messages] == expected
