from collections.abc import Generator
from pathlib import Path

import pytest
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.template.engine import Engine
from django.test import Client, override_settings

from next.conf import NextFrameworkSettings, next_framework_settings
from next.pages import Page
from next.pages.loaders import (
    DjxTemplateLoader,
    PythonTemplateLoader,
    reset_module_memo,
)
from next.pages.registry import PageContextRegistry
from next.ports import partial_shaper_slot
from next.server import NextStatReloader
from next.urls import URLPatternParser
from tests.support import (
    IntentOnlyShaper,
    _full_resolver,
    _minimal_resolver,
    _resolver_with_form,
    build_mock_http_request,
    named_temp_py,
    tick_scenario,
)


@pytest.fixture()
def mock_http_request():
    """Return the ``build_mock_http_request`` callable for injecting mock requests."""
    return build_mock_http_request


@pytest.fixture()
def client():
    """Django test client for HTTP requests."""
    return Client()


@pytest.fixture(autouse=True)
def _reload_next_framework_settings_after_test() -> Generator[None, None, None]:
    """Reload the global ``next_framework_settings`` after each test (teardown only)."""
    yield
    next_framework_settings.reload()


@pytest.fixture(autouse=True)
def _reset_page_module_memo() -> Generator[None, None, None]:
    """Reset the module memo and recorded import errors around each test.

    A leaked ``_LAST_LOAD_ERROR`` entry would keep the unified view's
    fast-path guard engaged for every later test in the session.
    """
    reset_module_memo()
    yield
    reset_module_memo()


@pytest.fixture()
def fresh_next_framework_settings() -> NextFrameworkSettings:
    """Return a new ``NextFrameworkSettings`` (separate merge cache from globals)."""
    return NextFrameworkSettings()


@pytest.fixture()
def page_instance():
    """Create a fresh Page instance for each test."""
    return Page()


@pytest.fixture()
def watched_template_edits() -> Generator[None, None, None]:
    """Run the body with the dev-loop staleness checks engaged.

    The suite runs with ``DEBUG`` off, so a test about picking an edit up
    without a restart asks for the dev setting by name.
    """
    with override_settings(DEBUG=True):
        yield


@pytest.fixture()
def url_parser():
    """Create a URLPatternParser instance for testing."""
    return URLPatternParser()


@pytest.fixture()
def python_template_loader():
    """Create a PythonTemplateLoader instance for testing."""
    return PythonTemplateLoader()


@pytest.fixture()
def djx_template_loader():
    """Create a DjxTemplateLoader instance for testing."""
    return DjxTemplateLoader()


@pytest.fixture()
def context_manager():
    """Create a PageContextRegistry instance for testing."""
    return PageContextRegistry(None)


@pytest.fixture()
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture()
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


@pytest.fixture()
def temp_python_file():
    """Create a temporary Python file for testing."""
    with named_temp_py('template = "test template"') as path:
        yield path


@pytest.fixture()
def form_engine():
    """Template engine with forms builtin."""
    return Engine(builtins=["next.templatetags.forms"])


@pytest.fixture()
def csrf_request():
    """HttpRequest with CSRF token set (for form tag tests)."""
    req = HttpRequest()
    req.method = "GET"
    get_token(req)
    return req


@pytest.fixture()
def dependency_resolver(request):
    """Build a ``DependencyResolver`` for the ``minimal``, ``with_form``, or ``full`` param."""
    kind = getattr(request, "param", "minimal")
    factories = {
        "minimal": _minimal_resolver,
        "with_form": _resolver_with_form,
        "full": _full_resolver,
    }
    return factories[kind]()


@pytest.fixture()
def reloader_tick_scenario(request):
    """Run the reloader tick scenario named by the indirect param."""
    name = request.param
    reloader = NextStatReloader()
    with tick_scenario(name, reloader) as payload:
        yield reloader, payload


@pytest.fixture()
def intent_only_shaper():
    """Bind a shaper that refuses to shape, restore the real one after.

    The slot is process-global, so the previously bound implementation is
    captured and put back rather than dropped.
    """
    bound = partial_shaper_slot.get()
    shaper = IntentOnlyShaper()
    partial_shaper_slot.set(shaper)
    try:
        yield shaper
    finally:
        partial_shaper_slot.set(bound)
