from django.urls import URLPattern

from next.seo import urls, views


class TestSeoUrls:
    def test_the_namespace_and_the_route_names(self) -> None:
        assert urls.app_name == "next_seo"
        assert [pattern.name for pattern in urls.urlpatterns] == [
            "sitemap",
            "sitemap_section",
            "robots",
        ]

    def test_the_routes_and_their_views(self) -> None:
        assert all(isinstance(pattern, URLPattern) for pattern in urls.urlpatterns)
        assert [str(pattern.pattern) for pattern in urls.urlpatterns] == [
            "sitemap.xml",
            "sitemap-<slug:section>.xml",
            "robots.txt",
        ]
        assert [pattern.callback for pattern in urls.urlpatterns] == [
            views.sitemap,
            views.sitemap,
            views.robots,
        ]
