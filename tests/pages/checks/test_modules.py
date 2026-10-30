from pathlib import Path
from unittest.mock import patch

import pytest

from next.checks import (
    check_async_callables,
    check_component_module_imports,
    check_page_module_imports,
)
from next.components import ComponentInfo, FileComponentsBackend
from next.components.loading import ModuleLoader
from tests.support import patch_checks_components_manager, patch_checks_router_manager


_ASYNC_PAGES = [
    pytest.param(
        "async def render():\n    return 'hi'\n", 'page.py render "render"', id="render"
    ),
    pytest.param(
        "from next.pages import context\n\n"
        "@context('rows')\n"
        "async def rows():\n    return [1]\n\n"
        "template = 'ok'\n",
        '@context callable "rows"',
        id="context",
    ),
    pytest.param(
        "from next.forms import action\n\n"
        "@action('archive')\n"
        "async def archive(request):\n    return None\n\n"
        "template = 'ok'\n",
        '@action handler "archive"',
        id="action",
    ),
    pytest.param(
        "from next.forms import Form, action\n\n"
        "class ContactForm(Form):\n"
        "    class Meta:\n        abstract = True\n\n"
        "    async def on_valid(self, request):\n        return None\n\n"
        "@action('contact', form_class=ContactForm)\n"
        "def contact(request):\n    return None\n\n"
        "template = 'ok'\n",
        'ContactForm hook "on_valid"',
        id="form-hook",
    ),
]


@pytest.fixture()
def _no_components():
    with patch("next.pages.checks.modules.import_component_modules", return_value=()):
        yield


@pytest.mark.usefixtures("_no_components")
class TestAsyncPageCallables:
    """`next.E218` names the async callable that stopped a `page.py` from loading."""

    @pytest.mark.parametrize(("source", "named"), _ASYNC_PAGES)
    def test_async_callable_reports_e218(
        self, tmp_path: Path, source: str, named: str
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(source)

        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_async_callables()
            imports = check_page_module_imports()

        assert [(m.id, m.obj) for m in messages] == [("next.E218", str(page_file))]
        assert named in messages[0].msg
        assert imports == []

    def test_sync_page_reports_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "page.py").write_text("def render():\n    return 'hi'\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_async_callables() == []

    def test_other_import_failure_is_left_to_e017(self, tmp_path: Path) -> None:
        (tmp_path / "page.py").write_text("raise RuntimeError('boom')\n")

        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_async_callables() == []
            assert [m.id for m in check_page_module_imports()] == ["next.E017"]

    def test_no_router_manager_reports_nothing(self) -> None:
        with patch(
            "next.pages.checks.modules.get_router_manager", return_value=(None, [])
        ):
            assert check_async_callables() == []


class TestAsyncComponentCallables:
    """`next.E218` names the async callable that stopped a `component.py`."""

    @pytest.mark.parametrize(
        ("source", "named"),
        [
            pytest.param(
                "async def render():\n    return 'hi'\n",
                'component.py render "render"',
                id="render",
            ),
            pytest.param(
                "from next.components import context\n\n"
                "@context('greeting')\n"
                "async def greeting():\n    return 'hi'\n",
                '@component.context callable "greeting"',
                id="context",
            ),
        ],
    )
    def test_async_callable_reports_e218(
        self, tmp_path: Path, source: str, named: str
    ) -> None:
        module_path = tmp_path / "component.py"
        module_path.write_text(source)
        ModuleLoader().load(module_path)
        backend = FileComponentsBackend({"DIRS": [], "COMPONENTS_DIR": "_components"})
        backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        backend._loaded = True

        with (
            patch(
                "next.pages.checks.modules.get_router_manager", return_value=(None, [])
            ),
            patch(
                "next.pages.checks.modules.import_component_modules",
                return_value=(module_path,),
            ),
            patch_checks_components_manager(backend),
        ):
            messages = check_async_callables()
            imports = check_component_module_imports()

        assert [(m.id, m.obj) for m in messages] == [("next.E218", str(module_path))]
        assert named in messages[0].msg
        assert imports == []
