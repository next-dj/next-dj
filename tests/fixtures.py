from collections.abc import Generator
from pathlib import Path

import pytest
from django.core.exceptions import DisallowedRedirect
from django.http import HttpRequest, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template.engine import Engine
from django.test import override_settings
from django.utils.encoding import iri_to_uri

from next.checks import reset_check_caches
from next.conf import NextFrameworkSettings, next_framework_settings
from next.forms import uid
from next.forms.wizard import SessionFormWizardBackend, wizard_backend_manager
from next.pages import Page
from next.pages.loaders import DjxTemplateLoader, PythonTemplateLoader
from next.pages.registry import PageContextRegistry
from next.ports import partial_shaper_slot
from next.server import NextStatReloader
from next.urls import URLPatternParser
from tests.support import (
    CountingWizardBackend,
    IntentOnlyShaper,
    build_mock_http_request,
    tick_scenario,
)


_REDIRECT_CAP = 16384


class _CappedRedirect(HttpResponseRedirect):
    """Refuse an encoded Location past the cap, as Django 5.2.9 and later do."""

    def __init__(self, redirect_to: str, **kwargs: object) -> None:
        if len(iri_to_uri(redirect_to)) > _REDIRECT_CAP:
            msg = f"Unsafe redirect exceeding {_REDIRECT_CAP} characters"
            raise DisallowedRedirect(msg)
        super().__init__(redirect_to, **kwargs)


@pytest.fixture()
def cap_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the shared redirect helper Django's Location cap on every version."""
    monkeypatch.setattr(uid, "HttpResponseRedirect", _CappedRedirect)


@pytest.fixture()
def mock_http_request():
    """Return the ``build_mock_http_request`` callable for injecting mock requests."""
    return build_mock_http_request


@pytest.fixture(autouse=True)
def _reload_next_framework_settings_after_test() -> Generator[None, None, None]:
    """Reload the global ``next_framework_settings`` after each test (teardown only)."""
    yield
    next_framework_settings.reload()


@pytest.fixture(autouse=True)
def _reset_check_caches() -> Generator[None, None, None]:
    """Drop the per-run check caches and the page module memo around each test.

    A leaked ``_FAILED_PATHS`` would arm the fail-loud probe of every later test.
    """
    reset_check_caches()
    yield
    reset_check_caches()


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
    return PageContextRegistry()


@pytest.fixture()
def test_file_path():
    """Create a test file path for render tests."""
    return Path("/test/path/page.py")


@pytest.fixture()
def global_file_path():
    """Create a file path for global page tests."""
    return Path("/test/global/page.py")


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
def reloader_tick_scenario(request):
    """Run the reloader tick scenario named by the indirect param."""
    name = request.param
    reloader = NextStatReloader()
    with tick_scenario(name, reloader) as payload:
        yield reloader, payload


@pytest.fixture()
def intent_only_shaper():
    """Bind a shaper that refuses to shape, restore the real one after.

    The slot is process-global, so the previous implementation is put back, not dropped.
    """
    bound = partial_shaper_slot.get()
    shaper = IntentOnlyShaper()
    partial_shaper_slot.set(shaper)
    try:
        yield shaper
    finally:
        partial_shaper_slot.set(bound)


@pytest.fixture()
def counting_wizard_backend() -> Generator[CountingWizardBackend, None, None]:
    """Count wizard backend round-trips, putting the cached backend back after.

    The manager caches its backend in the instance dict, so the entry found on entry
    is restored rather than dropped by a `reset()` the next test would pay for.
    """
    cached = wizard_backend_manager.__dict__.get("_backend")
    had_backend = "_backend" in wizard_backend_manager.__dict__
    counting = CountingWizardBackend(SessionFormWizardBackend({}))
    wizard_backend_manager._backend = counting
    try:
        yield counting
    finally:
        if had_backend:
            wizard_backend_manager._backend = cached
        else:
            wizard_backend_manager.reset()
