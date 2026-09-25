"""The SEO routes for mounting at the host root when `next.urls` sits under a prefix."""

from django.urls import path

from .views import robots, sitemap


app_name = "next_seo"

urlpatterns = [
    path("sitemap.xml", sitemap, name="sitemap"),
    path("sitemap-<slug:section>.xml", sitemap, name="sitemap_section"),
    path("robots.txt", robots, name="robots"),
]
