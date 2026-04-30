from django.contrib.staticfiles.views import serve as serve_static
from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path


def serve_tenant_static(
    request: HttpRequest,
    slug: str,
    path: str,
) -> HttpResponse:
    """Serve a co-located static asset under the per-tenant URL prefix.

    The custom `TenantPrefixStaticBackend` rewrites every asset URL to
    `/_t/<slug>/static/...` so a per-tenant CDN can cache them. In the
    development server we still want the file to be served, so this
    view forwards the request to Django's staticfiles serve view after
    discarding the slug. A real deployment would point a CDN at
    `STATIC_URL` and let the prefix decorate cache keys instead of
    routing.
    """
    del slug
    return serve_static(request, path)


urlpatterns = [
    re_path(
        r"^_t/(?P<slug>[^/]+)/static/(?P<path>.*)$",
        serve_tenant_static,
        name="tenant_static",
    ),
    path("", include("next.urls")),
]
