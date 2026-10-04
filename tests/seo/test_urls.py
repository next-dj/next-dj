from next.seo import urls
from next.seo.routes import SeoPatterns
from next.seo.views import robots_view, sitemap_view


class TestSeoUrls:
    """`next.seo.urls` mounts the lazy routes under the `next_seo` namespace."""

    def test_the_namespace_and_the_lazy_patterns(self) -> None:
        assert urls.app_name == "next_seo"
        assert isinstance(urls.urlpatterns, SeoPatterns)
        assert urls.urlpatterns.patterns == urls.PATTERNS

    def test_the_routes_and_their_views(self) -> None:
        assert [pattern.callback for pattern in urls.PATTERNS] == [
            sitemap_view,
            sitemap_view,
            robots_view,
        ]
