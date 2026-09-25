import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import override

import pytest
from django.core.signals import setting_changed
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django.urls import set_urlconf
from django.utils import translation
from django.utils.functional import lazy
from django.utils.safestring import SafeString

import next.pages.metadata.backends as backends_module
from next.conf.signals import settings_reloaded
from next.pages.errors import PageMetadataShapeError, PageMetadataURLError
from next.pages.metadata import (
    EMPTY_METADATA,
    Alternates,
    Article,
    HtmlMetadataRenderer,
    Metadata,
    MetadataRenderer,
    OpenGraph,
    OpenGraphImage,
    Robots,
    Twitter,
    Verification,
    absolute_url,
    render_metadata,
)
from next.pages.metadata.backends import default_renderer, forget_translated_urls
from next.testing import override_next_settings
from tests.support import (
    ABSOLUTE_URL_CASES,
    ROBOTS_CASES,
    AbsoluteUrlCase,
    RobotsCase,
    build_mock_http_request,
)


BASE = "https://acme.example"
I18N = {
    "ROOT_URLCONF": "tests.support.i18n_urls",
    "LANGUAGES": [("en", "English"), ("de", "German")],
    "LANGUAGE_CODE": "en",
    "USE_I18N": True,
}
NOINDEX = {"METADATA": {"NOINDEX": True}}
QUERY = {"METADATA": {"CANONICAL_QUERY": ("q", "page")}}
FULL = Metadata(
    title="Wallet",
    description="Money",
    base=BASE,
    site_name="Acme",
    canonical="/wallet/",
    alternates=Alternates(languages={"en": "/wallet/", "de": "/de/wallet/"}),
    robots=Robots(index=True, follow=True, googlebot="noimageindex"),
    og=OpenGraph(
        type="article",
        locale="en_GB",
        images=(OpenGraphImage(url="/a.png", width=1, height=2, alt="A"),),
        article=Article(
            published_time=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            modified_time="2026-02-03",
            authors=("Ann", "Bob"),
            section="Finance",
            tags=("money", "apps"),
        ),
    ),
    twitter=Twitter(
        card="summary",
        site="@acme",
        creator="@ann",
        title="Tw",
        description="TwD",
        images=("/t.png",),
    ),
    verification=Verification(google=("g1", "g2"), yandex=("y",), bing=("b",)),
    other=(("keywords", "a, b"), ("theme-color", "#fff")),
    jsonld=({"@type": "WebPage"}, {"@type": "Article"}),
)
FULL_LINES = (
    "<title>Wallet</title>",
    '<meta name="description" content="Money">',
    '<meta name="robots" content="index, follow">',
    '<meta name="googlebot" content="noimageindex">',
    f'<link rel="canonical" href="{BASE}/wallet/">',
    f'<link rel="alternate" hreflang="en" href="{BASE}/wallet/">',
    f'<link rel="alternate" hreflang="de" href="{BASE}/de/wallet/">',
    '<meta name="google-site-verification" content="g1">',
    '<meta name="google-site-verification" content="g2">',
    '<meta name="yandex-verification" content="y">',
    '<meta name="msvalidate.01" content="b">',
    '<meta name="keywords" content="a, b">',
    '<meta name="theme-color" content="#fff">',
    '<meta property="og:title" content="Wallet">',
    '<meta property="og:description" content="Money">',
    f'<meta property="og:url" content="{BASE}/wallet/">',
    '<meta property="og:type" content="article">',
    '<meta property="og:site_name" content="Acme">',
    '<meta property="og:locale" content="en_GB">',
    f'<meta property="og:image" content="{BASE}/a.png">',
    '<meta property="og:image:width" content="1">',
    '<meta property="og:image:height" content="2">',
    '<meta property="og:image:alt" content="A">',
    '<meta property="article:published_time" content="2026-01-02T03:04:05+00:00">',
    '<meta property="article:modified_time" content="2026-02-03">',
    '<meta property="article:author" content="Ann">',
    '<meta property="article:author" content="Bob">',
    '<meta property="article:section" content="Finance">',
    '<meta property="article:tag" content="money">',
    '<meta property="article:tag" content="apps">',
    '<meta name="twitter:card" content="summary">',
    '<meta name="twitter:site" content="@acme">',
    '<meta name="twitter:creator" content="@ann">',
    '<meta name="twitter:title" content="Tw">',
    '<meta name="twitter:description" content="TwD">',
    f'<meta name="twitter:image" content="{BASE}/t.png">',
    '<script type="application/ld+json">{"@type": "WebPage"}</script>',
    '<script type="application/ld+json">{"@type": "Article"}</script>',
)


def _request(path: str = "/wallet/") -> HttpRequest:
    return RequestFactory().get(path)


def _lines(meta: Metadata, request: HttpRequest | None = None) -> list[str]:
    return render_metadata(meta, request=request).split("\n")


def _count_translations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    original = backends_module.translate_url

    def counting(url: str, code: str) -> str:
        calls.append((url, code))
        return original(url, code)

    monkeypatch.setattr(backends_module, "translate_url", counting)
    return calls


@pytest.fixture(autouse=True)
def _fresh_translation_memo() -> Iterator[None]:
    forget_translated_urls()
    yield
    forget_translated_urls()


class TestRendererContract:
    """The ABC binds `render`, and the module renderer is the default."""

    def test_the_abc_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            MetadataRenderer()  # type: ignore[abstract]

    def test_a_subclass_answers_render(self) -> None:
        class Plain(MetadataRenderer):
            @override
            def render(self, meta: Metadata, *, request: HttpRequest | None) -> str:
                return SafeString(str(meta.title))

        assert Plain().render(Metadata(title="T"), request=None) == "T"

    def test_render_metadata_goes_through_the_default_renderer(self) -> None:
        assert isinstance(default_renderer, HtmlMetadataRenderer)
        assert render_metadata(EMPTY_METADATA, request=None) == ""

    def test_empty_metadata_renders_a_safe_empty_string(self) -> None:
        rendered = HtmlMetadataRenderer().render(EMPTY_METADATA, request=None)
        assert isinstance(rendered, SafeString)
        assert rendered == ""


class TestEscaping:
    """Every value is escaped, the lazy ones under the language of the render."""

    def test_the_description_is_escaped(self) -> None:
        meta = Metadata(description='"><script>alert(1)</script>')
        html = render_metadata(meta, request=None)
        assert "<script>" not in html
        assert html == (
            '<meta name="description" '
            'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">'
        )

    def test_the_title_is_escaped(self) -> None:
        assert _lines(Metadata(title="<b>")) == ["<title>&lt;b&gt;</title>"]

    def test_the_output_is_safe(self) -> None:
        assert isinstance(
            render_metadata(Metadata(title="T"), request=None), SafeString
        )

    def test_a_lazy_value_follows_the_active_language(self) -> None:
        meta = Metadata(title=lazy(translation.get_language, str)())
        with translation.override("de"):
            assert _lines(meta) == ["<title>de</title>"]
        with translation.override("en"):
            assert _lines(meta) == ["<title>en</title>"]

    def test_jsonld_escapes_the_script_closers(self) -> None:
        meta = Metadata(jsonld=({"name": "</script><!-- & x"},))
        html = render_metadata(meta, request=None)
        assert "</script><!--" not in html
        assert html == (
            '<script type="application/ld+json">'
            '{"name": "\\u003C/script\\u003E\\u003C!-- \\u0026 x"}</script>'
        )

    def test_jsonld_serialises_through_the_django_encoder(self) -> None:
        meta = Metadata(jsonld=({"at": datetime(2026, 1, 2, tzinfo=UTC)},))
        assert _lines(meta) == [
            (
                '<script type="application/ld+json">'
                '{"at": "2026-01-02T00:00:00Z"}</script>'
            )
        ]


class TestAbsoluteUrl:
    """`absolute_url` prefers the base, then the request host, and rejects schemes."""

    @pytest.mark.parametrize("case", ABSOLUTE_URL_CASES, ids=lambda c: c.id)
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_resolution(self, case: AbsoluteUrlCase) -> None:
        request = None if case.path is None else _request(case.path)
        if case.error is not None:
            with pytest.raises(case.error):
                absolute_url(case.url, base=case.base, request=request)
            return
        assert absolute_url(case.url, base=case.base, request=request) == case.expected

    def test_a_foreign_scheme_names_the_url(self) -> None:
        with pytest.raises(PageMetadataShapeError) as info:
            absolute_url("javascript:alert(1)", base=BASE, request=_request())
        assert info.value.source == "metadata"
        assert info.value.detail == (
            "carries the URL 'javascript:alert(1)' with a scheme outside http and https"
        )

    def test_an_unresolvable_url_names_itself(self) -> None:
        with pytest.raises(PageMetadataURLError) as info:
            absolute_url("/a/", base=None, request=None)
        assert info.value.url == "/a/"

    def test_a_relative_form_resolves_against_the_request_path(self) -> None:
        request = build_mock_http_request(path="/p/q/")
        assert absolute_url("./a", base=BASE, request=request) == f"{BASE}/p/q/a"

    @pytest.mark.parametrize(
        ("field", "meta"),
        [
            ("canonical", Metadata(canonical="ftp://x/")),
            ("hreflang", Metadata(alternates=Alternates(languages={"en": "ftp://x/"}))),
            ("og:url", Metadata(og=OpenGraph(url="ftp://x/"))),
            ("og:image", Metadata(og=OpenGraph(images=(OpenGraphImage("ftp://x"),)))),
            ("twitter:image", Metadata(twitter=Twitter(images=("ftp://x",)))),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_every_url_field_goes_through_the_scheme_allowlist(
        self, field: str, meta: Metadata
    ) -> None:
        with pytest.raises(PageMetadataShapeError):
            render_metadata(meta, request=_request())

    def test_og_url_given_absolute_is_kept(self) -> None:
        meta = Metadata(og=OpenGraph(url="https://x.example/a/"))
        assert '<meta property="og:url" content="https://x.example/a/">' in _lines(meta)


class TestCanonical:
    """`True` is the self URL under the query allowlist, a string is untouched."""

    @pytest.mark.parametrize("canonical", [None, False], ids=["none", "false"])
    def test_off_renders_no_link(self, *, canonical: bool | None) -> None:
        assert _lines(Metadata(canonical=canonical, base=BASE)) == [""]

    def test_true_without_a_request_raises(self) -> None:
        with pytest.raises(PageMetadataURLError) as info:
            render_metadata(Metadata(canonical=True, base=BASE), request=None)
        assert info.value.url == "canonical"

    def test_true_is_the_request_path_without_a_query(self) -> None:
        meta = Metadata(canonical=True, base=BASE)
        request = _request("/wallet/?utm=1&page=2")
        assert _lines(meta, request) == [
            f'<link rel="canonical" href="{BASE}/wallet/">'
        ]

    @override_settings(NEXT_FRAMEWORK=QUERY)
    def test_true_keeps_the_allowlisted_keys_in_allowlist_order(self) -> None:
        meta = Metadata(canonical=True, base=BASE)
        request = _request("/wallet/?utm=1&page=2&q=a%20b&q=c")
        assert _lines(meta, request) == [
            f'<link rel="canonical" href="{BASE}/wallet/?q=a+b&amp;q=c&amp;page=2">'
        ]

    @override_settings(NEXT_FRAMEWORK=QUERY)
    def test_true_drops_the_first_page(self) -> None:
        meta = Metadata(canonical=True, base=BASE)
        request = _request("/wallet/?page=1&q=x")
        assert _lines(meta, request) == [
            f'<link rel="canonical" href="{BASE}/wallet/?q=x">'
        ]

    def test_true_keeps_the_script_name(self) -> None:
        meta = Metadata(canonical=True, base=BASE)
        request = RequestFactory().get("/wallet/", SCRIPT_NAME="/app")
        assert _lines(meta, request) == [
            f'<link rel="canonical" href="{BASE}/app/wallet/">'
        ]

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_true_falls_back_to_the_request_host(self) -> None:
        assert _lines(Metadata(canonical=True), _request()) == [
            '<link rel="canonical" href="http://testserver/wallet/">'
        ]

    def test_a_string_is_left_as_declared(self) -> None:
        meta = Metadata(canonical="/other", base=f"{BASE}/")
        assert _lines(meta, _request("/x/?q=1")) == [
            f'<link rel="canonical" href="{BASE}/other">'
        ]


class TestRobots:
    """The directives fold like Next.js, and `NOINDEX` overrides everything."""

    @pytest.mark.parametrize("case", ROBOTS_CASES, ids=lambda c: c.id)
    def test_folding(self, case: RobotsCase) -> None:
        expected = [f'<meta name="robots" content="{case.expected}">']
        assert _lines(Metadata(robots=case.robots)) == (
            expected if case.expected else [""]
        )

    def test_googlebot_is_a_separate_meta(self) -> None:
        robots = Robots(index=True, googlebot=Robots(index=False, nosnippet=True))
        assert _lines(Metadata(robots=robots)) == [
            '<meta name="robots" content="index">',
            '<meta name="googlebot" content="noindex, nosnippet">',
        ]

    def test_googlebot_as_a_string(self) -> None:
        assert _lines(Metadata(robots=Robots(googlebot="none"))) == [
            '<meta name="googlebot" content="none">'
        ]

    def test_an_empty_googlebot_fold_emits_nothing(self) -> None:
        assert _lines(Metadata(robots=Robots(index=True, googlebot=Robots()))) == [
            '<meta name="robots" content="index">'
        ]

    @override_settings(NEXT_FRAMEWORK=NOINDEX)
    def test_noindex_overrides_the_chain_and_drops_googlebot(self) -> None:
        robots = Robots(index=True, follow=True, googlebot="all")
        assert _lines(Metadata(robots=robots)) == [
            '<meta name="robots" content="noindex, nofollow">'
        ]

    @override_settings(NEXT_FRAMEWORK=NOINDEX)
    def test_noindex_applies_without_any_robots(self) -> None:
        assert _lines(EMPTY_METADATA) == [
            '<meta name="robots" content="noindex, nofollow">'
        ]


class TestHreflang:
    """A mapping is emitted as given, `True` walks the localised URLconf."""

    def test_a_mapping_emits_one_link_per_entry(self) -> None:
        alternates = Alternates(languages={"en": "/a/", "de": "https://de.example/a/"})
        assert _lines(Metadata(base=BASE, alternates=alternates)) == [
            f'<link rel="alternate" hreflang="en" href="{BASE}/a/">',
            '<link rel="alternate" hreflang="de" href="https://de.example/a/">',
        ]

    def test_a_mapping_puts_the_given_x_default_last(self) -> None:
        alternates = Alternates(languages={"de": "/de/"}, x_default="/")
        assert _lines(Metadata(base=BASE, alternates=alternates)) == [
            f'<link rel="alternate" hreflang="de" href="{BASE}/de/">',
            f'<link rel="alternate" hreflang="x-default" href="{BASE}/">',
        ]

    @pytest.mark.parametrize("languages", [None, False], ids=["none", "false"])
    def test_off_emits_nothing(self, *, languages: bool | None) -> None:
        alternates = Alternates(languages=languages, x_default="/")
        assert _lines(Metadata(base=BASE, alternates=alternates)) == [""]

    def test_true_without_i18n_patterns_emits_nothing(self) -> None:
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        assert _lines(meta, _request("/headed/")) == [""]

    @override_settings(**I18N)
    def test_true_emits_one_link_per_language_and_an_unprefixed_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _count_translations(monkeypatch)
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        expected = [
            f'<link rel="alternate" hreflang="en" href="{BASE}/headed/">',
            f'<link rel="alternate" hreflang="de" href="{BASE}/de/headed/">',
            f'<link rel="alternate" hreflang="x-default" href="{BASE}/headed/">',
        ]
        assert _lines(meta, _request("/headed/")) == expected
        assert calls == [("/headed/", "en"), ("/headed/", "de")]
        assert _lines(meta, _request("/headed/")) == expected
        assert len(calls) == 2

    @override_settings(**I18N)
    def test_true_takes_the_given_x_default(self) -> None:
        alternates = Alternates(languages=True, x_default="/all/")
        meta = Metadata(base=BASE, alternates=alternates)
        assert _lines(meta, _request("/headed/"))[-1] == (
            f'<link rel="alternate" hreflang="x-default" href="{BASE}/all/">'
        )

    @override_settings(**I18N)
    def test_true_translates_the_canonical_string(self) -> None:
        meta = Metadata(
            base=BASE, canonical="/headed/", alternates=Alternates(languages=True)
        )
        assert _lines(meta)[1:] == [
            f'<link rel="alternate" hreflang="en" href="{BASE}/headed/">',
            f'<link rel="alternate" hreflang="de" href="{BASE}/de/headed/">',
            f'<link rel="alternate" hreflang="x-default" href="{BASE}/headed/">',
        ]

    @override_settings(**I18N)
    def test_true_without_a_request_or_canonical_raises(self) -> None:
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        with pytest.raises(PageMetadataURLError) as info:
            render_metadata(meta, request=None)
        assert info.value.url == "alternates"

    @override_settings(**I18N)
    def test_true_keeps_the_canonical_query(self) -> None:
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        with override_next_settings(**QUERY):
            lines = _lines(meta, _request("/headed/?page=2&x=1"))
        assert lines[1] == (
            f'<link rel="alternate" hreflang="de" href="{BASE}/de/headed/?page=2">'
        )

    @override_settings(LANGUAGES=I18N["LANGUAGES"], LANGUAGE_CODE="en")
    def test_the_thread_urlconf_is_honoured(self) -> None:
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        set_urlconf("tests.support.i18n_urls")
        try:
            lines = _lines(meta, _request("/headed/"))
        finally:
            set_urlconf(None)
        assert lines[1] == (
            f'<link rel="alternate" hreflang="de" href="{BASE}/de/headed/">'
        )


class TestTranslationMemo:
    """The memo drops on a settings reload and on a URLconf or language change."""

    @override_settings(**I18N)
    def test_settings_reloaded_clears_the_memo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _count_translations(monkeypatch)
        meta = Metadata(base=BASE, alternates=Alternates(languages=True))
        _lines(meta, _request("/headed/"))
        settings_reloaded.send(sender=None)
        _lines(meta, _request("/headed/"))
        assert len(calls) == 4

    @pytest.mark.parametrize(
        ("setting", "cleared"),
        [("ROOT_URLCONF", True), ("LANGUAGES", True), ("DEBUG", False)],
        ids=["urlconf", "languages", "other"],
    )
    def test_setting_changed_clears_only_for_urlconf_and_languages(
        self, setting: str, *, cleared: bool
    ) -> None:
        backends_module._translated[(None, "/x/", "en")] = "/x/"
        setting_changed.send(sender=None, setting=setting, value=None, enter=True)
        assert ((None, "/x/", "en") in backends_module._translated) is not cleared


class TestOpenGraphDerivation:
    """The og block borrows from the page only when it exists, twitter never."""

    def test_no_og_block_derives_nothing(self) -> None:
        meta = Metadata(title="T", description="D", site_name="S", canonical="/x/")
        assert not any("og:" in line for line in _lines(meta, _request()))

    def test_an_empty_og_block_borrows_title_description_url_and_site_name(
        self,
    ) -> None:
        meta = Metadata(
            title="T",
            description="D",
            site_name="S",
            base=BASE,
            canonical=True,
            og=OpenGraph(),
        )
        with translation.override(None):
            og_lines = [line for line in _lines(meta, _request()) if "og:" in line]
        assert og_lines == [
            '<meta property="og:title" content="T">',
            '<meta property="og:description" content="D">',
            f'<meta property="og:url" content="{BASE}/wallet/">',
            '<meta property="og:site_name" content="S">',
        ]

    def test_explicit_og_fields_win(self) -> None:
        meta = Metadata(
            title="T",
            description="D",
            og=OpenGraph(title="Social T", description="Social D"),
        )
        lines = _lines(meta)
        assert '<meta property="og:title" content="Social T">' in lines
        assert '<meta property="og:description" content="Social D">' in lines

    def test_og_url_is_not_derived_without_a_canonical(self) -> None:
        assert not any("og:url" in line for line in _lines(Metadata(og=OpenGraph())))

    def test_og_locale_comes_from_the_active_language(self) -> None:
        with translation.override("en-us"):
            lines = _lines(Metadata(og=OpenGraph()))
        assert lines == ['<meta property="og:locale" content="en_US">']

    def test_an_explicit_og_locale_wins(self) -> None:
        with translation.override("de"):
            lines = _lines(Metadata(og=OpenGraph(locale="fr_FR")))
        assert lines == ['<meta property="og:locale" content="fr_FR">']

    def test_no_active_language_gives_no_locale(self) -> None:
        with translation.override(None):
            assert _lines(Metadata(og=OpenGraph())) == [""]

    def test_an_image_without_a_url_keeps_its_dimensions(self) -> None:
        og = OpenGraph(images=(OpenGraphImage(width=3),))
        with translation.override(None):
            assert _lines(Metadata(og=og)) == [
                '<meta property="og:image:width" content="3">'
            ]

    def test_a_sparse_article_renders_only_what_it_carries(self) -> None:
        og = OpenGraph(article=Article(authors=("Ann",), tags=("x",)))
        with translation.override(None):
            assert _lines(Metadata(og=og)) == [
                '<meta property="article:author" content="Ann">',
                '<meta property="article:tag" content="x">',
            ]

    def test_twitter_copies_nothing_from_og(self) -> None:
        meta = Metadata(
            title="T",
            description="D",
            og=OpenGraph(images=(OpenGraphImage(url="https://x/a.png"),)),
            twitter=Twitter(card="summary"),
        )
        twitter_lines = [line for line in _lines(meta) if "twitter:" in line]
        assert twitter_lines == ['<meta name="twitter:card" content="summary">']


class TestOutputOrder:
    """A fully populated value renders every line in the documented order."""

    def test_full_metadata(self) -> None:
        assert _lines(FULL, _request()) == list(FULL_LINES)

    def test_lines_are_joined_by_newlines_only(self) -> None:
        html = render_metadata(FULL, request=_request())
        assert re.fullmatch(r"(<[^\n]+>\n)*<[^\n]+>", html)
