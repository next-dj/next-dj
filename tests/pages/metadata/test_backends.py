import re
from collections.abc import Iterable
from typing import ClassVar, override

import pytest
from django.test import override_settings
from django.utils import translation
from django.utils.functional import lazy
from django.utils.safestring import SafeString

from next.errors import (
    AbstractBackendError,
    SettingImportError,
    SettingNotSubclassError,
)
from next.pages.metadata import (
    Article,
    Book,
    Feed,
    HtmlMetadataRenderer,
    Icon,
    Link,
    Metadata,
    MetadataRenderer,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Profile,
    ResolvedMetadata,
    ThemeColor,
    Twitter,
    TwitterImage,
    TwitterPlayer,
    resolve_metadata,
)
from next.pages.metadata.backends import metadata_renderer
from tests.support import BASE


def _resolved(**values: object) -> ResolvedMetadata:
    defaults: dict[str, object] = {
        "title": None,
        "description": None,
        "noindex": False,
        "robots": None,
        "googlebot": None,
        "canonical": None,
        "alternates": (),
        "verification": (),
        "other": (),
        "og": None,
        "twitter": None,
        "jsonld": (),
        "source": Metadata(),
    }
    return ResolvedMetadata(**{**defaults, **values})


FULL = _resolved(
    title="Wallet",
    viewport="width=device-width, initial-scale=1, viewport-fit=cover",
    theme_color=(
        ThemeColor("#fff", "(prefers-color-scheme: light)"),
        ThemeColor("#111"),
    ),
    color_scheme="light dark",
    description="Money",
    keywords=("rockets", "acme"),
    robots="index, follow",
    googlebot="noimageindex",
    canonical=f"{BASE}/wallet/",
    alternates=(("en", f"{BASE}/wallet/"), ("de", f"{BASE}/de/wallet/")),
    feeds=(
        Feed(f"{BASE}/feed.xml", "application/rss+xml", "Acme blog"),
        Feed(f"{BASE}/atom.xml", "application/atom+xml"),
    ),
    icons=(
        Icon("icon", f"{BASE}/icon.svg", sizes="any", type="image/svg+xml"),
        Icon(
            "apple-touch-icon", f"{BASE}/apple.png", sizes="180x180", type="image/png"
        ),
        Icon("mask-icon", f"{BASE}/mask.svg", color="#1d4ed8"),
    ),
    manifest=f"{BASE}/manifest.webmanifest",
    links=(
        Link(
            "preconnect", "https://fonts.gstatic.com", (("crossorigin", "anonymous"),)
        ),
        Link(
            "preload",
            f"{BASE}/font.woff2",
            (("as", "font"), ("type", "font/woff2"), ("crossorigin", "anonymous")),
        ),
    ),
    verification=(
        ("google-site-verification", "g1"),
        ("yandex-verification", "y"),
        ("msvalidate.01", "b"),
        ("p:domain_verify", "p"),
        ("facebook-domain-verification", "f"),
        ("baidu-site-verification", "bd"),
    ),
    other=(("application-name", "Acme"),),
    og=OpenGraph(
        title="Wallet",
        description="Money",
        url=f"{BASE}/wallet/",
        type="article",
        site_name="Acme",
        locale="en_GB",
        locale_alternates=("de_DE",),
        determiner="the",
        images=(
            OpenGraphImage(
                url=f"{BASE}/a.png",
                secure_url=f"{BASE}/s.png",
                type="image/png",
                width=1,
                height=2,
                alt="A",
            ),
        ),
        videos=(
            OpenGraphVideo(f"{BASE}/v.mp4", type="video/mp4", width=640, height=360),
        ),
        audio=(OpenGraphAudio(f"{BASE}/a.mp3", type="audio/mpeg"),),
        article=Article(
            published_time="2026-01-02T03:04:05+00:00",
            modified_time="2026-02-03",
            authors=("Ann", "Bob"),
            section="Finance",
            tags=("money", "apps"),
        ),
        profile=Profile(first_name="Ann", username="ann"),
        book=Book(
            authors=("Ann",), isbn="978-3", release_date="2026-01-02", tags=("x",)
        ),
    ),
    properties=(("fb:app_id", "123"), ("product:price:amount", "49.00")),
    twitter=Twitter(
        card="player",
        site="@acme",
        site_id="1",
        creator="@ann",
        creator_id="2",
        title="Tw",
        description="TwD",
        images=(TwitterImage(f"{BASE}/t.png", "Alt"),),
        player=TwitterPlayer(f"{BASE}/p", 640, 360, f"{BASE}/s.mp4"),
    ),
    jsonld=(
        {"@type": "WebPage"},
        {"@type": "Article"},
        {"@context": "https://example.org/vocab", "@type": "Custom"},
    ),
)
FULL_LINES = (
    "<title>Wallet</title>",
    (
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover">'
    ),
    '<meta name="theme-color" content="#fff" media="(prefers-color-scheme: light)">',
    '<meta name="theme-color" content="#111">',
    '<meta name="color-scheme" content="light dark">',
    '<meta name="description" content="Money">',
    '<meta name="keywords" content="rockets, acme">',
    '<meta name="robots" content="index, follow">',
    '<meta name="googlebot" content="noimageindex">',
    f'<link rel="canonical" href="{BASE}/wallet/">',
    f'<link rel="alternate" hreflang="en" href="{BASE}/wallet/">',
    f'<link rel="alternate" hreflang="de" href="{BASE}/de/wallet/">',
    (
        '<link rel="alternate" type="application/rss+xml" title="Acme blog" '
        f'href="{BASE}/feed.xml">'
    ),
    f'<link rel="alternate" type="application/atom+xml" href="{BASE}/atom.xml">',
    f'<link rel="icon" href="{BASE}/icon.svg" type="image/svg+xml" sizes="any">',
    (
        f'<link rel="apple-touch-icon" href="{BASE}/apple.png" type="image/png" '
        'sizes="180x180">'
    ),
    f'<link rel="mask-icon" href="{BASE}/mask.svg" color="#1d4ed8">',
    f'<link rel="manifest" href="{BASE}/manifest.webmanifest">',
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">',
    (
        f'<link rel="preload" href="{BASE}/font.woff2" as="font" type="font/woff2" '
        'crossorigin="anonymous">'
    ),
    '<meta name="google-site-verification" content="g1">',
    '<meta name="yandex-verification" content="y">',
    '<meta name="msvalidate.01" content="b">',
    '<meta name="p:domain_verify" content="p">',
    '<meta name="facebook-domain-verification" content="f">',
    '<meta name="baidu-site-verification" content="bd">',
    '<meta name="application-name" content="Acme">',
    '<meta property="og:title" content="Wallet">',
    '<meta property="og:description" content="Money">',
    f'<meta property="og:url" content="{BASE}/wallet/">',
    '<meta property="og:type" content="article">',
    '<meta property="og:site_name" content="Acme">',
    '<meta property="og:locale" content="en_GB">',
    '<meta property="og:locale:alternate" content="de_DE">',
    '<meta property="og:determiner" content="the">',
    f'<meta property="og:image" content="{BASE}/a.png">',
    f'<meta property="og:image:secure_url" content="{BASE}/s.png">',
    '<meta property="og:image:type" content="image/png">',
    '<meta property="og:image:width" content="1">',
    '<meta property="og:image:height" content="2">',
    '<meta property="og:image:alt" content="A">',
    f'<meta property="og:video" content="{BASE}/v.mp4">',
    '<meta property="og:video:type" content="video/mp4">',
    '<meta property="og:video:width" content="640">',
    '<meta property="og:video:height" content="360">',
    f'<meta property="og:audio" content="{BASE}/a.mp3">',
    '<meta property="og:audio:type" content="audio/mpeg">',
    '<meta property="article:published_time" content="2026-01-02T03:04:05+00:00">',
    '<meta property="article:modified_time" content="2026-02-03">',
    '<meta property="article:author" content="Ann">',
    '<meta property="article:author" content="Bob">',
    '<meta property="article:section" content="Finance">',
    '<meta property="article:tag" content="money">',
    '<meta property="article:tag" content="apps">',
    '<meta property="profile:first_name" content="Ann">',
    '<meta property="profile:username" content="ann">',
    '<meta property="book:author" content="Ann">',
    '<meta property="book:isbn" content="978-3">',
    '<meta property="book:release_date" content="2026-01-02">',
    '<meta property="book:tag" content="x">',
    '<meta property="fb:app_id" content="123">',
    '<meta property="product:price:amount" content="49.00">',
    '<meta name="twitter:card" content="player">',
    '<meta name="twitter:site" content="@acme">',
    '<meta name="twitter:site:id" content="1">',
    '<meta name="twitter:creator" content="@ann">',
    '<meta name="twitter:creator:id" content="2">',
    '<meta name="twitter:title" content="Tw">',
    '<meta name="twitter:description" content="TwD">',
    f'<meta name="twitter:image" content="{BASE}/t.png">',
    '<meta name="twitter:image:alt" content="Alt">',
    f'<meta name="twitter:player" content="{BASE}/p">',
    '<meta name="twitter:player:width" content="640">',
    '<meta name="twitter:player:height" content="360">',
    f'<meta name="twitter:player:stream" content="{BASE}/s.mp4">',
    (
        '<script type="application/ld+json">{"@context": "https://schema.org", '
        '"@graph": [{"@type": "WebPage"}, {"@type": "Article"}]}</script>'
    ),
    (
        '<script type="application/ld+json">{"@context": "https://example.org/vocab", '
        '"@type": "Custom"}</script>'
    ),
)


def _lines(resolved: ResolvedMetadata) -> list[str]:
    return HtmlMetadataRenderer().render(resolved).split("\n")


class TestRendererContract:
    """The ABC binds `render`, and the configured renderer is the default."""

    def test_the_abc_cannot_be_instantiated(self) -> None:
        abstract: type = MetadataRenderer
        with pytest.raises(TypeError):
            abstract()

    def test_the_default_renderer_is_the_html_one(self) -> None:
        assert isinstance(metadata_renderer(), HtmlMetadataRenderer)

    def test_nothing_renders_a_safe_empty_string(self) -> None:
        rendered = HtmlMetadataRenderer().render(_resolved())
        assert isinstance(rendered, SafeString)
        assert rendered == ""


class TitleOnlyRenderer(MetadataRenderer):
    @override
    def render(self, resolved: ResolvedMetadata) -> SafeString:
        return SafeString(f"<title>{resolved.title}</title>")


class RobotsFirstRenderer(HtmlMetadataRenderer):
    sections: ClassVar[tuple[str, ...]] = ("robots", "title", "brand")

    @override
    def render_title(self, resolved: ResolvedMetadata) -> Iterable[SafeString]:
        return (SafeString(f"<title>[{resolved.title}]</title>"),)

    def render_brand(self, resolved: ResolvedMetadata) -> Iterable[SafeString]:
        return (SafeString('<meta name="brand" content="acme">'),)


TITLE_ONLY = f"{__name__}.TitleOnlyRenderer"


def _renderer_setting(dotted: object) -> dict[str, object]:
    return {"METADATA": {"RENDERER": dotted}}


class TestConfiguredRenderer:
    """`METADATA["RENDERER"]` names the class the tag renders through."""

    def test_the_renderer_is_built_once_per_reload(self) -> None:
        first = metadata_renderer()
        assert metadata_renderer() is first
        with override_settings(NEXT_FRAMEWORK={"METADATA": {}}):
            assert metadata_renderer() is not first
            assert isinstance(metadata_renderer(), HtmlMetadataRenderer)

    def test_the_setting_names_the_renderer(self) -> None:
        with override_settings(NEXT_FRAMEWORK=_renderer_setting(TITLE_ONLY)):
            renderer = metadata_renderer()
        assert type(renderer).__name__ == "TitleOnlyRenderer"
        assert renderer.render(_resolved(title="T")) == "<title>T</title>"
        assert isinstance(metadata_renderer(), HtmlMetadataRenderer)

    def test_a_class_outside_the_family_is_refused(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=_renderer_setting("next.pages.Metadata")),
            pytest.raises(SettingNotSubclassError) as caught,
        ):
            metadata_renderer()
        assert caught.value.scope == "METADATA"
        assert "NEXT_FRAMEWORK['METADATA']['RENDERER']" in str(caught.value)

    def test_a_value_that_is_no_dotted_path_is_refused(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=_renderer_setting(42)),
            pytest.raises(SettingNotSubclassError, match="42 is not a"),
        ):
            metadata_renderer()

    def test_a_path_that_does_not_import_is_refused(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=_renderer_setting("nope.Renderer")),
            pytest.raises(SettingImportError) as caught,
        ):
            metadata_renderer()
        assert caught.value.setting == "RENDERER"

    def test_the_abstract_root_is_refused(self) -> None:
        dotted = "next.pages.MetadataRenderer"
        with (
            override_settings(NEXT_FRAMEWORK=_renderer_setting(dotted)),
            pytest.raises(AbstractBackendError),
        ):
            metadata_renderer()


class TestSections:
    """A subclass reorders, extends and overrides the sections through public hooks."""

    def test_a_subclass_orders_and_extends_the_sections(self) -> None:
        resolved = _resolved(title="T", robots="noindex, nofollow", description="D")
        assert RobotsFirstRenderer().render(resolved).split("\n") == [
            '<meta name="robots" content="noindex, nofollow">',
            "<title>[T]</title>",
            '<meta name="brand" content="acme">',
        ]

    @override_settings(NEXT_FRAMEWORK={"SITE": {"INDEXABLE": False}})
    def test_a_custom_renderer_cannot_lose_a_closed_site(self) -> None:
        resolved = resolve_metadata(Metadata(title="T"), request=None)
        assert '<meta name="robots" content="noindex, nofollow">' in (
            RobotsFirstRenderer().render(resolved)
        )


class TestEscaping:
    """Every value is escaped, the lazy ones under the language of the render."""

    def test_the_description_is_escaped(self) -> None:
        html = HtmlMetadataRenderer().render(
            _resolved(description='"><script>alert(1)</script>')
        )
        assert html == (
            '<meta name="description" '
            'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">'
        )

    def test_the_title_is_escaped(self) -> None:
        assert _lines(_resolved(title="<b>")) == ["<title>&lt;b&gt;</title>"]

    def test_a_safe_title_is_not_escaped_twice(self) -> None:
        title = SafeString("Tom &amp; Jerry")
        assert _lines(_resolved(title=title)) == ["<title>Tom &amp; Jerry</title>"]

    def test_a_lazy_value_follows_the_active_language(self) -> None:
        resolved = _resolved(title=lazy(translation.get_language, str)())
        with translation.override("de"):
            assert _lines(resolved) == ["<title>de</title>"]
        with translation.override("en"):
            assert _lines(resolved) == ["<title>en</title>"]

    def test_jsonld_escapes_the_script_closers(self) -> None:
        html = HtmlMetadataRenderer().render(
            _resolved(jsonld=({"name": "</script><!-- & x"},))
        )
        assert html == (
            '<script type="application/ld+json">{"@context": "https://schema.org", '
            '"@graph": [{"name": "\\u003C/script\\u003E\\u003C!-- \\u0026 x"}]}'
            "</script>"
        )

    def test_jsonld_refuses_a_value_json_cannot_carry(self) -> None:
        with pytest.raises(ValueError, match="not JSON compliant"):
            _lines(_resolved(jsonld=({"v": float("nan")},)))


class TestSparseBlocks:
    """A block renders only the values it carries."""

    def test_an_image_renders_its_url_ahead_of_its_dimensions(self) -> None:
        og = OpenGraph(
            images=(OpenGraphImage(url="https://cdn.example/a.png", width=3),)
        )
        assert _lines(_resolved(og=og)) == [
            '<meta property="og:image" content="https://cdn.example/a.png">',
            '<meta property="og:image:width" content="3">',
        ]

    def test_a_sparse_article_renders_only_what_it_carries(self) -> None:
        og = OpenGraph(article=Article(authors=("Ann",), tags=("x",)))
        assert _lines(_resolved(og=og)) == [
            '<meta property="article:author" content="Ann">',
            '<meta property="article:tag" content="x">',
        ]

    def test_unresolved_true_alternates_render_no_line(self) -> None:
        og = OpenGraph(locale="en_US", locale_alternates=True)
        assert _lines(_resolved(og=og)) == [
            '<meta property="og:locale" content="en_US">'
        ]

    def test_a_twitter_image_without_alt_and_no_player(self) -> None:
        twitter = Twitter(images=(TwitterImage("https://acme.example/t.png"),))
        assert _lines(_resolved(twitter=twitter)) == [
            '<meta name="twitter:image" content="https://acme.example/t.png">'
        ]

    def test_a_player_renders_only_what_it_carries(self) -> None:
        twitter = Twitter(player=TwitterPlayer("https://acme.example/p", 1, 2))
        assert _lines(_resolved(twitter=twitter)) == [
            '<meta name="twitter:player" content="https://acme.example/p">',
            '<meta name="twitter:player:width" content="1">',
            '<meta name="twitter:player:height" content="2">',
        ]

    def test_a_foreign_context_alone_renders_no_graph(self) -> None:
        node = {"@context": "https://example.org/vocab", "@type": "X"}
        assert _lines(_resolved(jsonld=(node,))) == [
            (
                '<script type="application/ld+json">'
                '{"@context": "https://example.org/vocab", "@type": "X"}</script>'
            )
        ]

    def test_a_link_attribute_is_escaped(self) -> None:
        link = Link("me", "https://acme.example/", (("title", '"><x'),))
        assert _lines(_resolved(links=(link,))) == [
            '<link rel="me" href="https://acme.example/" title="&quot;&gt;&lt;x">'
        ]

    def test_googlebot_renders_alone(self) -> None:
        assert _lines(_resolved(googlebot="none")) == [
            '<meta name="googlebot" content="none">'
        ]


class TestOutputOrder:
    """A fully populated value renders every line in the section order."""

    def test_every_line_renders_in_the_documented_order(self) -> None:
        assert _lines(FULL) == list(FULL_LINES)

    def test_lines_are_joined_by_newlines_only(self) -> None:
        html = HtmlMetadataRenderer().render(FULL)
        assert re.fullmatch(r"(<[^\n]+>\n)*<[^\n]+>", html)
