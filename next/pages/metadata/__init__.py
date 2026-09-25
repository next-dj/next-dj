"""Page metadata as a folded chain of settings defaults and `page.py` segments."""

from . import providers
from .backends import (
    HtmlMetadataRenderer,
    MetadataRenderer,
    absolute_url,
    render_metadata,
)
from .chain import (
    PARENT_KEY,
    ChainEntry,
    ChainSource,
    MetadataThunk,
    chain_entry,
    resolve_metadata,
    static_metadata,
    templated_title,
)
from .defaults import (
    MetadataOptions,
    forget_site_defaults,
    metadata_options,
    site_segment,
)
from .merge import apply_title_template, fold_metadata
from .placeholders import (
    PLACEHOLDERS,
    parse_template,
    substitute_title,
    template_has_title,
    title_lazy,
)
from .registry import PageMetadataEntry, PageMetadataRegistry
from .schema import (
    EMPTY_METADATA,
    Alternates,
    Article,
    Metadata,
    MetadataDict,
    OpenGraph,
    OpenGraphImage,
    Robots,
    Segment,
    SiteMetadataDict,
    Text,
    TitleSpec,
    Twitter,
    Verification,
    normalize_metadata,
)


__all__ = [
    "EMPTY_METADATA",
    "PARENT_KEY",
    "PLACEHOLDERS",
    "Alternates",
    "Article",
    "ChainEntry",
    "ChainSource",
    "HtmlMetadataRenderer",
    "Metadata",
    "MetadataDict",
    "MetadataOptions",
    "MetadataRenderer",
    "MetadataThunk",
    "OpenGraph",
    "OpenGraphImage",
    "PageMetadataEntry",
    "PageMetadataRegistry",
    "Robots",
    "Segment",
    "SiteMetadataDict",
    "Text",
    "TitleSpec",
    "Twitter",
    "Verification",
    "absolute_url",
    "apply_title_template",
    "chain_entry",
    "fold_metadata",
    "forget_site_defaults",
    "metadata_options",
    "normalize_metadata",
    "parse_template",
    "providers",
    "render_metadata",
    "resolve_metadata",
    "site_segment",
    "static_metadata",
    "substitute_title",
    "template_has_title",
    "templated_title",
    "title_lazy",
]
