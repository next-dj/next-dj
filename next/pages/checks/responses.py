"""System checks for what a page response carries and how it delivers the CSRF token."""

from __future__ import annotations

import reprlib
from typing import TYPE_CHECKING, Final, cast

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template.defaulttags import CsrfTokenNode
from django.template.library import SimpleNode
from django.urls import NoReverseMatch, reverse
from django.utils import translation

from next.checks import NEXT
from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs
from next.conf.defaults import USER_SETTING
from next.csrf import CSRF_URL_NAME, CsrfDelivery, csrf_delivery, csrf_url
from next.pages.loaders import _load_python_module_memo
from next.pages.responses import cache_control, cache_problems, headers_problems
from next.utils import is_middleware, middleware_index, middleware_listed

from .composed import iter_composed_pages


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from django.template.base import Template


_LOCALE_MIDDLEWARE: Final = "django.middleware.locale.LocaleMiddleware"
_CONDITIONAL_GET: Final = "django.middleware.http.ConditionalGetMiddleware"
_UPDATE_CACHE: Final = "django.middleware.cache.UpdateCacheMiddleware"
_CACHE_GUARD: Final = "next.middleware.SharedCacheGuardMiddleware"
_COOKIELESS_MIDDLEWARE: Final = frozenset(
    {
        "django.middleware.security.SecurityMiddleware",
        "django.middleware.gzip.GZipMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "django.middleware.csp.ContentSecurityPolicyMiddleware",
        "next.site.middleware.RobotsHeaderMiddleware",
    }
)
"""Middleware known to set no cookie, so it may sit above `ConditionalGetMiddleware`.

A subclass counts as its base class.
"""
_RUNTIME_TAG: Final = "collect_scripts"
_MIN_LANGUAGES: Final = 2
_NAMED_PAGES: Final = 3


def _page_modules() -> Iterator[tuple[Path, object, object]]:
    """Yield each routed `page.py` that imports, with its `cache` and `headers`."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    seen: set[Path] = set()
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen):
                continue
            module = _load_python_module_memo(page_path)
            if module is not None:
                yield (
                    page_path,
                    getattr(module, "cache", None),
                    getattr(module, "headers", None),
                )


@register(NEXT)
def check_page_response_declarations(*args, **kwargs) -> list[CheckMessage]:
    """Require a readable `cache` and `headers` in every `page.py` (`next.E131`)."""
    errors: list[CheckMessage] = []
    for page_path, cache, headers in _page_modules():
        for name, value, problems in (
            ("cache", cache, cache_problems(cache)),
            ("headers", headers, headers_problems(headers)),
        ):
            if problems:
                errors.append(
                    Error(
                        f"{page_path} declares {name} = {reprlib.repr(value)}, and "
                        f"{', '.join(problems)}. The response does not carry it as "
                        "written.",
                        obj=str(page_path),
                        id="next.E131",
                    )
                )
    return errors


@register(NEXT)
def check_csrf_delivery(*args, **kwargs) -> list[CheckMessage]:
    """Require `CSRF_DELIVERY` to name a delivery mode (`next.E132`)."""
    raw = getattr(settings, USER_SETTING, None)
    value = raw.get("CSRF_DELIVERY") if isinstance(raw, dict) else None
    if not isinstance(value, str) or value in set(CsrfDelivery):
        return []
    modes = ", ".join(repr(mode.value) for mode in CsrfDelivery)
    return [
        Error(
            f"NEXT_FRAMEWORK['CSRF_DELIVERY'] is {value!r}, which names no delivery "
            f"mode, so pages deliver the token as under 'auto'. Write one of {modes}.",
            obj=settings,
            id="next.E132",
        )
    ]


@register(NEXT)
def check_csrf_endpoint_reversible(*args, **kwargs) -> list[CheckMessage]:
    """Require the token endpoint wherever a page may defer its token (`next.E148`).

    `auto` defers only on a page a shared cache may store, so it needs one such page.
    """
    mode = csrf_delivery()
    if mode is CsrfDelivery.EAGER:
        return []
    if mode is CsrfDelivery.AUTO and not shared_page_paths():
        return []
    try:
        csrf_url()
    except NoReverseMatch:
        return [
            Error(
                f"NEXT_FRAMEWORK['CSRF_DELIVERY'] is {mode.value!r}, so a page may "
                "leave its CSRF token to the _next/csrf/ endpoint, but ROOT_URLCONF "
                "does not route it. Pages fall back to embedding the token.",
                hint="Add path('', include('next.urls')) to ROOT_URLCONF.",
                obj=settings,
                id="next.E148",
            )
        ]
    return []


def _declared_shared(cache: object) -> bool:
    """Whether a `cache` may let a shared cache store the page.

    A callable returns its value per request, so the check counts it as shared.
    """
    if callable(cache):
        return True
    control = cache_control(cache)
    return control is not None and control.shared


def _named(page_path: Path) -> str:
    """Return the name of a page, marked when its `cache` is a callable."""
    module = _load_python_module_memo(page_path)
    if callable(getattr(module, "cache", None)):
        return f"{page_path} (callable cache)"
    return str(page_path)


def _listed(pages: list[Path]) -> str:
    """Return the names of the first few pages and the count of the rest."""
    named = ", ".join(_named(path) for path in pages[:_NAMED_PAGES])
    rest = len(pages) - _NAMED_PAGES
    return f"{named} and {rest} more" if rest > 0 else named


def shared_page_paths() -> list[Path]:
    """Return each routed page whose `cache` may let a shared cache store it."""
    return [
        path for path, cache, _headers in _page_modules() if _declared_shared(cache)
    ]


def private_pages_warning(cause: str, check_id: str) -> list[CheckMessage]:
    """Warn that a setting makes every page a CDN may store private, for `cause`."""
    pages = shared_page_paths()
    if not pages:
        return []
    return [
        DjangoWarning(
            "Every page declaring a cache a CDN may hold is sent with Cache-Control: "
            f"private, since {cause} Pages affected: {_listed(pages)}.",
            obj=settings,
            id=check_id,
        )
    ]


@register(NEXT)
def check_csrf_in_session(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `CSRF_USE_SESSIONS` makes every shared page private (`next.W121`)."""
    if not getattr(settings, "CSRF_USE_SESSIONS", False):
        return []
    return private_pages_warning(
        "CSRF_USE_SESSIONS reads the session on every request. Keep the CSRF token "
        "in its cookie, or drop the shared cache.",
        "next.W121",
    )


def _guarded(middleware: list[object]) -> bool:
    """Whether `SharedCacheGuardMiddleware` sees every response a cookie is set on.

    Only `UpdateCacheMiddleware` may be listed above it, so the copy that middleware
    stores is the private one. Listed above `UpdateCacheMiddleware`, the guard runs
    after the public copy is already stored.
    """
    index = middleware_index(middleware, _CACHE_GUARD)
    if index is None:
        return False
    update = middleware_index(middleware, _UPDATE_CACHE)
    if update is not None and update > index:
        return False
    return all(is_middleware(entry, _UPDATE_CACHE) for entry in middleware[:index])


@register(NEXT)
def check_conditional_get_order(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a cookie may be set on a shared 304 (`next.W124`).

    `ConditionalGetMiddleware` copies the cache of a shared page onto the 304 it
    builds, so a cookie that an outer middleware sets later is sent on a public 304.
    """
    middleware = list(getattr(settings, "MIDDLEWARE", None) or ())
    position = middleware_index(middleware, _CONDITIONAL_GET)
    if position is None or _guarded(middleware):
        return []
    outer = [
        str(name)
        for name in middleware[:position]
        if not any(is_middleware(name, quiet) for quiet in _COOKIELESS_MIDDLEWARE)
    ]
    pages = shared_page_paths() if outer else []
    if not pages:
        return []
    return [
        DjangoWarning(
            f"settings.MIDDLEWARE lists {', '.join(outer)} above "
            "ConditionalGetMiddleware, and it may set a cookie on the 304 that "
            "middleware builds after the page made its cache private, so a public "
            "304 could carry Set-Cookie into a shared cache. Pages affected: "
            f"{_listed(pages)}.",
            hint=(
                "List ConditionalGetMiddleware above it in settings.MIDDLEWARE, or "
                f"list {_CACHE_GUARD} first, which makes such a response private."
            ),
            obj=settings,
            id="next.W124",
        )
    ]


def shared_pages() -> Iterator[tuple[Path, Template]]:
    """Yield each page whose `cache` may let a shared cache store it."""
    for page_path, template in iter_composed_pages():
        module = _load_python_module_memo(page_path)
        if _declared_shared(getattr(module, "cache", None)):
            yield page_path, template


def renders_runtime(template: Template) -> bool:
    """Whether the template renders the runtime, which posts with the CSRF token."""
    nodes = cast("list[SimpleNode]", template.nodelist.get_nodes_by_type(SimpleNode))
    return any(getattr(node.func, "__name__", None) == _RUNTIME_TAG for node in nodes)


def shared_warning(page_path: Path, text: str, check_id: str) -> CheckMessage:
    """Return a warning about a page a CDN may store, `text` naming the problem."""
    return DjangoWarning(
        f"{_named(page_path)} declares a cache a CDN may hold, but {text}",
        obj=str(page_path),
        id=check_id,
    )


def _csrf_token_tag(page_path: Path, template: Template) -> CheckMessage | None:
    """Return `next.W113` for a shared page rendering `{% csrf_token %}`."""
    if not template.nodelist.get_nodes_by_type(CsrfTokenNode):
        return None
    return shared_warning(
        page_path,
        "it renders {% csrf_token %}, which sets the CSRF cookie, so every response "
        "is sent with Cache-Control: private. Post through {% form %}, which leaves "
        "the token to the runtime.",
        "next.W113",
    )


def _pages_language_prefixed() -> bool | None:
    """Whether the page routes carry a language prefix, `None` when it is unknown.

    The token endpoint shares the include of the pages, so its address is checked.
    """
    codes = [code for code, _name in settings.LANGUAGES][:_MIN_LANGUAGES]
    addresses: set[str] = set()
    for code in codes:
        with translation.override(code):
            try:
                addresses.add(reverse(f"next:{CSRF_URL_NAME}"))
            except NoReverseMatch:
                return None
    return len(addresses) > 1


def _mixed_languages() -> bool:
    """Whether one URL serves several languages, which a shared cache would mix."""
    if not middleware_listed(settings.MIDDLEWARE, _LOCALE_MIDDLEWARE):
        return False
    if len(settings.LANGUAGES) < _MIN_LANGUAGES:
        return False
    return _pages_language_prefixed() is False


@register(Tags.templates, NEXT)
def check_shared_page_responses(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a shared page that sets the CSRF cookie or mixes languages."""
    warnings: list[CheckMessage] = []
    mixed = _mixed_languages()
    for page_path, template in shared_pages():
        warning = _csrf_token_tag(page_path, template)
        if warning is not None:
            warnings.append(warning)
        if mixed:
            warnings.append(
                shared_warning(
                    page_path,
                    "LocaleMiddleware picks its language per request outside "
                    "i18n_patterns(), so a CDN hands one language to every visitor. "
                    "Route the pages through i18n_patterns().",
                    "next.W114",
                )
            )
    return warnings


__all__ = [
    "check_conditional_get_order",
    "check_csrf_delivery",
    "check_csrf_endpoint_reversible",
    "check_csrf_in_session",
    "check_page_response_declarations",
    "check_shared_page_responses",
    "renders_runtime",
    "shared_page_paths",
    "shared_pages",
    "shared_warning",
]
