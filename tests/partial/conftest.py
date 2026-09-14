from collections.abc import Generator
from pathlib import Path

import pytest

from next.forms.wizard import SessionFormWizardBackend, wizard_backend_manager
from next.pages.loaders import _load_python_module
from next.testing import NextClient
from tests.support import CountingWizardBackend, isolated_form_registries


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
def counting_wizard_backend() -> Generator[CountingWizardBackend, None, None]:
    """Install a counting wizard backend for the duration of the test."""
    counting = CountingWizardBackend(SessionFormWizardBackend({}))
    wizard_backend_manager._backend = counting
    yield counting
    wizard_backend_manager.reset()
