from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.template.engine import Engine
from django.test import Client

from next.conf import NextFrameworkSettings, next_framework_settings
from next.pages import (
    ContextManager,
    DjxTemplateLoader,
    LayoutManager,
    Page,
    PythonTemplateLoader,
)
from next.urls import URLPatternParser
from next.utils import NextStatReloader
from tests.support import (
    _full_resolver,
    _minimal_resolver,
    _resolver_with_form,
    build_mock_http_request,
    named_temp_py,
    patch_checks_router_manager,
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


@pytest.fixture()
def fresh_next_framework_settings() -> NextFrameworkSettings:
    """Return a new ``NextFrameworkSettings`` (separate merge cache from globals)."""
    return NextFrameworkSettings()


@pytest.fixture()
def page_instance():
    """Create a fresh Page instance for each test."""
    return Page()


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
    """Create a ContextManager instance for testing."""
    return ContextManager()


@pytest.fixture()
def layout_manager():
    """Create a LayoutManager instance for testing."""
    return LayoutManager()


@pytest.fixture()
def mock_frame():
    """Mock inspect.currentframe for testing."""
    with patch("next.pages.inspect.currentframe") as mock_frame:
        yield mock_frame


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
def context_temp_file():
    """Create a temporary file for context decorator tests."""
    with named_temp_py("def test_func(): pass") as path:
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
    """Indirect fixture: ``DependencyResolver`` from ``next.deps`` (``minimal``, ``with_form``, or ``full``)."""
    kind = getattr(request, "param", "minimal")
    factories = {
        "minimal": _minimal_resolver,
        "with_form": _resolver_with_form,
        "full": _full_resolver,
    }
    return factories[kind]()


@pytest.fixture()
def reloader_tick_scenario(request):
    """Indirect: param is a key from ``tests.support.TICK_SCENARIOS``."""
    name = request.param
    reloader = NextStatReloader()
    with tick_scenario(name, reloader) as payload:
        yield reloader, payload


@pytest.fixture()
def checks_router_patch(request, tmp_path):
    """Indirect: ``request.param`` is ``list[tuple[str, Path]]`` routes for page checks mocks."""
    routes = request.param
    with patch_checks_router_manager(
        pages_directory=tmp_path,
        scan_routes=routes,
    ) as ctx:
        yield ctx
