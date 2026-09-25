import next.pages.metadata


METADATA_EXPORTS = {
    "SITE_SOURCE",
    "HtmlMetadataRenderer",
    "Metadata",
    "MetadataDict",
    "MetadataRenderer",
    "MetadataThunk",
    "PageMetadataEntry",
    "PageMetadataRegistry",
    "Segment",
    "SiteMetadataDict",
    "Text",
    "chain_entry",
    "chain_title",
    "forget_metadata_scope",
    "metadata_options",
    "normalize_metadata",
    "normalize_site_metadata",
    "page_noindex",
    "providers",
    "render_metadata",
    "site_segment",
    "template_has_title",
}


class TestMetadataPublicSurface:
    """The curated `next.pages.metadata` surface names exactly what it exports."""

    def test_exported_names_are_pinned(self) -> None:
        assert set(next.pages.metadata.__all__) == METADATA_EXPORTS
        assert all(hasattr(next.pages.metadata, name) for name in METADATA_EXPORTS)
