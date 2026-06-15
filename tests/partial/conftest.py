import copy
from pathlib import Path

import pytest
from django.core.cache import cache

from next.forms.diagnostics import registration_diagnostics
from next.forms.manager import form_action_manager
from next.forms.wizard import wizard_backend_manager
from next.pages.loaders import _load_python_module


_PARTIAL_DIR = Path(__file__).resolve().parent
_REGRESSION_FORMS = _PARTIAL_DIR / "regression_forms.py"
_RESULT_FORMS = _PARTIAL_DIR / "result_forms.py"

_SITE_PAGES = _PARTIAL_DIR.parent / "site_pages"
_ZONED_PAGE = _SITE_PAGES / "zoned" / "page.py"
_BOARD_FORMS_PAGE = _SITE_PAGES / "board_forms" / "page.py"
_BOARD_SETTINGS_PAGE = _SITE_PAGES / "board_settings" / "page.py"
_WIZARD_PAGE = _SITE_PAGES / "wizard" / "[step]" / "page.py"
_WIZARD_PUSH_PAGE = _SITE_PAGES / "wizard_push" / "[step]" / "page.py"

_PARTIAL_MODULES = (
    _REGRESSION_FORMS,
    _RESULT_FORMS,
    _ZONED_PAGE,
    _BOARD_FORMS_PAGE,
    _BOARD_SETTINGS_PAGE,
    _WIZARD_PAGE,
    _WIZARD_PUSH_PAGE,
)


@pytest.fixture(autouse=True)
def _partial_form_registries():
    """Register the partial-suite forms and restore the clean baseline after.

    The snapshot is taken before the partial modules load, so the teardown
    drops every action and provider they registered. A later forms suite
    then sees the registry exactly as it was, not the partial fixtures.
    Re-execution each test is idempotent because registration keys on the
    file path.
    """
    backend = form_action_manager.default_backend
    registry_snapshot = copy.deepcopy(backend._registry)
    uid_snapshot = copy.deepcopy(backend._uid_to_name)
    name_index_snapshot = copy.deepcopy(backend._name_index)
    diagnostics_snapshot = registration_diagnostics.snapshot()

    wizard_backend_manager.reset()
    cache.clear()

    for module_path in _PARTIAL_MODULES:
        _load_python_module(module_path)

    yield

    backend._registry.clear()
    backend._registry.update(registry_snapshot)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_snapshot)
    backend._name_index.clear()
    backend._name_index.update(name_index_snapshot)

    registration_diagnostics.restore(diagnostics_snapshot)

    wizard_backend_manager.reset()
    cache.clear()
