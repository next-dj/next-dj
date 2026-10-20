from django.contrib.staticfiles.views import serve as serve_static
from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path


def serve_tenant_static(request: HttpRequest, slug: str, path: str) -> HttpResponse:
    """Serve a co-located asset under the per-tenant prefix, discarding the slug.

    Development only, a real deployment points a CDN at `STATIC_URL` instead.
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
