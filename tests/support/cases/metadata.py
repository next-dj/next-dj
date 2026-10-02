from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from next.pages.metadata import (
    RESET,
    AlternatesDict,
    ArticleDict,
    BookDict,
    FeedDict,
    IconDict,
    IconsDict,
    LinkDict,
    MetadataDict,
    OpenGraph,
    OpenGraphAudioDict,
    OpenGraphDict,
    OpenGraphImageDict,
    OpenGraphVideoDict,
    OtherIconDict,
    ProfileDict,
    Replace,
    Robots,
    RobotsDict,
    SiteMetadataDict,
    SiteTitleDict,
    ThemeColorDict,
    TitleDict,
    TwitterDict,
    TwitterImageDict,
    TwitterPlayerDict,
    VerificationDict,
    ViewportDict,
)
from next.pages.metadata.normalize import normalize_metadata, normalize_site_metadata
from next.site import SiteOriginError


if TYPE_CHECKING:
    from collections.abc import Callable

    from next.pages.metadata.markers import Segment


class Size(Enum):
    """A plain enum, which JSON cannot write."""

    LARGE = "large"


@dataclass(frozen=True, slots=True)
class MetadataShapeCase:
    """One raw metadata value `normalize_metadata` rejects, and why."""

    id: str
    raw: object
    site: bool
    detail_fragment: str


METADATA_SHAPE_CASES: tuple[MetadataShapeCase, ...] = (
    MetadataShapeCase(
        "non_mapping", ["x"], False, "declares metadata as 'list', expected a mapping"
    ),
    MetadataShapeCase(
        "unknown_top_key",
        {"foo": 1},
        False,
        "declares metadata key 'foo', expected one of",
    ),
    MetadataShapeCase(
        "non_str_top_key", {1: "x"}, False, "declares metadata key '1', expected one of"
    ),
    MetadataShapeCase(
        "site_unknown_key",
        {"base": "https://acme.example"},
        True,
        "declares metadata key 'base', expected one of",
    ),
    MetadataShapeCase(
        "description_not_text",
        {"description": 5},
        False,
        "declares metadata key 'description' as 'int', expected text",
    ),
    MetadataShapeCase(
        "canonical_not_a_url",
        {"canonical": 1},
        False,
        "declares metadata key 'canonical' as 'int', expected a URL string or True",
    ),
    MetadataShapeCase(
        "canonical_false",
        {"canonical": False},
        False,
        "declares metadata key 'canonical' as False, write RESET to drop an "
        "inherited canonical",
    ),
    MetadataShapeCase(
        "title_wrong_type",
        {"title": 5},
        False,
        "declares metadata key 'title' as 'int', expected text or a mapping",
    ),
    MetadataShapeCase(
        "title_unknown_key",
        {"title": {"foo": "x"}},
        False,
        "declares metadata key 'title.foo', expected one of",
    ),
    MetadataShapeCase(
        "title_template_not_text",
        {"title": {"template": 5}},
        False,
        "declares metadata key 'title.template' as 'int', expected text",
    ),
    MetadataShapeCase(
        "title_part_reset",
        {"title": {"template": RESET}},
        False,
        "declares metadata key 'title.template' as 'Replace', expected text",
    ),
    MetadataShapeCase(
        "site_bare_title",
        {"title": "Acme"},
        True,
        "declares metadata key 'title' as 'str', expected a mapping",
    ),
    MetadataShapeCase(
        "site_absolute_title",
        {"title": {"absolute": "Acme"}},
        True,
        "declares metadata key 'title.absolute', expected one of",
    ),
    MetadataShapeCase(
        "robots_wrong_type",
        {"robots": 1},
        False,
        "declares metadata key 'robots' as 'int', expected a string or a mapping",
    ),
    MetadataShapeCase(
        "robots_unknown_key",
        {"robots": {"foo": True}},
        False,
        "declares metadata key 'robots.foo', expected one of",
    ),
    MetadataShapeCase(
        "robots_flag_not_bool",
        {"robots": {"index": "yes"}},
        False,
        "declares metadata key 'robots.index' as 'str', expected a bool",
    ),
    MetadataShapeCase(
        "robots_limit_given_bool",
        {"robots": {"max_snippet": True}},
        False,
        "declares metadata key 'robots.max_snippet' as 'bool', expected an int",
    ),
    MetadataShapeCase(
        "googlebot_nests_googlebot",
        {"robots": {"googlebot": {"googlebot": "none"}}},
        False,
        "declares metadata key 'robots.googlebot.googlebot', expected one of",
    ),
    MetadataShapeCase(
        "og_unknown_key",
        {"og": {"foo": 1}},
        False,
        "declares metadata key 'og.foo', expected one of",
    ),
    MetadataShapeCase(
        "og_images_not_sequence",
        {"og": {"images": "/a.png"}},
        False,
        "declares metadata key 'og.images' as 'str', expected a sequence where "
        "each item is a URL string or a mapping",
    ),
    MetadataShapeCase(
        "og_image_wrong_item",
        {"og": {"images": [1]}},
        False,
        "declares metadata key 'og.images[0]' as 'int', expected a URL string or a "
        "mapping",
    ),
    MetadataShapeCase(
        "og_image_unknown_key",
        {"og": {"images": [{"src": "/a.png"}]}},
        False,
        "declares metadata key 'og.images[0].src', expected one of",
    ),
    MetadataShapeCase(
        "og_image_width_not_int",
        {"og": {"images": ["/a.png", {"width": "1"}]}},
        False,
        "declares metadata key 'og.images[1].width' as 'str', expected an int",
    ),
    MetadataShapeCase(
        "og_image_url_not_a_string",
        {"og": {"images": [{"url": 1}]}},
        False,
        "declares metadata key 'og.images[0].url' as 'int', expected a URL string",
    ),
    MetadataShapeCase(
        "og_article_unknown_key",
        {"og": {"article": {"foo": 1}}},
        False,
        "declares metadata key 'og.article.foo', expected one of",
    ),
    MetadataShapeCase(
        "og_article_author_not_str",
        {"og": {"article": {"authors": ["ada", 1]}}},
        False,
        "declares metadata key 'og.article.authors[1]' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "og_article_time_wrong_type",
        {"og": {"article": {"published_time": 1}}},
        False,
        "declares metadata key 'og.article.published_time' as 'int', expected a "
        "datetime, a date or a string",
    ),
    MetadataShapeCase(
        "twitter_unknown_key",
        {"twitter": {"foo": 1}},
        False,
        "declares metadata key 'twitter.foo', expected one of",
    ),
    MetadataShapeCase(
        "twitter_images_not_sequence",
        {"twitter": {"images": 1}},
        False,
        "declares metadata key 'twitter.images' as 'int', expected a sequence "
        "where each item is a URL string",
    ),
    MetadataShapeCase(
        "twitter_image_not_a_string",
        {"twitter": {"images": [1]}},
        False,
        "declares metadata key 'twitter.images[0]' as 'int', expected a URL string",
    ),
    MetadataShapeCase(
        "alternates_unknown_key",
        {"alternates": {"foo": 1}},
        False,
        "declares metadata key 'alternates.foo', expected one of",
    ),
    MetadataShapeCase(
        "alternates_languages_wrong_values",
        {"alternates": {"languages": {"en": 1}}},
        False,
        "declares metadata key 'alternates.languages' as 'dict', expected a bool "
        "or a mapping of language codes to URLs",
    ),
    MetadataShapeCase(
        "alternates_two_x_defaults",
        {"alternates": {"languages": {"x-default": "/"}, "x_default": "/all/"}},
        False,
        "declares both metadata key 'alternates.x_default' and an 'x-default' "
        "language, keep one",
    ),
    MetadataShapeCase(
        "verification_unknown_engine",
        {"verification": {"duck": "x"}},
        False,
        "declares metadata key 'verification.duck', expected one of",
    ),
    MetadataShapeCase(
        "verification_wrong_scalar",
        {"verification": {"google": 1}},
        False,
        "declares metadata key 'verification.google' as 'int', expected a string "
        "or a sequence where each item is a string",
    ),
    MetadataShapeCase(
        "verification_wrong_item",
        {"verification": {"google": ["a", 1]}},
        False,
        "declares metadata key 'verification.google[1]' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "other_not_mapping",
        {"other": [("a", "b")]},
        False,
        "declares metadata key 'other' as 'list', expected a mapping of names to "
        "text or sequences of text",
    ),
    MetadataShapeCase(
        "other_value_not_text",
        {"other": {"a": 1}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping of names to "
        "text or sequences of text",
    ),
    MetadataShapeCase(
        "other_sequence_with_a_number",
        {"other": {"a": ["x", 1]}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "other_replaced_value_not_text",
        {"other": {"a": Replace(1)}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "other_value_bytes",
        {"other": {"a": b"x"}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "other_key_not_str",
        {"other": {1: "x"}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "jsonld_wrong_type",
        {"jsonld": "x"},
        False,
        "declares metadata key 'jsonld' as 'str', expected a node, a mapping or a "
        "sequence of them",
    ),
    MetadataShapeCase(
        "jsonld_wrong_item",
        {"jsonld": [{"@type": "Thing"}, 1]},
        False,
        "declares metadata key 'jsonld[1]' as 'int', expected a node or a mapping",
    ),
    MetadataShapeCase(
        "jsonld_not_a_number",
        {"jsonld": {"rating": {"value": float("nan")}}},
        False,
        "declares metadata key 'jsonld.rating.value' as nan, expected a finite number",
    ),
    MetadataShapeCase(
        "jsonld_infinite_in_a_list",
        {"jsonld": [{"values": [1.0, float("inf")]}]},
        False,
        "declares metadata key 'jsonld[0].values[1]' as inf, expected a finite number",
    ),
    MetadataShapeCase(
        "jsonld_set",
        {"jsonld": {"@type": "Thing", "keywords": {"a", "b"}}},
        False,
        "declares metadata key 'jsonld.keywords' as 'set', expected a JSON value",
    ),
    MetadataShapeCase(
        "jsonld_bytes_in_a_list",
        {"jsonld": [{"values": ["a", b"b"]}]},
        False,
        "declares metadata key 'jsonld[0].values[1]' as 'bytes', expected a JSON value",
    ),
    MetadataShapeCase(
        "jsonld_plain_enum",
        {"jsonld": {"@type": "Thing", "size": Size.LARGE}},
        False,
        "declares metadata key 'jsonld.size' as 'Size', expected a JSON value",
    ),
    MetadataShapeCase(
        "jsonld_object",
        {"jsonld": {"@type": "Thing", "owner": object()}},
        False,
        "declares metadata key 'jsonld.owner' as 'object', expected a JSON value",
    ),
    MetadataShapeCase(
        "links_without_href",
        {"links": [{"rel": "preconnect"}]},
        False,
        "declares metadata key 'links[0]' without 'href'",
    ),
    MetadataShapeCase(
        "links_unknown_attribute",
        {"links": [{"rel": "me", "href": "/", "foo": 1}]},
        False,
        "declares metadata key 'links[0].foo', expected one of",
    ),
    MetadataShapeCase(
        "links_as_not_a_string",
        {"links": [{"rel": "preload", "href": "/f", "as": 1}]},
        False,
        "declares metadata key 'links[0].as' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "icons_unknown_group",
        {"icons": {"favicon": "/f.ico"}},
        False,
        "declares metadata key 'icons.favicon', expected one of",
    ),
    MetadataShapeCase(
        "icons_group_wrong_type",
        {"icons": {"icon": 1}},
        False,
        "declares metadata key 'icons.icon' as 'int', expected a URL string or a "
        "mapping or a sequence where each item is a URL string or a mapping",
    ),
    MetadataShapeCase(
        "icon_without_url",
        {"icons": {"icon": {"sizes": "16x16"}}},
        False,
        "declares metadata key 'icons.icon' without 'url'",
    ),
    MetadataShapeCase(
        "other_icon_without_rel",
        {"icons": {"other": [{"url": "/m.svg"}]}},
        False,
        "declares metadata key 'icons.other[0]' without 'rel'",
    ),
    MetadataShapeCase(
        "viewport_wrong_type",
        {"viewport": 1},
        False,
        "declares metadata key 'viewport' as 'int', expected a string or a mapping",
    ),
    MetadataShapeCase(
        "viewport_scale_not_a_number",
        {"viewport": {"initial_scale": "1"}},
        False,
        "declares metadata key 'viewport.initial_scale' as 'str', expected a number",
    ),
    MetadataShapeCase(
        "viewport_scalable_not_bool",
        {"viewport": {"user_scalable": "no"}},
        False,
        "declares metadata key 'viewport.user_scalable' as 'str', expected a bool",
    ),
    MetadataShapeCase(
        "theme_color_without_color",
        {"theme_color": [{"media": "(prefers-color-scheme: dark)"}]},
        False,
        "declares metadata key 'theme_color[0]' without 'color'",
    ),
    MetadataShapeCase(
        "keywords_wrong_type",
        {"keywords": 1},
        False,
        "declares metadata key 'keywords' as 'int', expected text or a sequence "
        "where each item is text",
    ),
    MetadataShapeCase(
        "breadcrumb_true",
        {"breadcrumb": True},
        False,
        "declares metadata key 'breadcrumb' as 'bool', expected text or False",
    ),
    MetadataShapeCase(
        "site_breadcrumb",
        {"breadcrumb": "Home"},
        True,
        "declares metadata key 'breadcrumb', expected one of",
    ),
    MetadataShapeCase(
        "feed_without_type",
        {"alternates": {"feeds": [{"url": "/feed.xml"}]}},
        False,
        "declares metadata key 'alternates.feeds[0]' without 'type'",
    ),
    MetadataShapeCase(
        "og_image_without_url",
        {"og": {"images": [{"alt": "x", "width": 5}]}},
        False,
        "declares metadata key 'og.images[0]' without 'url'",
    ),
    MetadataShapeCase(
        "og_video_without_url",
        {"og": {"videos": [{"type": "video/mp4"}]}},
        False,
        "declares metadata key 'og.videos[0]' without 'url'",
    ),
    MetadataShapeCase(
        "og_locale_alternates_a_string",
        {"og": {"locale_alternates": "de_DE"}},
        False,
        "declares metadata key 'og.locale_alternates' as 'str', expected a bool or "
        "a sequence where each item is a string",
    ),
    MetadataShapeCase(
        "twitter_player_without_width",
        {"twitter": {"player": {"url": "/p", "height": 1}}},
        False,
        "declares metadata key 'twitter.player' without 'width'",
    ),
    MetadataShapeCase(
        "twitter_image_without_url",
        {"twitter": {"images": [{"alt": "x"}]}},
        False,
        "declares metadata key 'twitter.images[0]' without 'url'",
    ),
    MetadataShapeCase(
        "verification_other_not_text",
        {"verification": {"other": {"baidu": 1}}},
        False,
        "declares metadata key 'verification.other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "jsonld_key_not_str",
        {"jsonld": {"@type": "Thing", 1: "x"}},
        False,
        "declares metadata key 'jsonld' with the key 1, expected string keys",
    ),
)


@dataclass(frozen=True, slots=True)
class UrlSchemeCase:
    """One URL field a page may declare, reached from the top of the metadata."""

    id: str
    build: Callable[[str], dict[str, object]]
    path: str


URL_SCHEME_CASES: tuple[UrlSchemeCase, ...] = (
    UrlSchemeCase("canonical", lambda url: {"canonical": url}, "canonical"),
    UrlSchemeCase("og_url", lambda url: {"og": {"url": url}}, "og.url"),
    UrlSchemeCase("og_image", lambda url: {"og": {"images": [url]}}, "og.images[0]"),
    UrlSchemeCase(
        "og_image_url",
        lambda url: {"og": {"images": [{"url": url}]}},
        "og.images[0].url",
    ),
    UrlSchemeCase(
        "twitter_image", lambda url: {"twitter": {"images": [url]}}, "twitter.images[0]"
    ),
    UrlSchemeCase(
        "hreflang",
        lambda url: {"alternates": {"languages": {"de": url}}},
        "alternates.languages.de",
    ),
    UrlSchemeCase(
        "x_default",
        lambda url: {"alternates": {"x_default": url}},
        "alternates.x_default",
    ),
    UrlSchemeCase("icon", lambda url: {"icons": {"icon": url}}, "icons.icon"),
    UrlSchemeCase(
        "apple_icon", lambda url: {"icons": {"apple": {"url": url}}}, "icons.apple.url"
    ),
    UrlSchemeCase(
        "icon_in_a_list", lambda url: {"icons": {"icon": [url]}}, "icons.icon[0]"
    ),
    UrlSchemeCase(
        "other_icon",
        lambda url: {"icons": {"other": [{"rel": "mask-icon", "url": url}]}},
        "icons.other[0].url",
    ),
    UrlSchemeCase("manifest", lambda url: {"manifest": url}, "manifest"),
    UrlSchemeCase(
        "link", lambda url: {"links": [{"rel": "me", "href": url}]}, "links[0].href"
    ),
    UrlSchemeCase(
        "feed",
        lambda url: {"alternates": {"feeds": [{"url": url, "type": "rss"}]}},
        "alternates.feeds[0].url",
    ),
    UrlSchemeCase(
        "og_image_secure_url",
        lambda url: {"og": {"images": [{"url": "/i", "secure_url": url}]}},
        "og.images[0].secure_url",
    ),
    UrlSchemeCase("og_video", lambda url: {"og": {"videos": [url]}}, "og.videos[0]"),
    UrlSchemeCase(
        "og_video_secure_url",
        lambda url: {"og": {"videos": [{"url": "/v", "secure_url": url}]}},
        "og.videos[0].secure_url",
    ),
    UrlSchemeCase("og_audio", lambda url: {"og": {"audio": [url]}}, "og.audio[0]"),
    UrlSchemeCase(
        "twitter_image_url",
        lambda url: {"twitter": {"images": [{"url": url}]}},
        "twitter.images[0].url",
    ),
    UrlSchemeCase(
        "twitter_player",
        lambda url: {"twitter": {"player": {"url": url, "width": 1, "height": 1}}},
        "twitter.player.url",
    ),
    UrlSchemeCase(
        "twitter_player_stream",
        lambda url: {
            "twitter": {"player": {"url": "/p", "width": 1, "height": 1, "stream": url}}
        },
        "twitter.player.stream",
    ),
)


@dataclass(frozen=True, slots=True)
class MetadataMergeCase:
    """One chain of raw segments and the folded values it must settle on."""

    id: str
    segments_raw: tuple[object, ...]
    expected_title: str | None
    expected_fields: dict[str, object] = field(default_factory=dict)


METADATA_MERGE_CASES: tuple[MetadataMergeCase, ...] = (
    MetadataMergeCase("empty_chain", (), None),
    MetadataMergeCase("single_text", ({"title": "Wallet"},), "Wallet"),
    MetadataMergeCase(
        "template_wraps_child_text",
        (
            {"title": {"template": "{title} · {site_name}"}, "site_name": "Acme"},
            {"title": "Wallet"},
        ),
        "Wallet · Acme",
    ),
    MetadataMergeCase(
        "root_default_stands_bare",
        ({"title": {"template": "{title} · Acme", "default": "Acme"}},),
        "Acme",
    ),
    MetadataMergeCase(
        "default_survives_a_child_without_title",
        (
            {"title": {"template": "{title} · Acme", "default": "Acme"}},
            {"description": "Money"},
        ),
        "Acme",
        {"description": "Money"},
    ),
    MetadataMergeCase(
        "absolute_ignores_template",
        (
            {"title": {"template": "{title} · Acme"}},
            {"title": {"absolute": "Just this"}},
        ),
        "Just this",
    ),
    MetadataMergeCase(
        "nearer_template_replaces_the_root_one",
        (
            {"title": {"template": "{title} · Acme"}},
            {"title": {"template": "{title} | Wallet"}},
            {"title": "Cards"},
        ),
        "Cards | Wallet",
    ),
    MetadataMergeCase(
        "template_applies_to_the_segment_after_it",
        (
            {"title": {"template": "{title} · Acme", "default": "Acme"}},
            {"title": {"template": "{title} | Wallet"}},
        ),
        "Acme",
    ),
    MetadataMergeCase(
        "template_without_title_still_applies",
        ({"title": {"template": "Only Acme"}}, {"title": "Cards"}),
        "Only Acme",
    ),
    MetadataMergeCase(
        "site_name_missing_skips_the_template",
        ({"title": {"template": "{title} · {site_name}"}}, {"title": "Cards"}),
        "Cards",
    ),
    MetadataMergeCase(
        "site_name_reads_the_final_fold",
        (
            {"title": {"template": "{title} · {site_name}"}, "site_name": "Root"},
            {"title": "Cards", "site_name": "Leaf"},
        ),
        "Cards · Leaf",
        {"site_name": "Leaf"},
    ),
    MetadataMergeCase(
        "site_name_declared_after_the_template",
        (
            {"title": {"template": "{title} · {site_name}"}},
            {"title": "Cards", "site_name": "Leaf"},
        ),
        "Cards · Leaf",
    ),
    MetadataMergeCase(
        "later_segment_without_title_keeps_it",
        ({"title": "Wallet"}, {"description": "Money"}),
        "Wallet",
        {"description": "Money"},
    ),
    MetadataMergeCase(
        "nested_block_merges_per_field",
        ({"og": {"type": "website", "title": "Root"}}, {"og": {"title": "Leaf"}}),
        None,
        {"og": OpenGraph(type="website", title="Leaf")},
    ),
    MetadataMergeCase(
        "robots_string_replaces_the_dict",
        ({"robots": {"index": False}}, {"robots": "all"}),
        None,
        {"robots": "all"},
    ),
    MetadataMergeCase(
        "robots_dict_replaces_the_string",
        ({"robots": "all"}, {"robots": {"index": False}}),
        None,
        {"robots": Robots(index=False)},
    ),
    MetadataMergeCase(
        "robots_dicts_merge_per_flag",
        (
            {"robots": {"index": True, "googlebot": {"nosnippet": True}}},
            {"robots": {"follow": False, "googlebot": {"index": False}}},
        ),
        None,
        {
            "robots": Robots(
                index=True, follow=False, googlebot=Robots(index=False, nosnippet=True)
            )
        },
    ),
    MetadataMergeCase(
        "other_merges_by_name_and_jsonld_appends",
        (
            {"other": {"a": "1", "b": "2"}, "jsonld": {"@type": "Root"}},
            {"other": {"c": "3"}, "jsonld": [{"@type": "Leaf"}]},
        ),
        None,
        {
            "other": (("a", "1"), ("b", "2"), ("c", "3")),
            "jsonld": ({"@type": "Root"}, {"@type": "Leaf"}),
        },
    ),
    MetadataMergeCase(
        "empty_other_does_not_clear_the_parent",
        ({"other": {"a": "1"}}, {"other": {}}),
        None,
        {"other": (("a", "1"),)},
    ),
    MetadataMergeCase(
        "scalars_fold_by_nearest",
        (
            {"description": "Root", "site_name": "Acme", "canonical": True},
            {"description": "Leaf", "canonical": "/leaf/"},
        ),
        None,
        {"description": "Leaf", "site_name": "Acme", "canonical": "/leaf/"},
    ),
    MetadataMergeCase(
        "reset_drops_the_title_and_its_template",
        (
            {"title": {"template": "{title} · Acme", "default": "Acme"}},
            {"title": RESET},
            {"title": "Cards"},
        ),
        "Cards",
    ),
)


@dataclass(frozen=True, slots=True)
class TitleTemplateCase:
    """One title template and either its substitution or the error it raises."""

    id: str
    template: str
    values: dict[str, object] = field(default_factory=dict)
    expected: str | None = None
    error_fragment: str | None = None


TITLE_TEMPLATE_CASES: tuple[TitleTemplateCase, ...] = (
    TitleTemplateCase(
        "both_placeholders",
        "{title} · {site_name}",
        {"title": "Wallet", "site_name": "Acme"},
        expected="Wallet · Acme",
    ),
    TitleTemplateCase("plain_text", "Acme", {"title": "Wallet"}, expected="Acme"),
    TitleTemplateCase(
        "escaped_braces",
        "{{title}} {title}",
        {"title": "Wallet"},
        expected="{title} Wallet",
    ),
    TitleTemplateCase(
        "site_name_alone",
        "{site_name}",
        {"title": "Wallet", "site_name": "Acme"},
        expected="Acme",
    ),
    TitleTemplateCase(
        "attribute_access",
        "{x.__class__}",
        error_fragment="names the placeholder 'x.__class__', expected one of "
        "site_name, title",
    ),
    TitleTemplateCase(
        "index_access", "{c[0]}", error_fragment="names the placeholder 'c[0]'"
    ),
    TitleTemplateCase("positional", "{0}", error_fragment="names the placeholder '0'"),
    TitleTemplateCase("auto_numbered", "{}", error_fragment="names the placeholder ''"),
    TitleTemplateCase(
        "conversion",
        "{title!r}",
        error_fragment="formats the placeholder 'title', expected a bare {title}",
    ),
    TitleTemplateCase(
        "format_spec",
        "{title:>10}",
        error_fragment="formats the placeholder 'title', expected a bare {title}",
    ),
    TitleTemplateCase(
        "unbalanced_open",
        "{title",
        error_fragment="is malformed, expected '}' before end of string",
    ),
    TitleTemplateCase(
        "unbalanced_close",
        "}",
        error_fragment="is malformed, Single '}' encountered in format string",
    ),
    TitleTemplateCase(
        "unknown_name",
        "{nope}",
        error_fragment="names the placeholder 'nope', expected one of site_name, title",
    ),
    TitleTemplateCase(
        "missing_value",
        "{title} · {site_name}",
        {"title": "Wallet"},
        error_fragment="needs a value for 'site_name', which the chain did not provide",
    ),
)


@dataclass(frozen=True, slots=True)
class RobotsCase:
    """One robots value and the content its meta folds to."""

    id: str
    robots: Robots | str
    expected: str


ROBOTS_CASES: tuple[RobotsCase, ...] = (
    RobotsCase("string_as_is", "noindex, nofollow", "noindex, nofollow"),
    RobotsCase("empty", Robots(), ""),
    RobotsCase("index_follow", Robots(index=True, follow=True), "index, follow"),
    RobotsCase("noindex", Robots(index=False), "noindex"),
    RobotsCase("nofollow", Robots(follow=False), "nofollow"),
    RobotsCase(
        "flags",
        Robots(noarchive=True, nosnippet=True, noimageindex=True, notranslate=True),
        "noarchive, nosnippet, noimageindex, notranslate",
    ),
    RobotsCase("false_flags_stay_out", Robots(noarchive=False, nosnippet=False), ""),
    RobotsCase(
        "limits",
        Robots(
            unavailable_after="2026-12-31",
            max_snippet=20,
            max_image_preview="large",
            max_video_preview=-1,
        ),
        "unavailable_after: 2026-12-31, max-snippet:20, max-image-preview:large, "
        "max-video-preview:-1",
    ),
    RobotsCase(
        "everything_in_order",
        Robots(index=False, follow=True, noarchive=True, max_snippet=0),
        "noindex, follow, noarchive, max-snippet:0",
    ),
)


@dataclass(frozen=True, slots=True)
class AbsoluteUrlCase:
    """One URL to make absolute, the site URL and request path it sees, and the outcome.

    A `path` of `None` means no request, and `error` names what raises over `expected`.
    """

    id: str
    url: str
    site: str | None = None
    path: str | None = None
    expected: str | None = None
    error: type[Exception] | None = None


ABSOLUTE_URL_CASES: tuple[AbsoluteUrlCase, ...] = (
    AbsoluteUrlCase(
        "http_as_is", "http://x.example/a/", expected="http://x.example/a/"
    ),
    AbsoluteUrlCase(
        "https_as_is",
        "https://x.example/a/?q=1",
        site="https://acme.example",
        path="/p/",
        expected="https://x.example/a/?q=1",
    ),
    AbsoluteUrlCase(
        "any_scheme_as_is",
        "mailto:ann@acme.example",
        expected="mailto:ann@acme.example",
    ),
    AbsoluteUrlCase(
        "root_relative_site_first",
        "/a/",
        site="https://acme.example/",
        path="/p/",
        expected="https://acme.example/a/",
    ),
    AbsoluteUrlCase(
        "root_relative_iri_escaped",
        "/мир/?q=a b",
        site="https://acme.example",
        expected="https://acme.example/%D0%BC%D0%B8%D1%80/?q=a%20b",
    ),
    AbsoluteUrlCase(
        "root_relative_request", "/a/", path="/p/", expected="http://testserver/a/"
    ),
    AbsoluteUrlCase("root_relative_nothing", "/a/", error=SiteOriginError),
    AbsoluteUrlCase(
        "dot_relative",
        "./a",
        site="https://acme.example",
        path="/p/q/",
        expected="https://acme.example/p/q/a",
    ),
    AbsoluteUrlCase(
        "bare_relative", "a", path="/p/q/", expected="http://testserver/p/q/a"
    ),
    AbsoluteUrlCase(
        "parent_relative",
        "../a",
        site="https://acme.example",
        path="/p/q/",
        expected="https://acme.example/p/a",
    ),
    AbsoluteUrlCase(
        "relative_to_an_escaped_path",
        "a",
        site="https://acme.example",
        path="/%D0%BC%D0%B8%D1%80/",
        expected="https://acme.example/%D0%BC%D0%B8%D1%80/a",
    ),
    AbsoluteUrlCase(
        "relative_without_request",
        "./a",
        site="https://acme.example",
        error=SiteOriginError,
    ),
)


@dataclass(frozen=True, slots=True)
class SchemaParityCase:
    """One metadata ``TypedDict`` and how a public normaliser reaches its block."""

    id: str
    typed_dict: type
    normalize: Callable[..., Segment]
    nest: Callable[[dict[str, object]], dict[str, object]] = lambda block: block


SCHEMA_PARITY_CASES: tuple[SchemaParityCase, ...] = (
    SchemaParityCase("page", MetadataDict, normalize_metadata),
    SchemaParityCase("site", SiteMetadataDict, normalize_site_metadata),
    SchemaParityCase("title", TitleDict, normalize_metadata, lambda b: {"title": b}),
    SchemaParityCase(
        "site_title", SiteTitleDict, normalize_site_metadata, lambda b: {"title": b}
    ),
    SchemaParityCase("og", OpenGraphDict, normalize_metadata, lambda b: {"og": b}),
    SchemaParityCase(
        "og_image",
        OpenGraphImageDict,
        normalize_metadata,
        lambda b: {"og": {"images": [{**b, "url": "/i.png"}]}},
    ),
    SchemaParityCase(
        "article", ArticleDict, normalize_metadata, lambda b: {"og": {"article": b}}
    ),
    SchemaParityCase(
        "twitter", TwitterDict, normalize_metadata, lambda b: {"twitter": b}
    ),
    SchemaParityCase(
        "alternates", AlternatesDict, normalize_metadata, lambda b: {"alternates": b}
    ),
    SchemaParityCase(
        "verification",
        VerificationDict,
        normalize_metadata,
        lambda b: {"verification": b},
    ),
    SchemaParityCase("robots", RobotsDict, normalize_metadata, lambda b: {"robots": b}),
    SchemaParityCase(
        "og_video",
        OpenGraphVideoDict,
        normalize_metadata,
        lambda b: {"og": {"videos": [{**b, "url": "/v"}]}},
    ),
    SchemaParityCase(
        "og_audio",
        OpenGraphAudioDict,
        normalize_metadata,
        lambda b: {"og": {"audio": [{**b, "url": "/a"}]}},
    ),
    SchemaParityCase(
        "profile", ProfileDict, normalize_metadata, lambda b: {"og": {"profile": b}}
    ),
    SchemaParityCase(
        "book", BookDict, normalize_metadata, lambda b: {"og": {"book": b}}
    ),
    SchemaParityCase(
        "twitter_image",
        TwitterImageDict,
        normalize_metadata,
        lambda b: {"twitter": {"images": [{**b, "url": "/t"}]}},
    ),
    SchemaParityCase(
        "twitter_player",
        TwitterPlayerDict,
        normalize_metadata,
        lambda b: {"twitter": {"player": {**b, "url": "/p", "width": 1, "height": 1}}},
    ),
    SchemaParityCase(
        "feed",
        FeedDict,
        normalize_metadata,
        lambda b: {"alternates": {"feeds": [{**b, "url": "/f", "type": "rss"}]}},
    ),
    SchemaParityCase("icons", IconsDict, normalize_metadata, lambda b: {"icons": b}),
    SchemaParityCase(
        "icon",
        IconDict,
        normalize_metadata,
        lambda b: {"icons": {"icon": {**b, "url": "/i"}}},
    ),
    SchemaParityCase(
        "other_icon",
        OtherIconDict,
        normalize_metadata,
        lambda b: {"icons": {"other": [{**b, "url": "/i", "rel": "mask-icon"}]}},
    ),
    SchemaParityCase(
        "link",
        LinkDict,
        normalize_metadata,
        lambda b: {"links": [{**b, "rel": "me", "href": "/"}]},
    ),
    SchemaParityCase(
        "viewport", ViewportDict, normalize_metadata, lambda b: {"viewport": b}
    ),
    SchemaParityCase(
        "theme_color",
        ThemeColorDict,
        normalize_metadata,
        lambda b: {"theme_color": [{**b, "color": "#fff"}]},
    ),
)
