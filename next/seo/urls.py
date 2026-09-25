"""The SEO routes for mounting at the host root when `next.urls` sits under a prefix."""

from django.urls import path

from .views import HOST_ROOT_NAMESPACE, robots_view, sitemap_view


app_name = HOST_ROOT_NAMESPACE

urlpatterns = [
    path("sitemap.xml", sitemap_view, name="sitemap"),
    path("sitemap-<slug:section>.xml", sitemap_view, name="sitemap_section"),
    path("robots.txt", robots_view, name="robots"),
]
