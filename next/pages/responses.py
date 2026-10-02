"""The cache, headers and robots header a page response carries on top of its body.

`cache` is a claim of one page, while `headers` flow down the tree like a layout does.
"""

from __future__ import annotations

import functools
import logging
import re
import reprlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from http.cookies import Morsel, SimpleCookie
from typing import TYPE_CHECKING, Any, Final, TypedDict, override

from django.core.exceptions import ImproperlyConfigured
from django.template.response import SimpleTemplateResponse
from django.utils.cache import patch_cache_control, patch_vary_headers

from next.caches import BoundedCache
from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded
from next.csrf import CsrfDelivery, csrf_delivery, defer_token
from next.deps.cache import render_dep_cache
from next.deps.resolver import current_resolver
from next.diagnostics import INTENDED_EXCEPTIONS, FailureLog
from next.pages.loaders import AncestorStamps
from next.pages.metadata.markers import NOINDEX_DIRECTIVES, robots_directives
from next.pages.metadata.resolve import published_metadata, robots_contents
from next.site.headers import ROBOTS_HEADER, stamp_site_robots
from next.utils import is_int


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _typeshed import SupportsItems
    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

    from .manager import Page


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)


class CacheDict(TypedDict, total=False):
    """The `cache` of a page as `Cache-Control` directives, ages in seconds."""

    public: bool
    max_age: int
    s_maxage: int
    stale_while_revalidate: int
    stale_if_error: int
    immutable: bool
    no_store: bool
    no_cache: bool
    must_revalidate: bool
    vary: list[str]


type HeadersDict = Mapping[str, str | None]
"""The `headers` of a page, `None` dropping one an ancestor page set."""

CACHE_FLAGS: Final = ("public", "immutable", "no_store", "no_cache", "must_revalidate")
CACHE_AGES: Final = ("max_age", "s_maxage", "stale_while_revalidate", "stale_if_error")
CACHE_KEYS: Final = frozenset((*CACHE_FLAGS, *CACHE_AGES, "vary"))
"""The keys a `CacheDict` may carry."""

CACHE_HEADERS: Final = frozenset(
    {
        "age",
        "cache-control",
        "cdn-cache-control",
        "cloudflare-cdn-cache-control",
        "expires",
        "surrogate-control",
        "vary",
    }
)
"""The caching header names `cache` owns, lower-cased, a CDN-targeted one included.

One set by `headers` would outlive the private form a personal response takes.
"""

CSP_HEADERS: Final = frozenset(
    {"content-security-policy", "content-security-policy-report-only"}
)
"""The policy headers a CSP middleware owns, lower-cased.

Django's middleware and django-csp skip a response carrying one, so a page's own
would replace the whole site policy, its nonce included.
"""

FORBIDDEN_HEADERS: Final = (
    CACHE_HEADERS
    | CSP_HEADERS
    | {
        "connection",
        "content-length",
        "content-type",
        "set-cookie",
        "transfer-encoding",
        "x-robots-tag",
    }
)
"""The header names a page may not set, lower-cased, since the framework owns them."""

_SHARED: Final = frozenset({"public", "s_maxage"})
_PERSONAL: Final = _SHARED | {"private"}
_TOKEN: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_UNSENDABLE: Final = re.compile(r"[^\t\x20-\x7e]")
"""What no header value may hold, a control character or one outside ASCII."""
_BLOCKING: Final = NOINDEX_DIRECTIVES | {"nofollow"}
_SUCCESS: Final = range(200, 300)
_CACHEABLE: Final = frozenset({"GET", "HEAD"})

_CACHE_SHAPE: Final = (
    "seconds as an int, False, a CacheDict or a callable returning one"
)
_ANSWER_SHAPE: Final = "seconds as an int, False, a CacheDict or None"
"""What a callable `cache` returns, `None` declaring no cache for that response."""


@dataclass(frozen=True, slots=True)
class CacheControl:
    """The `Cache-Control` directives and `Vary` names one `cache` declaration sets."""

    directives: tuple[tuple[str, int | bool], ...]
    vary: tuple[str, ...] = ()

    @property
    def shared(self) -> bool:
        """Whether a shared cache such as a CDN may store the response."""
        return any(name in _SHARED for name, _value in self.directives)

    @property
    def stores(self) -> bool:
        """Whether any cache may keep a copy of the response at all."""
        return all(name != "no_store" for name, _value in self.directives)

    @property
    def seconds(self) -> int | None:
        """Return how long the response stays fresh, the shared age as a fallback."""
        ages = dict(self.directives)
        age = ages.get("max_age", ages.get("s_maxage"))
        return age if is_int(age) else None

    def private(self) -> CacheControl:
        """Return the directives with every shared-cache permission dropped."""
        kept = tuple(pair for pair in self.directives if pair[0] not in _PERSONAL)
        return CacheControl((("private", True), *kept), self.vary)

    def apply(self, response: HttpResponseBase) -> None:
        """Patch the directives and the `Vary` names onto `response`."""
        patch_cache_control(response, **dict(self.directives))
        if self.vary:
            patch_vary_headers(response, self.vary)


NO_STORE: Final = CacheControl((("private", True), ("no_store", True)))
"""What `cache = False` sets, a response no cache keeps."""


def _from_mapping(value: Mapping[object, object]) -> CacheControl | None:
    """Read a `CacheDict`, `no_store` taking any shared permission back."""
    directives: list[tuple[str, int | bool]] = [
        (name, True) for name in CACHE_FLAGS if value.get(name) is True
    ]
    for name in CACHE_AGES:
        age = value.get(name)
        if is_int(age) and age >= 0:
            directives.append((name, age))
    if value.get("no_store") is True:
        directives = [pair for pair in directives if pair[0] not in _SHARED]
    vary = value.get("vary")
    names = (
        tuple(name for name in vary if is_header_name(name))
        if isinstance(vary, list | tuple)
        else ()
    )
    if not directives and not names:
        return None
    return CacheControl(tuple(directives), names)


def cache_control(value: object) -> CacheControl | None:
    """Normalise a static `cache` value, a wrong shape reading as no declaration.

    Seconds alone mean public, the same as `cache = 3600` in a `sitemap.py`.
    """
    if value is False:
        return NO_STORE
    if is_int(value):
        return (
            CacheControl((("public", True), ("max_age", value))) if value >= 0 else None
        )
    if isinstance(value, Mapping):
        return _from_mapping(value)
    return None


def _mapping_problems(value: Mapping[object, object]) -> list[str]:
    unknown = sorted(str(key) for key in value if key not in CACHE_KEYS)
    problems = [f"unknown keys {', '.join(unknown)}"] if unknown else []
    problems.extend(
        f"{name} must be a bool"
        for name in CACHE_FLAGS
        if value.get(name) is not None and not isinstance(value.get(name), bool)
    )
    for name in CACHE_AGES:
        age = value.get(name)
        if age is not None and not (is_int(age) and age >= 0):
            problems.append(f"{name} must be seconds as an int of 0 or more")
    vary = value.get("vary")
    if vary is not None and not (
        isinstance(vary, list | tuple) and all(is_header_name(name) for name in vary)
    ):
        problems.append("vary must be a list of header names")
    if value.get("public") is True and value.get("no_store") is True:
        problems.append("public contradicts no_store")
    return problems


def cache_problems(value: object, *, callable_allowed: bool = True) -> list[str]:
    """Return what keeps a `cache` declaration from being read as written."""
    if value is None or value is False or (callable_allowed and callable(value)):
        return []
    if is_int(value):
        return [] if value >= 0 else ["the age is negative"]
    if isinstance(value, Mapping):
        return _mapping_problems(value)
    return [f"expected {_CACHE_SHAPE}"]


def is_header_name(value: object) -> bool:
    """Whether `value` is a valid HTTP header name."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def is_header_value(value: object) -> bool:
    """Whether `value` is ASCII text a header carries on one line as written."""
    return isinstance(value, str) and _UNSENDABLE.search(value) is None


def _usable_header(name: object, value: object) -> bool:
    return (
        is_header_name(name)
        and str(name).lower() not in FORBIDDEN_HEADERS
        and (value is None or is_header_value(value))
    )


def headers_problems(value: object) -> list[str]:
    """Return what keeps a `headers` declaration from being sent as written."""
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return ["expected a mapping of header names to text or None"]
    problems: list[str] = []
    for name, header in value.items():
        if not is_header_name(name):
            problems.append(f"{name!r} is no header name")
        elif name.lower() in CACHE_HEADERS:
            problems.append(f"{name} follows cache, so declare the caching there")
        elif name.lower() in CSP_HEADERS:
            problems.append(
                f"{name} would replace the site policy, so declare it through the "
                "CSP middleware"
            )
        elif name.lower() in FORBIDDEN_HEADERS:
            problems.append(f"{name} belongs to the framework")
        if header is not None and not is_header_value(header):
            problems.append(
                f"the value of {name!r} must be ASCII text on one line, with no "
                "control character"
            )
    return problems


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    """What one page response carries on top of its body, settled before it renders.

    `robots` is the header the static metadata calls for, when no head was rendered.
    """

    cache: CacheControl | None = None
    headers: tuple[tuple[str, str], ...] = ()
    robots: str | None = None

    @property
    def shared(self) -> bool:
        """Whether a shared cache may store the response, which defers its CSRF."""
        return self.cache is not None and self.cache.shared


@dataclass(frozen=True, slots=True)
class _Declared:
    """What the `page.py` files above one page declare, with the stamps they ran at.

    `dynamic` is a callable `cache` only a request answers, `policy` holding the rest.
    """

    ancestors: AncestorStamps
    policy: ResponsePolicy
    dynamic: Callable[..., object] | None


_DECLARED: BoundedCache[Path, _Declared] = BoundedCache()


def _merge_headers(merged: dict[str, tuple[str, str | None]], value: object) -> None:
    """Fold one `headers` over the ones above it, by name without regard to case."""
    if not isinstance(value, Mapping):
        return
    for name, header in value.items():
        if _usable_header(name, header):
            merged[name.lower()] = (name, header)


def _read_declared(page: Page, file_path: Path) -> _Declared:
    """Read `cache` off the page and `headers` off each `page.py` from the root down."""
    ancestors, modules = AncestorStamps.begin(file_path).loaded()
    merged: dict[str, tuple[str, str | None]] = {}
    own: object = None
    for path, module in zip(ancestors.paths, modules, strict=True):
        if module is None:
            continue
        _merge_headers(merged, getattr(module, "headers", None))
        if path == file_path:
            own = getattr(module, "cache", None)
    headers = tuple(
        (name, header) for name, header in merged.values() if header is not None
    )
    return _Declared(
        ancestors=ancestors,
        policy=ResponsePolicy(
            cache_control(own), headers, _static_robots(page, file_path)
        ),
        dynamic=own if callable(own) else None,
    )


def _revalidated(held: _Declared) -> _Declared | None:
    """Return `held` while no `page.py` behind it loaded again, `None` once one did."""
    ancestors = held.ancestors.revalidated()
    if ancestors is None:
        return None
    return held if ancestors is held.ancestors else replace(held, ancestors=ancestors)


def response_policy(
    page: Page,
    file_path: Path,
    request: HttpRequest,
    *,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object] | None = None,
) -> ResponsePolicy:
    """Return the policy of one response, a static one read once per module reload.

    Only a `GET` or a `HEAD` carries a cache, a callable one resolved by injection.
    """
    stored = _DECLARED.get(file_path)
    held = None if stored is None else _revalidated(stored)
    if held is None:
        held = _read_declared(page, file_path)
    if held is not stored:
        _DECLARED[file_path] = held
    if request.method not in _CACHEABLE:
        return replace(held.policy, cache=None)
    func = held.dynamic
    if func is None:
        return held.policy
    cache = render_dep_cache(request) if dep_cache is None else dep_cache
    return replace(
        held.policy, cache=_dynamic_cache(func, file_path, request, cache, url_kwargs)
    )


def _dynamic_cache(
    func: Callable[..., object],
    file_path: Path,
    request: HttpRequest,
    dep_cache: dict[str, object],
    url_kwargs: Mapping[str, object],
) -> CacheControl | None:
    """Answer a callable `cache`, a raising or misshapen one taking the page no-store.

    No cache keeps a response whose policy is unknown, so a failure costs only the copy.
    """
    try:
        resolved = current_resolver().resolve_dependencies(
            func, request=request, _cache=dep_cache, _stack=[], **url_kwargs
        )
        value = func(**resolved)
    except INTENDED_EXCEPTIONS:
        raise
    except Exception as exc:  # noqa: BLE001 - a page's own code may raise anything
        _failures.contain(
            exc,
            ("cache", file_path),
            "The callable cache in %s raised, so the page goes out no-store. "
            "Make it return %s.",
            file_path,
            _ANSWER_SHAPE,
        )
        return NO_STORE
    problems = cache_problems(value, callable_allowed=False)
    if not problems:
        return cache_control(value)
    message = (
        f"The callable cache in {file_path} returned {reprlib.repr(value)}, and "
        f"{', '.join(problems)}, so the page goes out no-store. Make it return "
        f"{_ANSWER_SHAPE}."
    )
    if fail_loudly():
        raise ImproperlyConfigured(message)
    _failures.warn(("cache", file_path), message)
    return NO_STORE


_SHARED_RENDER_ATTR: Final = "_next_shared_render"
_PERSONAL_RENDER_ATTR: Final = "_next_personal_render"
_COOKIE_VARY_ATTR: Final = "_next_cookie_vary"


def prepare_page_render(policy: ResponsePolicy, request: HttpRequest) -> None:
    """Set up the render of a full page its policy lets a shared cache hold.

    The CSRF token leaves the HTML where `CSRF_DELIVERY` asks, and the render learns
    no visitor may show. A zone answer is private and skips both.
    """
    mode = csrf_delivery()
    if mode is CsrfDelivery.LAZY or (mode is CsrfDelivery.AUTO and policy.shared):
        defer_token(request)
    if policy.shared:
        mark_shared_render(request)


def mark_shared_render(request: HttpRequest) -> None:
    """Mark a render a shared cache may hold, so its HTML shows no visitor."""
    setattr(request, _SHARED_RENDER_ATTR, True)


def shared_render(request: HttpRequest | None) -> bool:
    """Whether a shared cache may hold the response this render builds."""
    return getattr(request, _SHARED_RENDER_ATTR, False) is True


def mark_personal_render(request: HttpRequest | None) -> None:
    """Mark a render whose HTML shows one visitor, so no shared cache may keep it."""
    if request is not None:
        setattr(request, _PERSONAL_RENDER_ATTR, True)


def personal_render(request: HttpRequest | None) -> bool:
    """Whether this render put something of one visitor into its HTML."""
    return getattr(request, _PERSONAL_RENDER_ATTR, False) is True


def vary_on_cookie(request: HttpRequest | None) -> None:
    """Mark the response of `request` as one whose HTML follows a cookie."""
    if request is not None:
        setattr(request, _COOKIE_VARY_ATTR, True)


def cookie_varies(request: HttpRequest | None) -> bool:
    """Whether the HTML of this response follows a cookie."""
    return getattr(request, _COOKIE_VARY_ATTR, False) is True


_WARNED: BoundedCache[Path, bool] = BoundedCache()


def forget_response_policies(**kwargs) -> None:
    """Drop the memoised policies and the pages already warned about going private."""
    _DECLARED.clear()
    _WARNED.clear()


settings_reloaded.connect(forget_response_policies)


def _warn_private(file_path: Path) -> None:
    """Log once per page that its shared cache went private for a personal response."""
    if file_path in _WARNED:
        return
    _WARNED[file_path] = True
    logger.warning(
        "%s declares a shared cache, but its response follows the visitor through a "
        "cookie, the session, the CSRF token, the consent, a CSP nonce or the "
        "Authorization header, so it goes out private.",
        file_path,
    )


def _take_private(
    response: HttpResponseBase, control: CacheControl, file_path: Path
) -> None:
    """Replace the shared cache of `response` with its private form."""
    del response["Cache-Control"]
    control.private().apply(response)
    _warn_private(file_path)


class SharedCookies(SimpleCookie):
    """The cookies of a shared response, the first one set taking the cache private.

    Middleware sets the CSRF and session cookies after the view, so the check sits here.
    """

    def __init__(
        self, response: HttpResponseBase, control: CacheControl, file_path: Path
    ) -> None:
        """Guard `response`, which holds the shared `control` of the page."""
        super().__init__()
        self.shared: tuple[HttpResponseBase, CacheControl, Path] | None = (
            response,
            control,
            file_path,
        )

    @override
    def __setitem__(self, key: str, value: str | Morsel[str]) -> None:
        """Set the cookie, then take back the shared-cache permission."""
        super().__setitem__(key, value)
        self.take_private()

    @override
    def update(self, *args: Any, **kwargs: Any) -> None:
        """Set every cookie given, then take back the shared-cache permission."""
        super().update(*args, **kwargs)
        self._took_cookie()

    @override
    def load(self, rawdata: str | SupportsItems[str, str | Morsel[Any]]) -> None:
        """Parse the cookies given, then take back the shared-cache permission."""
        super().load(rawdata)
        self._took_cookie()

    def _took_cookie(self) -> None:
        if self:
            self.take_private()

    @override
    def __reduce__(self) -> tuple[object, ...]:
        """Pickle as a plain `SimpleCookie`, since a cached copy guards nothing."""
        return (SimpleCookie, (), None, None, iter(self.items()))

    def take_private(self) -> None:
        """Turn the response private, once, however many cookies follow."""
        shared = self.shared
        if shared is not None:
            self.shared = None
            _take_private(*shared)


def _personal(request: HttpRequest, response: HttpResponseBase) -> bool:
    """Whether the response shows who asked, by a cookie, the session or its HTML.

    A request with credentials is personal too, as a shared copy would reach everyone,
    and so is one whose render read the CSP nonce, a template of the project included.
    """
    # Read here, since `next.static` renders pages and so imports this module first.
    from next.static.nonce import nonce_minted  # noqa: PLC0415

    if (
        response.cookies
        or request.META.get("CSRF_COOKIE_NEEDS_UPDATE")
        or request.META.get("HTTP_AUTHORIZATION")
        or personal_render(request)
        or cookie_varies(request)
        or nonce_minted(request)
    ):
        return True
    session = getattr(request, "session", None)
    return bool(getattr(session, "accessed", False))


def _after_render(request: HttpRequest, response: HttpResponseBase) -> None:
    """Settle the cookie `Vary` and the shared cache of a lazily rendered response."""
    if cookie_varies(request):
        patch_vary_headers(response, ("Cookie",))
    cookies = response.cookies
    if isinstance(cookies, SharedCookies) and _personal(request, response):
        cookies.take_private()


def _apply_cache(
    response: HttpResponseBase,
    control: CacheControl,
    request: HttpRequest,
    file_path: Path,
) -> None:
    """Set the cache of a successful response that names none of its own."""
    if response.has_header("Cache-Control") or response.status_code not in _SUCCESS:
        return
    if control.shared and _personal(request, response):
        _warn_private(file_path)
        control = control.private()
    control.apply(response)
    if control.shared:
        response.cookies = SharedCookies(response, control, file_path)


def _blocks(content: str | None) -> bool:
    return content is not None and not _BLOCKING.isdisjoint(robots_directives(content))


def robots_tag(robots: str | None, googlebot: str | None) -> str | None:
    """Return the `X-Robots-Tag` a page's robots directives call for, if any block.

    A header holds one agent, so the directives for every agent win over googlebot.
    """
    if _blocks(robots):
        return robots
    if _blocks(googlebot):
        return f"googlebot: {googlebot}"
    return None


def _static_robots(page: Page, file_path: Path) -> str | None:
    """Return the robots header the static metadata of the page calls for."""
    return robots_tag(*robots_contents(page.static_metadata(file_path), indexable=True))


def _stamp_headers(response: HttpResponseBase, policy: ResponsePolicy) -> None:
    """Add the `headers` of the page, each one the response already set kept."""
    for name, value in policy.headers:
        response.headers.setdefault(name, value)


def finish_response[R: HttpResponseBase](
    response: R, policy: ResponsePolicy, request: HttpRequest, file_path: Path
) -> R:
    """Stamp the headers, the cache and the robots header of a page on its response.

    A response `render()` built keeps every header it set, the page filling the gaps.
    """
    _stamp_headers(response, policy)
    if policy.cache is not None:
        _apply_cache(response, policy.cache, request, file_path)
    stamp_site_robots(response, request)
    if isinstance(response, SimpleTemplateResponse) and not response.is_rendered:
        response.add_post_render_callback(functools.partial(_after_render, request))
    elif cookie_varies(request):
        patch_vary_headers(response, ("Cookie",))
    resolved = published_metadata(request)
    tag = (
        policy.robots
        if resolved is None
        else robots_tag(resolved.robots, resolved.googlebot)
    )
    if tag is not None:
        response.headers.setdefault(ROBOTS_HEADER, tag)
    return response


def finish_zone_response[R: HttpResponseBase](
    response: R, policy: ResponsePolicy, request: HttpRequest
) -> R:
    """Stamp the headers and the robots header of a page on one of its zone answers.

    Many CDNs ignore `Vary` and would serve a zone answer as the page, so none is kept.
    """
    _stamp_headers(response, policy)
    if not response.has_header("Cache-Control"):
        NO_STORE.apply(response)
    return stamp_site_robots(response, request)


__all__ = [
    "CACHE_AGES",
    "CACHE_FLAGS",
    "CACHE_HEADERS",
    "CACHE_KEYS",
    "CSP_HEADERS",
    "FORBIDDEN_HEADERS",
    "NO_STORE",
    "CacheControl",
    "CacheDict",
    "HeadersDict",
    "ResponsePolicy",
    "SharedCookies",
    "cache_control",
    "cache_problems",
    "cookie_varies",
    "finish_response",
    "finish_zone_response",
    "forget_response_policies",
    "headers_problems",
    "is_header_name",
    "is_header_value",
    "mark_personal_render",
    "mark_shared_render",
    "personal_render",
    "prepare_page_render",
    "response_policy",
    "robots_tag",
    "shared_render",
    "vary_on_cookie",
]
