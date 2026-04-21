from __future__ import annotations

import inspect
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from django.http import HttpRequest

from next.conf import NextFrameworkSettings
from next.deps import DependencyResolver
from next.forms import FormProvider
from next.urls import DUrl, FileRouterBackend, HttpRequestProvider, UrlKwargsProvider


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from next.server import NextStatReloader


def build_mock_http_request(
    *, path: str | None = "/test/", **attrs: object
) -> MagicMock:
    """Return ``MagicMock(spec=HttpRequest)`` with optional ``path`` and attributes."""
    m = MagicMock(spec=HttpRequest)
    if path is not None:
        m.path = path
    for key, val in attrs.items():
        setattr(m, key, val)
    return m


def inspect_parameter(
    name: str,
    annotation: object = inspect.Parameter.empty,
    *,
    default: object = inspect.Parameter.empty,
) -> inspect.Parameter:
    """Build a positional-or-keyword parameter for provider unit tests."""
    return inspect.Parameter(
        name,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        default=default,
        annotation=annotation,
    )


@dataclass(frozen=True, slots=True)
class CoerceUrlValueCase:
    """One row for ``TestCoerceUrlValue`` (raw string, type hint, expected value)."""

    id: str
    raw: str
    hint: type
    expected: object


COERCE_URL_VALUE_CASES: tuple[CoerceUrlValueCase, ...] = (
    CoerceUrlValueCase("int_ok", "42", int, 42),
    CoerceUrlValueCase("int_bad", "x", int, "x"),
    CoerceUrlValueCase("bool_true", "true", bool, True),
    CoerceUrlValueCase("bool_one", "1", bool, True),
    CoerceUrlValueCase("bool_yes", "yes", bool, True),
    CoerceUrlValueCase("bool_zero", "0", bool, False),
    CoerceUrlValueCase("bool_false", "false", bool, False),
    CoerceUrlValueCase("float_ok", "3.14", float, 3.14),
    CoerceUrlValueCase("float_bad", "x", float, "x"),
    CoerceUrlValueCase("str_pass", "hello", str, "hello"),
)


@dataclass(frozen=True, slots=True)
class UrlKwargsResolveCase:
    """One row for ``UrlKwargsProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object


URL_KWARGS_RESOLVE_CASES: tuple[UrlKwargsResolveCase, ...] = (
    UrlKwargsResolveCase("int_match", "id", int, {"id": 42}, 42),
    UrlKwargsResolveCase("str_to_int", "id", int, {"id": "99"}, 99),
    UrlKwargsResolveCase(
        "no_annotation",
        "slug",
        inspect.Parameter.empty,
        {"slug": "hello"},
        "hello",
    ),
    UrlKwargsResolveCase(
        "int_conv_fail", "id", int, {"id": "not-a-number"}, "not-a-number"
    ),
    UrlKwargsResolveCase(
        "str_annot", "slug", str, {"slug": "hello-world"}, "hello-world"
    ),
    UrlKwargsResolveCase("missing_key", "missing", str, {"other": "value"}, None),
)


@dataclass(frozen=True, slots=True)
class UrlByAnnotationResolveCase:
    """One row for ``UrlByAnnotationProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object | None


URL_BY_ANNOTATION_RESOLVE_CASES: tuple[UrlByAnnotationResolveCase, ...] = (
    UrlByAnnotationResolveCase("coerce_int", "pk", DUrl[int], {"pk": "123"}, 123),
    UrlByAnnotationResolveCase(
        "str_slug", "slug", DUrl[str], {"slug": "hello"}, "hello"
    ),
    UrlByAnnotationResolveCase("missing_key", "missing", DUrl[str], {}, None),
)


def next_framework_settings_for_checks(*, backends: list) -> object:
    """Stand-in for ``next_framework_settings`` in checks tests."""
    return SimpleNamespace(
        DEFAULT_COMPONENT_BACKENDS=backends,
        DEFAULT_PAGE_BACKENDS=list(
            NextFrameworkSettings.DEFAULTS["DEFAULT_PAGE_BACKENDS"],
        ),
        URL_NAME_TEMPLATE=NextFrameworkSettings.DEFAULTS["URL_NAME_TEMPLATE"],
    )


def next_framework_settings_for_checks_backends_value(backends: object) -> object:
    """Stand-in when ``DEFAULT_COMPONENT_BACKENDS`` is not a list (or None)."""
    default_pages = list(NextFrameworkSettings.DEFAULTS["DEFAULT_PAGE_BACKENDS"])
    return SimpleNamespace(
        DEFAULT_COMPONENT_BACKENDS=backends,
        DEFAULT_PAGE_BACKENDS=default_pages,
        URL_NAME_TEMPLATE=NextFrameworkSettings.DEFAULTS["URL_NAME_TEMPLATE"],
    )


def next_framework_settings_component_backends_list(backends: object) -> object:
    """Patch stand-in with only ``DEFAULT_COMPONENT_BACKENDS`` (may be wrong type)."""
    return SimpleNamespace(DEFAULT_COMPONENT_BACKENDS=backends)


@contextmanager
def patch_checks_router_manager(
    *,
    pages_directory: Path,
    scan_routes: Iterable[tuple[str, Path]],
) -> Generator[tuple[MagicMock, MagicMock, MagicMock], None, None]:
    """Patch `get_router_manager` and `get_pages_directory` for page checks tests."""
    routes = list(scan_routes)
    mock_mgr = MagicMock()
    mock_router = MagicMock()
    mock_mgr._backends = [mock_router]
    mock_router.pages_dir = "pages"
    mock_router.app_dirs = True
    mock_router._scan_pages_directory.return_value = routes
    with (
        patch(
            "next.pages.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.urls.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.checks.common.get_pages_directory",
            return_value=pages_directory,
        ) as mock_get_pages_dir,
    ):
        yield mock_mgr, mock_router, mock_get_pages_dir


@contextmanager
def patch_checks_router_manager_with_routers(
    *,
    routers: list[object],
) -> Generator[MagicMock, None, None]:
    """Patch `get_router_manager` so the manager exposes the given routers list."""
    mock_mgr = MagicMock()
    mock_mgr._backends = list(routers)
    with (
        patch(
            "next.pages.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
        patch(
            "next.urls.checks.get_router_manager",
            return_value=(mock_mgr, []),
        ),
    ):
        yield mock_mgr


@contextmanager
def patch_checks_components_manager(
    *fake_backends: object,
) -> Generator[MagicMock, None, None]:
    """Patch components-check settings and `ComponentsManager` with fake backends."""
    mock_ns = next_framework_settings_for_checks(
        backends=[
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            },
        ],
    )
    with (
        patch("next.components.checks.next_framework_settings", mock_ns),
        patch("next.components.checks.ComponentsManager") as mock_manager_klass,
    ):
        mock_manager = mock_manager_klass.return_value
        mock_manager._reload_config = lambda: None
        mock_manager._backends = list(fake_backends)
        yield mock_manager


def _ctx(
    request=None,
    form=None,
    url_kwargs=None,
    context_data=None,
    cache=None,
    stack=None,
    resolver_inst=None,
    _context_data=None,
    **kwargs: object,
) -> SimpleNamespace:
    """Build dynamic context (SimpleNamespace) for provider tests."""
    if url_kwargs is None:
        reserved = {
            "request",
            "form",
            "context_data",
            "cache",
            "stack",
            "resolver",
            "_context_data",
        }
        url_kwargs = {k: v for k, v in kwargs.items() if k not in reserved}
    return SimpleNamespace(
        request=request,
        form=form,
        url_kwargs=url_kwargs or {},
        context_data=context_data or _context_data or {},
        cache=cache,
        stack=stack,
        resolver=resolver_inst,
    )


def _minimal_resolver() -> DependencyResolver:
    """Return a resolver with only HttpRequest and URL providers (for isolated tests)."""
    return DependencyResolver(HttpRequestProvider(), UrlKwargsProvider())


def _resolver_with_form() -> DependencyResolver:
    """Return a resolver with request, URL and form providers."""
    return DependencyResolver(
        HttpRequestProvider(),
        UrlKwargsProvider(),
        FormProvider(),
    )


def _full_resolver() -> DependencyResolver:
    """Return a resolver with all auto-registered providers (for callable dependency tests)."""
    return DependencyResolver()


@contextmanager
def route_watch_layer_patches(
    *,
    get_pages_directories_for_watch,
    scan_pages_tree,
):
    """Apply the usual ``next.server`` patches around route discovery for ``tick()`` tests."""
    with (
        patch(
            "next.server.autoreload.get_pages_directories_for_watch",
            get_pages_directories_for_watch,
        ),
        patch("next.server.autoreload.scan_pages_tree", scan_pages_tree),
    ):
        yield


@contextmanager
def tick_scenario_route_set_grows(reloader: NextStatReloader):
    """Watch dirs appear on the second call. Scan then returns a page when routes are ready."""
    fake_path = Path("/fake/pages/home/page.py")
    call_count = [0]

    def watch_side_effect():
        call_count[0] += 1
        return [] if call_count[0] == 1 else [Path("/fake/pages")]

    def scan_side_effect(pages_path):
        if call_count[0] < 2:
            return iter([])
        return iter([("home", pages_path / "home" / "page.py")])

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=watch_side_effect,
            scan_pages_tree=scan_side_effect,
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify, fake_path.resolve()


@contextmanager
def tick_scenario_no_notify_first_tick(reloader: NextStatReloader):
    """Watch and scan stay stable. The first tick must not notify."""
    fake_dir = Path("/fake")
    fake_page = fake_dir / "page.py"
    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=lambda: [fake_dir],
            scan_pages_tree=lambda _p: iter([("home", fake_page)]),
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify


@contextmanager
def tick_scenario_route_set_unchanged(reloader: NextStatReloader):
    """Keep the same routes on every tick so notify stays silent."""
    fake_dir = Path("/fake")
    fake_page = fake_dir / "page.py"

    def route_iter(_path):
        return iter([("home", fake_page)])

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=lambda: [fake_dir],
            scan_pages_tree=route_iter,
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify


@contextmanager
def tick_scenario_watch_raises(reloader: NextStatReloader):
    """If ``get_pages_directories_for_watch`` raises, the tick still runs."""
    with (
        patch(
            "next.server.autoreload.get_pages_directories_for_watch",
            side_effect=ValueError("bad"),
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
    ):
        yield


@contextmanager
def tick_scenario_mtime_change(reloader: NextStatReloader):
    """Snapshot mtime increases between ticks."""
    fake_path = Path("/fake/file.py")
    first_snapshot = [(fake_path, 1000.0)]
    second_snapshot = [(fake_path, 2000.0)]
    call_count = [0]

    def snapshot_side_effect():
        call_count[0] += 1
        return iter(first_snapshot if call_count[0] == 1 else second_snapshot)

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=list,
            scan_pages_tree=lambda _p: iter([]),
        ),
        patch.object(reloader, "snapshot_files", side_effect=snapshot_side_effect),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify, fake_path


TICK_SCENARIOS: dict[str, object] = {
    "route_set_grows": tick_scenario_route_set_grows,
    "no_notify_first_tick": tick_scenario_no_notify_first_tick,
    "route_set_unchanged": tick_scenario_route_set_unchanged,
    "watch_raises": tick_scenario_watch_raises,
    "mtime_change": tick_scenario_mtime_change,
}


@contextmanager
def tick_scenario(name: str, reloader: NextStatReloader):
    """Dispatch named ``tick()`` patch scenario (for tests and indirect fixtures)."""
    fn = TICK_SCENARIOS[name]
    with fn(reloader) as stack:
        yield stack


@contextmanager
def named_temp_py(content: str, *, suffix: str = ".py") -> Generator[Path, None, None]:
    """Write ``content`` to a named temp file and delete it after the block."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(content)
        path = Path(f.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def file_router_config_entry(
    *,
    pages_dir: Path | str | None = None,
    app_dirs: bool = False,
    dirs: list[object] | None = None,
    options: dict[str, object] | None = None,
) -> dict[str, object]:
    """One ``DEFAULT_PAGE_BACKENDS`` entry for ``FileRouterBackend`` in page/layout tests."""
    opts = dict(options or {})
    dirs_list: list[object] = list(dirs) if dirs is not None else []
    if pages_dir is not None and not app_dirs:
        dirs_list = [pages_dir, *dirs_list]
    return {
        "BACKEND": "next.urls.FileRouterBackend",
        "PAGES_DIR": "pages",
        "APP_DIRS": app_dirs,
        "DIRS": dirs_list,
        "OPTIONS": opts,
    }


def default_page_router_config(pages_dir: Path | str) -> list[dict[str, object]]:
    """Single-router list with ``DIRS`` containing ``pages_dir`` (``APP_DIRS`` false)."""
    return [file_router_config_entry(pages_dir=pages_dir)]


def file_router_backend_from_params(params: object) -> object:
    """Build FileRouterBackend from tuple params or return params unchanged."""
    if isinstance(params, tuple):
        if len(params) == 3:
            return FileRouterBackend(
                params[0],
                app_dirs=params[1],
                options=params[2],
            )
        if len(params) == 2:
            return FileRouterBackend(params[0], app_dirs=params[1])
        if len(params) == 1:
            return FileRouterBackend(params[0])
        return params
    return params


__all__ = [
    "COERCE_URL_VALUE_CASES",
    "TICK_SCENARIOS",
    "URL_BY_ANNOTATION_RESOLVE_CASES",
    "URL_KWARGS_RESOLVE_CASES",
    "CoerceUrlValueCase",
    "UrlByAnnotationResolveCase",
    "UrlKwargsResolveCase",
    "_ctx",
    "_full_resolver",
    "_minimal_resolver",
    "_resolver_with_form",
    "build_mock_http_request",
    "default_page_router_config",
    "file_router_backend_from_params",
    "file_router_config_entry",
    "inspect_parameter",
    "named_temp_py",
    "next_framework_settings_component_backends_list",
    "next_framework_settings_for_checks",
    "next_framework_settings_for_checks_backends_value",
    "patch_checks_components_manager",
    "patch_checks_router_manager",
    "patch_checks_router_manager_with_routers",
    "route_watch_layer_patches",
    "tick_scenario",
    "tick_scenario_mtime_change",
    "tick_scenario_no_notify_first_tick",
    "tick_scenario_route_set_grows",
    "tick_scenario_route_set_unchanged",
    "tick_scenario_watch_raises",
]
