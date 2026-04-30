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


def is_debug_fallback_enabled() -> bool:
    """Return whether the DEBUG-only query/cookie fallbacks are active."""
    return bool(settings.DEBUG)


class TenantMiddleware:
    """Resolve the active tenant for every request.

    Production contract is the `X-Tenant` header. When the DEBUG
    fallback is active, the middleware also accepts a
    `?tenant=<slug>` query parameter and a `next_tenant` cookie. The
    query-parameter path is a developer affordance for browser-based
    demos. It is fully disabled in production.
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
            return HttpResponseBadRequest(
                "Missing X-Tenant header.\n"
                "Send a request with X-Tenant: <slug>. In DEBUG you may also "
                "pass ?tenant=<slug> for a browser demo.",
            )

        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist:
            response = HttpResponse(f"Unknown tenant slug {slug!r}.", status=404)
            if slug_came_from_cookie:
                response.delete_cookie(COOKIE_NAME)
            return response

        if debug_query:
            response = HttpResponseRedirect(_strip_tenant_query(request))
            response.set_cookie(
                COOKIE_NAME,
                slug,
                httponly=True,
                samesite="Lax",
            )
            return response

        request.tenant = tenant  # type: ignore[attr-defined]
        return self._get_response(request)


def _resolve_tenant_slug(
    request: HttpRequest,
) -> tuple[str | None, bool, bool]:
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
    if remaining:
        return f"{request.path}?{remaining.urlencode()}"
    return request.path
