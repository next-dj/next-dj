from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from next.forms import Form
from next.forms.manager import form_action_manager
from next.pages.loaders import _load_python_module


if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


_ROUTER_PAGE_SOURCE = "from next.forms import Form\n\n\n" + "\n".join(
    f"class BenchRouted{i}(Form):\n    pass\n" for i in range(20)
)

_IMPORTED_MODULE_NAME = "bench_imported_forms"
_IMPORTED_MODULE_SOURCE = "from next.forms import Form\n\n\n" + "\n".join(
    f"class BenchImported{i}(Form):\n    pass\n" for i in range(20)
)


@pytest.fixture()
def _restore_action_registry():
    """Snapshot and restore the default backend registry around the bench."""
    backend = form_action_manager.default_backend
    registry = dict(backend._registry)
    uid_to_name = dict(backend._uid_to_name)
    name_index = dict(backend._name_index)
    url_cache = dict(backend._url_cache)
    yield
    backend._registry.clear()
    backend._registry.update(registry)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_to_name)
    backend._name_index.clear()
    backend._name_index.update(name_index)
    backend._url_cache.clear()
    backend._url_cache.update(url_cache)


@pytest.fixture()
def router_page_file(settings, tmp_path: Path) -> Path:
    """Write a `page.py` inside a BASE_DIR the registration gate accepts."""
    settings.BASE_DIR = tmp_path
    page_file = tmp_path / "page.py"
    page_file.write_text(_ROUTER_PAGE_SOURCE, encoding="utf-8")
    return page_file


@pytest.fixture()
def imported_forms_module(settings, tmp_path: Path) -> Iterator[ModuleType]:
    """Import a forms module normally, so it owns a `sys.modules` entry."""
    settings.BASE_DIR = tmp_path
    (tmp_path / f"{_IMPORTED_MODULE_NAME}.py").write_text(
        _IMPORTED_MODULE_SOURCE, encoding="utf-8"
    )
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module(_IMPORTED_MODULE_NAME)
    finally:
        sys.modules.pop(_IMPORTED_MODULE_NAME, None)
        sys.path.remove(str(tmp_path))


class TestBenchFormRegistration:
    """Import-time cost of declaring auto-registered Form subclasses.

    Each declaration runs `__init_subclass__`, the registration gate and
    `_definition_file_of`. Class names repeat on every round, so the registry
    overwrites in place and stays size-stable.
    """

    @pytest.mark.benchmark(group="forms.registration")
    @pytest.mark.usefixtures("_restore_action_registry")
    def test_define_form_subclasses(self, benchmark) -> None:
        """20 subclasses built straight through the metaclass."""
        metaclass = type(Form)

        def run() -> None:
            for i in range(20):
                metaclass(f"BenchDeclared{i}", (Form,), {})

        benchmark(run)

        backend = form_action_manager.default_backend
        assert backend.get_meta("bench_declared0") is not None

    @pytest.mark.benchmark(group="forms.registration")
    @pytest.mark.usefixtures("_restore_action_registry")
    def test_define_form_subclasses_in_router_module(
        self, benchmark, router_page_file: Path
    ) -> None:
        """20 subclasses in a page.py the router execs, attributed via the stack."""

        def run() -> None:
            _load_python_module(router_page_file)

        benchmark(run)

        backend = form_action_manager.default_backend
        assert backend.get_meta("bench_routed0", str(router_page_file)) is not None

    @pytest.mark.benchmark(group="forms.registration")
    @pytest.mark.usefixtures("_restore_action_registry")
    def test_define_form_subclasses_in_imported_module(
        self, benchmark, imported_forms_module: ModuleType
    ) -> None:
        """20 subclasses in an imported module, attributed via inspect."""

        def run() -> None:
            importlib.reload(imported_forms_module)

        benchmark(run)

        backend = form_action_manager.default_backend
        meta = backend.get_meta("bench_imported0")
        assert meta is not None
        assert meta["file_path"] == str(Path(imported_forms_module.__file__).resolve())
