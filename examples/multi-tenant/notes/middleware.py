from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect

from notes.models import Tenant


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


HEADER_NAME = "HTTP_X_TENANT"
QUERY_PARAM = "tenant"
COOKIE_NAME = "next_tenant"
TENANT_STATIC_PREFIX = "/_t/"
MISSING_TENANT_BODY = "Missing X-Tenant header."
MISSING_TENANT_DEBUG_HINT = " In DEBUG you may also pass ?tenant=<slug> for a demo."
UNKNOWN_TENANT_BODY = "Unknown tenant."


def is_debug_fallback_enabled() -> bool:
    """Return whether the DEBUG-only query/cookie fallbacks are active."""
    return bool(settings.DEBUG)


class TenantMiddleware:
    """Resolve the active tenant for every request out of the `X-Tenant` header.

    A request header is attacker-controlled, so this shape isolates tenants only behind
    a reverse proxy that owns the header. That proxy has to be the single route to the
    application, has to derive the slug from something it owns rather than from
    anything the client sent, and has to set the header on every request it forwards so
    an inbound copy is overwritten instead of passed along. A proxy that fills the
    header in only when it is absent keeps the forged value, and an application exposed
    directly hands one tenant's notes to anybody who names another tenant's slug.

    Reading the tenant from the signed-in user's membership rows or from the request
    host needs no proxy, and the how-to on scoping requests per tenant weighs the three
    shapes against each other. The DEBUG fallback here additionally accepts a
    `?tenant=<slug>` query parameter and a `next_tenant` cookie so the demo is
    browsable without a header-injecting extension, and it is off in production.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the downstream view callable."""
        self._get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Attach `request.tenant` and forward, or short-circuit on errors."""
        if request.path.startswith(TENANT_STATIC_PREFIX):
            return self._get_response(request)
        slug, debug_query, slug_came_from_cookie = _resolve_tenant_slug(request)
        if slug is None:
            return HttpResponseBadRequest(_missing_tenant_body())

        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            # The body never repeats the submitted slug. A reflected value enumerates
            # tenant names for an attacker and, in an HTML response, is an XSS sink.
            response = HttpResponse(UNKNOWN_TENANT_BODY, status=404)
            if slug_came_from_cookie:
                response.delete_cookie(COOKIE_NAME)
            return response

        if debug_query:
            response = HttpResponseRedirect(_strip_tenant_query(request))
            response.set_cookie(COOKIE_NAME, slug, httponly=True, samesite="Lax")
            return response

        request.tenant = tenant  # type: ignore[attr-defined]
        return self._get_response(request)


def _missing_tenant_body() -> str:
    """Return the fixed 400 body, naming the demo affordance only while it exists."""
    if is_debug_fallback_enabled():
        return MISSING_TENANT_BODY + MISSING_TENANT_DEBUG_HINT
    return MISSING_TENANT_BODY


def _resolve_tenant_slug(request: HttpRequest) -> tuple[str | None, bool, bool]:
    """Return (slug, came_from_query, came_from_cookie) for the request."""
    header_value = request.META.get(HEADER_NAME, "").strip()
    if header_value:
        return header_value, False, False

    if not is_debug_fallback_enabled():
        return None, False, False

    query_value = request.GET.get(QUERY_PARAM, "").strip()
    if query_value:
        return query_value, True, False

    cookie_value = request.COOKIES.get(COOKIE_NAME, "").strip()
    if cookie_value:
        return cookie_value, False, True

    return None, False, False


def _strip_tenant_query(request: HttpRequest) -> str:
    """Return the request path with the `tenant` query parameter removed."""
    remaining = request.GET.copy()
    remaining.pop(QUERY_PARAM, None)
    # A path the client sent as `//host/...` is a protocol-relative URL in a Location
    # header and would redirect off-site, so the leading slashes collapse to one.
    path = "/" + request.path.lstrip("/")
    if remaining:
        return f"{path}?{remaining.urlencode()}"
    return path
