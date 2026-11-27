import time
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import DisallowedRedirect
from django.db import connection
from django.db.models import Model
from django.http import HttpRequest, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template.engine import Engine
from django.test import override_settings
from django.utils.encoding import iri_to_uri

from next import stamps, utils
from next.bridge.loop import BackgroundLoop, background_loop
from next.components import registry as registry_module, tags as component_tags
from next.components.context import component
from next.components.registry import DeferredComponents
from next.conf import NextFrameworkSettings, next_framework_settings
from next.deps.objects import forget_object_rows
from next.diagnostics import reset_failure_logs, watch_degraded
from next.forms.wizard import SessionFormWizardBackend, wizard_backend_manager
from next.intercept import intercepts
from next.pages import Page, manager as manager_module
from next.pages.declarations import page_declarations
from next.pages.loaders import DjxTemplateLoader, PythonTemplateLoader
from next.pages.metadata.hreflang import forget_translated_urls
from next.pages.registry import PageContextRegistry
from next.permissions.access import _listed_middleware, login_by_default
from next.permissions.manager import PermissionManager
from next.server import NextStatReloader
from next.testing import override_next_settings, reset_check_caches
from next.urls import URLPatternParser
from tests.support import (
    OBJECT_PERMISSION_BACKENDS,
    TICK_NS,
    CountingWizardBackend,
    build_mock_http_request,
    install_visibility_hooks,
    isolated_form_registries,
    restored_permissions,
    tags,
    tick_scenario,
)
from tests.support.access import restored_declarations
from tests.support.leaks import Snapshot, leaked, snapshot
from tests.support.navigation import layout_tree


_REDIRECT_CAP = 16384


class _CappedRedirect(HttpResponseRedirect):
    """Refuse an encoded Location past the cap, as Django 5.2.9 and later do."""

    def __init__(self, redirect_to: str, **kwargs: object) -> None:
        if len(iri_to_uri(redirect_to)) > _REDIRECT_CAP:
            msg = f"Unsafe redirect exceeding {_REDIRECT_CAP} characters"
            raise DisallowedRedirect(msg)
        super().__init__(redirect_to, **kwargs)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Return an empty `pages` directory, the root of a page tree under test."""
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


@pytest.fixture()
def cap_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the shared redirect helpers Django's Location cap on every version."""
    monkeypatch.setattr(utils, "HttpResponseRedirect", _CappedRedirect)


@pytest.fixture()
def site_model(transactional_db) -> Iterator[type[Model]]:
    """Install `django.contrib.sites` with its table for the test, then drop both."""
    installed = [*settings.INSTALLED_APPS, "django.contrib.sites"]
    with override_settings(INSTALLED_APPS=installed):
        model = apps.get_model("sites", "Site")
        with connection.schema_editor() as editor:
            editor.create_model(model)
        try:
            yield model
        finally:
            model.objects.clear_cache()
            with connection.schema_editor() as editor:
                editor.delete_model(model)


@pytest.fixture()
def scoped_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the auth and contenttypes default managers a `visible_to` hook."""
    install_visibility_hooks(monkeypatch)


@pytest.fixture()
def mock_http_request():
    """Return the ``build_mock_http_request`` callable for injecting mock requests."""
    return build_mock_http_request


_SETTLED_LEAD_NS = 3600 * 10**9


_BEFORE = pytest.StashKey[Snapshot]()
_WIDER = pytest.StashKey[bool]()
_SHIFTING = pytest.StashKey[set[int]]()


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[object], request: pytest.FixtureRequest
) -> Generator[None, object, object]:
    """Note a fixture wider than one test whose setup moves the state tests share."""
    if fixturedef.scope == "function":
        return (yield)
    before = snapshot()
    result = yield
    if leaked(before, snapshot()):
        request.config.stash[_WIDER] = True
        request.config.stash.setdefault(_SHIFTING, set()).add(id(fixturedef))
    return result


def pytest_fixture_post_finalizer(
    fixturedef: pytest.FixtureDef[object], request: pytest.FixtureRequest
) -> None:
    """Note the teardown of a wider fixture whose setup moved the shared state."""
    if id(fixturedef) in request.config.stash.get(_SHIFTING, set()):
        request.config.stash[_WIDER] = True


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """Record the process state before the first fixture of the test sets up."""
    item.config.stash[_WIDER] = False
    before = snapshot()
    result = yield
    if not item.config.stash[_WIDER]:
        item.stash[_BEFORE] = before
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, None, None]:
    """Fail the test that leaves a Django setting or a port binding changed behind it.

    Under xdist the leak breaks an unrelated later test, so it is named at its source.
    """
    item.config.stash[_WIDER] = False
    result = yield
    before = item.stash.get(_BEFORE, None)
    if before is not None and not item.config.stash[_WIDER]:
        found = leaked(before, snapshot())
        if found:
            pytest.fail(f"The test left changed: {', '.join(found)}.")
    return result


@pytest.fixture(autouse=True)
def _settled_mtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the snapshot clock an hour ahead, so no mtime a test writes counts as racy.

    A test about the racy window pins the clock through the `tick` fixture instead.
    """
    monkeypatch.setattr(stamps, "time_ns", lambda: time.time_ns() + _SETTLED_LEAD_NS)


@pytest.fixture()
def tick(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Pin the snapshot clock to `TICK_NS` plus an indirect lead, moved by its item."""
    now = [TICK_NS + getattr(request, "param", 0)]
    monkeypatch.setattr(stamps, "time_ns", lambda: now[0])
    return now


@pytest.fixture(autouse=True)
def _reload_next_framework_settings_after_test() -> Generator[None, None, None]:
    """Reload the global ``next_framework_settings`` after each test (teardown only)."""
    yield
    next_framework_settings.reload()


@pytest.fixture(autouse=True)
def _drop_temporary_component_contexts(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Drop the context functions a test registered from a tree under its temp root.

    A leftover one is a callable every later check of the same worker still reads.
    """
    yield
    root = tmp_path_factory.getbasetemp().resolve()
    for registered in tuple(component.registry.registered()):
        if registered.is_relative_to(root):
            component.registry.unregister(registered)


@pytest.fixture(autouse=True)
def _reset_check_caches() -> Generator[None, None, None]:
    """Drop the per-run check caches and the page module memo around each test.

    A leaked ``_FAILED_PATHS`` would arm the fail-loud probe of every later test.
    """
    reset_check_caches()
    yield
    reset_check_caches()


@pytest.fixture(autouse=True)
def _reset_access_memos() -> Generator[None, None, None]:
    """Drop the login-default memos, since tests write MIDDLEWARE without a signal."""
    login_by_default.cache_clear()
    _listed_middleware.cache_clear()
    yield
    login_by_default.cache_clear()
    _listed_middleware.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_intercepts() -> Generator[None, None, None]:
    """Empty the intercept table around each test, since a router walk fills it."""
    intercepts.clear()
    yield
    intercepts.clear()


@pytest.fixture(autouse=True)
def _fresh_object_rows() -> Generator[None, None, None]:
    """Drop the `visible_to` memo, since tests patch and lift the manager hooks."""
    forget_object_rows()
    yield
    forget_object_rows()


@pytest.fixture(autouse=True)
def _fresh_translation_memo() -> Generator[None, None, None]:
    """Drop the process-wide hreflang memo so no test reads the URLconf of another."""
    forget_translated_urls()
    yield
    forget_translated_urls()


def _forget_app_holdings() -> None:
    utils._app_holding_cache.clear()
    utils._resolved_app_roots.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_app_holdings() -> Generator[None, None, None]:
    """Drop the app-holding memo, since tests patch the installed app list."""
    _forget_app_holdings()
    yield
    _forget_app_holdings()


@pytest.fixture(autouse=True)
def _rearmed_failure_logs() -> Generator[None, None, None]:
    """Start each test with every failure log re-armed, whatever ran before it."""
    reset_failure_logs()
    yield
    reset_failure_logs()


@pytest.fixture(autouse=True)
def _undegraded() -> Generator[None, None, None]:
    """Start each test with no degraded render, as a page view starts its own."""
    watch_degraded()
    yield
    watch_degraded()


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

    The suite runs with ``DEBUG`` off, so a test about edits asks for the dev setting.
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
def counting_wizard_backend() -> Generator[CountingWizardBackend, None, None]:
    """Count wizard backend round-trips, putting the cached backend back after.

    The manager caches its backend in the instance dict, so the entry is restored.
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


@pytest.fixture()
def isolated_permissions() -> Generator[PermissionManager, None, None]:
    """Yield the shared permission manager empty and restore its permissions after."""
    with restored_permissions() as manager:
        yield manager


@pytest.fixture()
def form_registries() -> Generator[None, None, None]:
    """Run the test over empty form action registries and restore them after."""
    with isolated_form_registries():
        yield


@pytest.fixture(autouse=True)
def _isolated_declarations() -> Generator[None, None, None]:
    """Run each test over no access declarations and restore the earlier ones after.

    A page module a test runs declares into the shared registry, read by later checks.
    """
    with restored_declarations():
        yield


@pytest.fixture()
def object_permission_backend() -> Generator[None, None, None]:
    """List `ObjectPermissionBackend` after `ModelBackend` for the test."""
    with override_settings(AUTHENTICATION_BACKENDS=OBJECT_PERMISSION_BACKENDS):
        yield


@pytest.fixture()
def guard_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let `{% load guard_tags %}` find the block tags of `tests.support.tags`."""
    engine = Engine.get_default()
    monkeypatch.setitem(engine.template_libraries, "guard_tags", tags.register)


@pytest.fixture()
def findable_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, str], str]:
    """Return a writer of templates the default engine finds by a test-unique name.

    The cached loader keeps a name across tests, so each name carries the tmp folder.
    """
    folder = tmp_path / "templates"
    folder.mkdir()
    engine = Engine.get_default()
    monkeypatch.setattr(engine, "dirs", [*engine.dirs, str(folder)])

    def write(name: str, text: str) -> str:
        full = f"{tmp_path.name}_{name}"
        (folder / full).write_text(text)
        return full

    return write


@pytest.fixture()
def fresh_deferred_components(monkeypatch: pytest.MonkeyPatch) -> DeferredComponents:
    """Track the deferred components of this test alone, as every reader sees them."""
    fresh = DeferredComponents()
    for module in (registry_module, manager_module, component_tags):
        monkeypatch.setattr(module, "deferred_components", fresh)
    return fresh


@pytest.fixture()
def router_on() -> Generator[None, None, None]:
    """Run the test with the client router enabled."""
    with override_next_settings(ROUTER={"ENABLED": True}):
        yield


@pytest.fixture()
def fresh_transaction_declarations() -> Generator[None, None, None]:
    """Start and end the test with no `page` declaration of any kind."""
    page_declarations.reset()
    yield
    page_declarations.reset()


@pytest.fixture()
def layout_pages(tmp_path: Path) -> dict[str, Path]:
    """Write a page tree under two layouts and return its `page.py` files by name."""
    return layout_tree(tmp_path / "pages")


@pytest.fixture(autouse=True)
def _stopped_background_loop() -> Iterator[None]:
    """Join the process loop a test started, so the next test starts without it."""
    before = background_loop.current()
    yield
    if background_loop.current() is not before:
        background_loop.stop()


@pytest.fixture()
def loop_owner() -> Iterator[BackgroundLoop]:
    """Return a loop owner of its own, stopped when the test ends."""
    held = BackgroundLoop()
    yield held
    held.stop()
