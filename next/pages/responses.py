"""The `Cache-Control`, custom headers and `X-Robots-Tag` of a page response.

`cache` applies to the page that declares it, while `headers` apply to every page
below the `page.py` that declares them.
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

from next.caches import PageCache
from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded
from next.csrf import CsrfDelivery, csrf_delivery, defer_token
from next.deps.cache import render_dep_cache
from next.deps.resolver import current_resolver
from next.diagnostics import FailureLog, degraded
from next.pages.loaders import AncestorStamps
from next.pages.metadata.markers import NOINDEX_DIRECTIVES, Metadata, robots_directives
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
"""The `headers` of a page, where `None` removes a header an ancestor page set."""

CACHE_FLAGS: Final = ("public", "immutable", "no_store", "no_cache", "must_revalidate")
CACHE_AGES: Final = ("max_age", "s_maxage", "stale_while_revalidate", "stale_if_error")
CACHE_KEYS: Final = frozenset((*CACHE_FLAGS, *CACHE_AGES, "vary"))
"""The keys a `CacheDict` may carry."""

CDN_HEADERS: Final = (
    "CDN-Cache-Control",
    "Cloudflare-CDN-Cache-Control",
    "Surrogate-Control",
)
"""The headers a CDN reads in place of `Cache-Control`, removed when made private."""

CACHE_HEADERS: Final = frozenset(
    {"age", "cache-control", "expires", "vary", *(name.lower() for name in CDN_HEADERS)}
)
"""The lower-cased caching header names only `cache` sets, the CDN-targeted ones too.

A value set through `headers` would remain on a response the framework makes private.
"""

CSP_HEADERS: Final = frozenset(
    {"content-security-policy", "content-security-policy-report-only"}
)
"""The lower-cased policy headers the CSP middleware sets.

Django's CSP middleware and django-csp skip a response that already carries one, so a
page value would replace the site policy and its nonce.
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
"""The lower-cased header names a page may not set, because the framework sets them."""

_SHARED: Final = frozenset({"public", "s_maxage"})
_PERSONAL: Final = _SHARED | {"private"}
_TOKEN: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_UNSENDABLE: Final = re.compile(r"[^\t\x20-\x7e]")
"""Match a control character or a non-ASCII character, which no header value allows."""
_BLOCKING: Final = NOINDEX_DIRECTIVES | {"nofollow"}
_SUCCESS: Final = range(200, 300)
_CACHEABLE: Final = frozenset({"GET", "HEAD"})

_CACHE_SHAPE: Final = (
    "seconds as an int, False, a CacheDict or a callable returning one"
)
_ANSWER_SHAPE: Final = "seconds as an int, False, a CacheDict or None"
"""The shapes a callable `cache` may return, `None` declaring no cache."""


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
        """Whether any cache may store the response."""
        return all(name != "no_store" for name, _value in self.directives)

    @property
    def seconds(self) -> int | None:
        """Return the freshness lifetime, `s_maxage` when `max_age` is unset."""
        ages = dict(self.directives)
        age = ages.get("max_age", ages.get("s_maxage"))
        return age if is_int(age) else None

    def private(self) -> CacheControl:
        """Return the directives with every shared-cache permission dropped."""
        kept = tuple(pair for pair in self.directives if pair[0] not in _PERSONAL)
        return CacheControl((("private", True), *kept), self.vary)

    def apply(self, response: HttpResponseBase) -> None:
        """Patch the directives and the `Vary` names onto `response`.

        Django writes an empty `Cache-Control` when given no directive, so none is
        written for a declaration that only names `Vary` headers.
        """
        if self.directives:
            patch_cache_control(response, **dict(self.directives))
        if self.vary:
            patch_vary_headers(response, self.vary)


NO_STORE: Final = CacheControl((("private", True), ("no_store", True)))
"""The directives `cache = False` sets, which forbid every cache to store a copy."""


def _from_mapping(value: Mapping[object, object]) -> CacheControl | None:
    """Read a `CacheDict`, where `no_store` removes `public` and `s_maxage`."""
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
    """Normalise a static `cache` value, returning `None` for a wrong shape.

    An int of seconds means `public, max-age=<seconds>`, as in a `sitemap.py`.
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
    """Return the problems of a `CacheDict`, one per unknown key or misshapen value."""
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
    """Return the problems that keep a `cache` declaration from applying as written."""
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
    """Whether `value` is single-line ASCII text without a control character."""
    return isinstance(value, str) and _UNSENDABLE.search(value) is None


def _usable_header(name: object, value: object) -> bool:
    """Whether a page may send the header `name` with `value`, `None` removing it."""
    return (
        is_header_name(name)
        and str(name).lower() not in FORBIDDEN_HEADERS
        and (value is None or is_header_value(value))
    )


def headers_problems(value: object) -> list[str]:
    """Return the problems that keep a `headers` declaration from being sent."""
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return ["expected a mapping of header names to text or None"]
    problems: list[str] = []
    for name, header in value.items():
        if not is_header_name(name):
            problems.append(f"{name!r} is not a valid header name")
        elif name.lower() in CACHE_HEADERS:
            problems.append(f"{name} is set by cache, so declare the caching there")
        elif name.lower() in CSP_HEADERS:
            problems.append(
                f"{name} would replace the site policy, so declare it through the "
                "CSP middleware"
            )
        elif name.lower() in FORBIDDEN_HEADERS:
            problems.append(f"{name} is set by the framework")
        if header is not None and not is_header_value(header):
            problems.append(
                f"the value of {name!r} must be ASCII text on one line, with no "
                "control character"
            )
    return problems


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    """The cache, headers and robots header of one page response, set before render.

    `robots` is the `X-Robots-Tag` the static metadata requires, used when no head was
    rendered.
    """

    cache: CacheControl | None = None
    headers: tuple[tuple[str, str], ...] = ()
    robots: str | None = None

    @property
    def shared(self) -> bool:
        """Whether a shared cache may store the response, deferring the CSRF token."""
        return self.cache is not None and self.cache.shared


@dataclass(frozen=True, slots=True)
class _Declared:
    """The declarations of the `page.py` files above one page, with their load stamps.

    `dynamic` holds a callable `cache`, which needs a request, and `policy` the rest.
    `uncached` is `policy` without its cache, for a zone or a non-cacheable method.
    `refused` marks a metadata chain the schema refused, reported again per response.
    """

    ancestors: AncestorStamps
    policy: ResponsePolicy
    uncached: ResponsePolicy
    dynamic: Callable[..., object] | None
    refused: bool = False


_DECLARED: PageCache[Path, _Declared] = PageCache()


def _merge_headers(merged: dict[str, tuple[str, str | None]], value: object) -> None:
    """Merge one `headers` mapping over the ancestor ones, matching names by case."""
    if not isinstance(value, Mapping):
        return
    for name, header in value.items():
        if _usable_header(name, header):
            merged[name.lower()] = (name, header)


def _read_declared(page: Page, file_path: Path) -> _Declared:
    """Read `cache` from the page and `headers` from each `page.py`, the root first."""
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
    static = page.static_metadata(file_path)
    uncached = ResponsePolicy(None, headers, _static_robots(static.metadata))
    return _Declared(
        ancestors=ancestors,
        policy=replace(uncached, cache=cache_control(own)),
        uncached=uncached,
        dynamic=own if callable(own) else None,
        refused=static.refused,
    )


def _revalidated(held: _Declared) -> _Declared | None:
    """Return `held` while no `page.py` it was read from has reloaded, else `None`."""
    ancestors = held.ancestors.revalidated()
    if ancestors is None:
        return None
    return held if ancestors is held.ancestors else replace(held, ancestors=ancestors)


def _declared(page: Page, file_path: Path) -> _Declared:
    """Return the declarations above `file_path`, read again once one reloads."""
    stored = _DECLARED.get(file_path)
    held = None if stored is None else _revalidated(stored)
    if held is None:
        held = _read_declared(page, file_path)
    elif held.refused:
        # A memo hit skips the chain read, so the refusal is reported for this render.
        page.static_metadata(file_path)
    if held is not stored:
        _DECLARED[file_path] = held
    return held


def zone_policy(page: Page, file_path: Path) -> ResponsePolicy:
    """Return the policy of a zone response, the page headers and no cache.

    A zone response is never stored, so a callable `cache` is not called for it.
    """
    return _declared(page, file_path).uncached


def response_policy(
    page: Page,
    file_path: Path,
    request: HttpRequest,
    *,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object] | None = None,
) -> ResponsePolicy:
    """Return the policy of a full page, reading the static part once per reload.

    Only a `GET` or a `HEAD` receives a cache, and a callable `cache` is resolved
    through dependency injection.
    """
    held = _declared(page, file_path)
    if request.method not in _CACHEABLE:
        return held.uncached
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
    """Evaluate a callable `cache`, falling back to `NO_STORE` when it fails.

    A response whose policy is unknown must not be stored, so a failure only disables
    caching.
    """
    try:
        resolved = current_resolver().resolve_dependencies(
            func, request=request, _cache=dep_cache, _stack=[], **url_kwargs
        )
        value = func(**resolved)
    except Exception as exc:  # noqa: BLE001 - a page's own code may raise anything
        _failures.contain(
            exc,
            ("cache", file_path),
            "The callable cache in %s raised, so the page is sent with "
            "Cache-Control: private, no-store. Make it return %s.",
            file_path,
            _ANSWER_SHAPE,
        )
        return NO_STORE
    problems = cache_problems(value, callable_allowed=False)
    if not problems:
        return cache_control(value)
    message = (
        f"The callable cache in {file_path} returned {reprlib.repr(value)}, and "
        f"{', '.join(problems)}, so the page is sent with Cache-Control: private, "
        f"no-store. Make it return {_ANSWER_SHAPE}."
    )
    if fail_loudly():
        raise ImproperlyConfigured(message)
    _failures.warn(("cache", file_path), message)
    return NO_STORE


_SHARED_RENDER_ATTR: Final = "_next_shared_render"
_PERSONAL_RENDER_ATTR: Final = "_next_personal_render"
_COOKIE_VARY_ATTR: Final = "_next_cookie_vary"


def prepare_page_render(policy: ResponsePolicy, request: HttpRequest) -> None:
    """Prepare the render of a full page according to its policy.

    The CSRF token is deferred where `CSRF_DELIVERY` requires it, and a page a shared
    cache may store is marked as a shared render. A zone response skips both steps.
    """
    mode = csrf_delivery()
    if mode is CsrfDelivery.LAZY or (mode is CsrfDelivery.AUTO and policy.shared):
        defer_token(request)
    if policy.shared:
        mark_shared_render(request)


def mark_shared_render(request: HttpRequest) -> None:
    """Mark a render a shared cache may store, so its HTML must not show a visitor."""
    setattr(request, _SHARED_RENDER_ATTR, True)


def shared_render(request: HttpRequest | None) -> bool:
    """Whether a shared cache may store the response this render builds."""
    return getattr(request, _SHARED_RENDER_ATTR, False) is True


def mark_personal_render(request: HttpRequest | None) -> None:
    """Mark a render whose HTML is specific to one visitor, so it stays private."""
    if request is not None:
        setattr(request, _PERSONAL_RENDER_ATTR, True)


def personal_render(request: HttpRequest | None) -> bool:
    """Whether this render put visitor-specific content into its HTML."""
    return getattr(request, _PERSONAL_RENDER_ATTR, False) is True


def vary_on_cookie(request: HttpRequest | None) -> None:
    """Mark the response of `request` as one whose HTML depends on a cookie."""
    if request is not None:
        setattr(request, _COOKIE_VARY_ATTR, True)


def cookie_varies(request: HttpRequest | None) -> bool:
    """Whether the HTML of this response depends on a cookie."""
    return getattr(request, _COOKIE_VARY_ATTR, False) is True


def forget_response_policies(**kwargs) -> None:
    """Drop the memoised policies, so a settings reload takes effect."""
    _DECLARED.clear()


settings_reloaded.connect(forget_response_policies)


def _warn_private(file_path: Path) -> None:
    """Log per page, at the `FailureLog` rate, that its shared cache is made private."""
    _failures.warn(
        ("private", file_path),
        "%s declares a shared cache, but its response follows the visitor through a "
        "cookie, the session, the CSRF token, the consent, a CSP nonce or the "
        "Authorization header, so it is sent with Cache-Control: private.",
        file_path,
    )


def _take_private(
    response: HttpResponseBase, control: CacheControl, file_path: Path
) -> None:
    """Replace the shared cache of `response` with its private form."""
    _drop_cache_headers(response)
    control.private().apply(response)
    _warn_private(file_path)


def drop_cdn_headers(response: HttpResponseBase) -> None:
    """Remove every header in `CDN_HEADERS` from `response`."""
    for name in CDN_HEADERS:
        response.headers.pop(name, None)


def _drop_cache_headers(response: HttpResponseBase) -> None:
    """Remove `Cache-Control` and every CDN header, before a private form is set."""
    response.headers.pop("Cache-Control", None)
    drop_cdn_headers(response)


class SharedCookies(SimpleCookie):
    """The cookie jar of a shared response, which makes it private on the first cookie.

    Middleware sets the CSRF and session cookies after the view returns, so the jar
    itself runs the check.
    """

    def __init__(
        self, response: HttpResponseBase, control: CacheControl, file_path: Path
    ) -> None:
        """Watch `response`, which carries the shared `control` of the page."""
        super().__init__()
        self.shared: tuple[HttpResponseBase, CacheControl, Path] | None = (
            response,
            control,
            file_path,
        )

    @override
    def __setitem__(self, key: str, value: str | Morsel[str]) -> None:
        """Set the cookie, then make the response private."""
        super().__setitem__(key, value)
        self.take_private()

    @override
    def update(self, *args: Any, **kwargs: Any) -> None:
        """Set every cookie given, then make the response private."""
        super().update(*args, **kwargs)
        self._took_cookie()

    @override
    def load(self, rawdata: str | SupportsItems[str, str | Morsel[Any]]) -> None:
        """Parse the cookies given, then make the response private."""
        super().load(rawdata)
        self._took_cookie()

    def _took_cookie(self) -> None:
        """Make the response private when the jar holds any cookie."""
        if self:
            self.take_private()

    @override
    def __reduce__(self) -> tuple[object, ...]:
        """Pickle as a plain `SimpleCookie`, since a cached copy has no response."""
        return (SimpleCookie, (), None, None, iter(self.items()))

    def take_private(self) -> None:
        """Make the response private on the first call, ignoring every later call."""
        shared = self.shared
        if shared is not None:
            self.shared = None
            _take_private(*shared)


def _personal(request: HttpRequest, response: HttpResponseBase) -> bool:
    """Whether the response is specific to the visitor, by a cookie, session or HTML.

    A request with an `Authorization` header is personal too, since a shared copy would
    reach every visitor, and so is a render that read the CSP nonce in any template.
    """
    # Imported here, because the import of `next.static` reaches this module through
    # its checks, which load the page manager.
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


def _after_render(
    policy: ResponsePolicy, request: HttpRequest, response: HttpResponseBase
) -> None:
    """Set the robots and `Vary` headers of a lazily rendered response.

    A render that read the visitor makes a shared response private.
    """
    _seal(response, policy, request)
    cookies = response.cookies
    if isinstance(cookies, SharedCookies) and _personal(request, response):
        cookies.take_private()


def _apply_cache(
    response: HttpResponseBase,
    control: CacheControl,
    request: HttpRequest,
    file_path: Path,
) -> None:
    """Set the cache on a successful response that carries no `Cache-Control`."""
    if response.has_header("Cache-Control") or response.status_code not in _SUCCESS:
        return
    if not control.shared:
        control.apply(response)
    elif _personal(request, response):
        _take_private(response, control, file_path)
    else:
        control.apply(response)
        response.cookies = SharedCookies(response, control, file_path)


def _hold_degraded(response: HttpResponseBase) -> None:
    """Send a page whose failure was contained with `Cache-Control: private, no-store`.

    The page lacks what the failing source would have added, possibly a `noindex`, so
    neither a CDN nor the browser may store it.
    """
    cookies = response.cookies
    if isinstance(cookies, SharedCookies):
        cookies.shared = None
    _drop_cache_headers(response)
    NO_STORE.apply(response)


def _blocks(content: str | None) -> bool:
    """Whether the robots `content` holds a directive that blocks indexing or links."""
    return content is not None and not _BLOCKING.isdisjoint(robots_directives(content))


def robots_tag(robots: str | None, googlebot: str | None) -> str | None:
    """Return the `X-Robots-Tag` value of a page, `None` unless a directive blocks.

    The header names one agent, so the directives for every agent take precedence over
    the googlebot ones.
    """
    if _blocks(robots):
        return robots
    if _blocks(googlebot):
        return f"googlebot: {googlebot}"
    return None


def _static_robots(meta: Metadata) -> str | None:
    """Return the `X-Robots-Tag` value the static metadata of a page requires."""
    return robots_tag(*robots_contents(meta, indexable=True))


def _stamp_headers(response: HttpResponseBase, policy: ResponsePolicy) -> None:
    """Add the `headers` of the page, keeping every header the response already set."""
    for name, value in policy.headers:
        response.headers.setdefault(name, value)


def _seal(
    response: HttpResponseBase, policy: ResponsePolicy, request: HttpRequest
) -> None:
    """Set the robots headers and the cookie `Vary`, then make a degraded page private.

    Every stamp runs first, since the site robots header may call a callable
    `INDEXABLE` for the first time in the render, and a failure it contains must
    still keep the response out of every cache. A rendered head published the robots
    of this request, and the static metadata of the page stands in for a missing one.
    """
    stamp_site_robots(response, request)
    resolved = published_metadata(request)
    tag = (
        policy.robots
        if resolved is None
        else robots_tag(resolved.robots, resolved.googlebot)
    )
    if tag is not None:
        response.headers.setdefault(ROBOTS_HEADER, tag)
    if cookie_varies(request):
        patch_vary_headers(response, ("Cookie",))
    if degraded():
        _hold_degraded(response)


def finish_response[R: HttpResponseBase](
    response: R, policy: ResponsePolicy, request: HttpRequest, file_path: Path
) -> R:
    """Set the headers, the cache and the robots header of a page on its response.

    A response that `render()` built keeps every header it set, and the declarations
    of the page fill only the missing ones. A response whose template is not rendered
    yet gets these headers after its render, so the robots of its head reach the header.
    """
    _stamp_headers(response, policy)
    if policy.cache is not None and not degraded():
        _apply_cache(response, policy.cache, request, file_path)
    if isinstance(response, SimpleTemplateResponse) and not response.is_rendered:
        response.add_post_render_callback(
            functools.partial(_after_render, policy, request)
        )
    else:
        _seal(response, policy, request)
    return response


def finish_zone_response[R: HttpResponseBase](
    response: R, policy: ResponsePolicy, request: HttpRequest
) -> R:
    """Set the headers and the site robots header of a page on a zone response.

    Many CDNs ignore `Vary` and would serve a zone response for the full page, so a
    response without its own `Cache-Control` is sent with `private, no-store`. A
    response whose render contained a failure is sent with `private, no-store` too.
    """
    _stamp_headers(response, policy)
    if not response.has_header("Cache-Control"):
        NO_STORE.apply(response)
    stamp_site_robots(response, request)
    if degraded():
        _hold_degraded(response)
    return response


__all__ = [
    "CACHE_AGES",
    "CACHE_FLAGS",
    "CACHE_HEADERS",
    "CACHE_KEYS",
    "CDN_HEADERS",
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
    "drop_cdn_headers",
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
    "zone_policy",
]
