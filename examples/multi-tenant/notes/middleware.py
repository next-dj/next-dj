from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from notes.models import Tenant


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest


HEADER_NAME = "HTTP_X_TENANT"
QUERY_PARAM = "tenant"
COOKIE_NAME = "next_tenant"
TENANT_STATIC_PREFIX = "/_t/"
HOME_PATH = "/"
MISSING_TENANT_BODY = "Missing X-Tenant header."
MISSING_TENANT_DEBUG_HINT = " In DEBUG you may also pass ?tenant=<slug> for a demo."
UNKNOWN_TENANT_BODY = "Unknown tenant."


def is_debug_fallback_enabled() -> bool:
    """Return whether the DEBUG-only query/cookie fallbacks are active."""
    return bool(settings.DEBUG)


class TenantMiddleware:
    """Resolve the active tenant for every request out of the `X-Tenant` header.

    Safe only behind a reverse proxy that is the sole route in, derives the slug
    itself, and always overwrites rather than fills in a client-supplied header.
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
            # No `secure=True`, because the fallback that writes this cookie runs only
            # under DEBUG, where the demo is served over plain HTTP.
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
    """Return the request path with the `tenant` query parameter removed.

    A path the client sent as `//host/...` is a protocol-relative URL in a `Location`
    header, so the target passes the same vetting Django's own `LoginView` applies.
    """
    remaining = request.GET.copy()
    remaining.pop(QUERY_PARAM, None)
    target = request.path
    if remaining:
        target = f"{target}?{remaining.urlencode()}"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        return HOME_PATH
    return target
