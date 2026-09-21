from collections.abc import Generator
from pathlib import Path

import pytest

from next.pages.loaders import _load_python_module
from next.partial.registry import PatchOpRegistry, patch_op_registry, register_patch_op
from next.testing import NextClient
from tests.support import isolated_form_registries


_PARTIAL_DIR = Path(__file__).resolve().parent
_REGRESSION_FORMS = _PARTIAL_DIR / "regression_forms.py"
_RESULT_FORMS = _PARTIAL_DIR / "result_forms.py"

_SITE_PAGES = _PARTIAL_DIR.parent / "site_pages"
_ZONED_PAGE = _SITE_PAGES / "zoned" / "page.py"
_BOARD_FORMS_PAGE = _SITE_PAGES / "board_forms" / "page.py"
_BOARD_SETTINGS_PAGE = _SITE_PAGES / "board_settings" / "page.py"
_WIZARD_PAGE = _SITE_PAGES / "wizard" / "[step]" / "page.py"
_WIZARD_PUSH_PAGE = _SITE_PAGES / "wizard_push" / "[step]" / "page.py"
_TAGZONE_PAGE = _SITE_PAGES / "tagzone" / "page.py"
_TAGWIZARD_PAGE = _SITE_PAGES / "tagwizard" / "[step]" / "page.py"

_PARTIAL_MODULES = (
    _REGRESSION_FORMS,
    _RESULT_FORMS,
    _ZONED_PAGE,
    _BOARD_FORMS_PAGE,
    _BOARD_SETTINGS_PAGE,
    _WIZARD_PAGE,
    _WIZARD_PUSH_PAGE,
    _TAGZONE_PAGE,
    _TAGWIZARD_PAGE,
)


@pytest.fixture(autouse=True)
def _partial_form_registries():
    """Register the partial-suite forms and restore the clean baseline after.

    The snapshot predates the partial modules, so teardown drops what they registered,
    and re-execution is idempotent because registration keys on the file path.
    """
    with isolated_form_registries():
        for module_path in _PARTIAL_MODULES:
            _load_python_module(module_path)
        yield


@pytest.fixture()
def next_client() -> NextClient:
    """Test client that submits form fields manually, without CSRF checks."""
    return NextClient(enforce_csrf_checks=False)


@pytest.fixture()
def restored_op_registry() -> Generator[PatchOpRegistry, None, None]:
    """Yield the process-global patch-op registry and put its records back after.

    Order, index, and version are restored together so a verb registered here cannot
    reach a later test through whichever of the three that test happens to read.
    """
    ordered = list(patch_op_registry._ordered)
    by_name = dict(patch_op_registry._by_name)
    version = patch_op_registry.version
    try:
        yield patch_op_registry
    finally:
        patch_op_registry._ordered = ordered
        patch_op_registry._by_name = by_name
        patch_op_registry._version = version


@pytest.fixture()
def custom_op(restored_op_registry: PatchOpRegistry) -> str:
    """Register a custom patch verb, dropped again when the registry is restored."""
    register_patch_op("confetti")
    return "confetti"
