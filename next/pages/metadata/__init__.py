"""Page metadata as a folded chain of settings defaults and `page.py` segments."""

from . import providers
from .backends import HtmlMetadataRenderer, MetadataRenderer, render_metadata
from .chain import MetadataThunk, chain_entry, chain_title
from .normalize import normalize_metadata, normalize_site_metadata
from .placeholders import template_has_title
from .registry import PageMetadataEntry, PageMetadataRegistry
from .schema import Metadata, MetadataDict, Segment, SiteMetadataDict, Text
from .scope import (
    SITE_SOURCE,
    forget_metadata_scope,
    metadata_options,
    page_noindex,
    site_segment,
)


__all__ = [
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
]
