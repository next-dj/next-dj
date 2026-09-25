import next.seo


SEO_EXPORTS = {
    "Entry",
    "RouteSitemap",
    "Rule",
    "SitemapOriginError",
    "SitemapTrailError",
    "checks",
    "seo_manager",
    "signals",
    "sitemap",
}


class TestSeoPublicSurface:
    """The curated `next.seo` surface names exactly what it exports."""

    def test_exported_names_are_pinned(self) -> None:
        assert set(next.seo.__all__) == SEO_EXPORTS
        assert all(hasattr(next.seo, name) for name in SEO_EXPORTS)
