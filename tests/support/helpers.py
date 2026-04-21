from __future__ import annotations

import inspect
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from django.http import HttpRequest

from next.conf import NextFrameworkSettings
from next.deps import DependencyResolver
from next.forms import FormProvider
from next.urls import FileRouterBackend, HttpRequestProvider, UrlKwargsProvider


if TYPE_CHECKING:
    from collections.abc import Generator


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
