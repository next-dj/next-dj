import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django.urls import ResolverMatch, reverse_lazy
from django.utils import translation
from django.utils.functional import lazy

from next.pages import ld
from next.pages.errors import PageMetadataRequestError, PageMetadataShapeError
from next.pages.metadata import (
    Alternates,
    Article,
    Book,
    Breadcrumb,
    Crumb,
    Feed,
    HtmlMetadataRenderer,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Robots,
    ThemeColor,
    Twitter,
    TwitterImage,
    TwitterPlayer,
    Verification,
    Viewport,
    absolute_url,
    resolve_metadata,
)
from next.pages.metadata.normalize import normalize_metadata
from next.pages.metadata.resolve import (
    SITE_NOINDEX,
    _failures,
    iso_time,
    og_locale,
    publish_metadata,
    published_metadata,
    robots_content,
    self_path,
    viewport_content,
)
from next.seo.origin import request_origin
from next.site import SiteOriginError
from next.testing import override_next_settings
from next.urls.parser import default_url_parser
from tests.support import (
    ABSOLUTE_URL_CASES,
    BASE,
    CLOSED_SITE,
    FEED_URLCONF,
    I18N,
    I18N_PREFIXED_URLCONF,
    I18N_ROUTED,
    NAMESPACED_URLCONF,
    ROBOTS_CASES,
    URL_SCHEME_CASES,
    WITH_BASE,
    AbsoluteUrlCase,
    RobotsCase,
    UrlSchemeCase,
    build_mock_http_request,
    handler_declared_here,
)


QUERY = {"METADATA": {"CANONICAL_QUERY": ("q", "page")}}


def _request(path: str = "/wallet/", **extra: str) -> HttpRequest:
    return RequestFactory().get(path, **extra)


@pytest.fixture()
def with_base() -> Iterator[None]:
    with override_next_settings(**WITH_BASE):
        yield


class TestAbsoluteUrl:
    """`absolute_url` prefers the site URL, then the request host."""

    @pytest.mark.parametrize(
        "case", ABSOLUTE_URL_CASES, ids=[case.id for case in ABSOLUTE_URL_CASES]
    )
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_a_url_resolves_on_the_site_or_the_request(
        self, case: AbsoluteUrlCase
    ) -> None:
        request = None if case.path is None else _request(case.path)
        with override_settings(NEXT_FRAMEWORK={"SITE": {"URL": case.site}}):
            if case.error is not None:
                with pytest.raises(case.error):
                    absolute_url(case.url, request=request)
                return
            assert absolute_url(case.url, request=request) == case.expected

    def test_an_unresolvable_url_names_itself(self) -> None:
        with pytest.raises(SiteOriginError) as info:
            absolute_url("/a/")
        assert info.value.url == "/a/"

    @pytest.mark.parametrize(
        ("site", "scheme", "expected"),
        [
            (BASE, "http", "https://cdn.example/x.png"),
            ("http://acme.example", "https", "http://cdn.example/x.png"),
            (None, "https", "https://cdn.example/x.png"),
            (None, "http", "http://cdn.example/x.png"),
        ],
        ids=["site_https", "site_http", "request_https", "request_http"],
    )
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_a_protocol_relative_url_keeps_its_own_host(
        self, site: str | None, scheme: str, expected: str
    ) -> None:
        request = RequestFactory().get("/p/", secure=scheme == "https")
        with override_settings(NEXT_FRAMEWORK={"SITE": {"URL": site}}):
            assert absolute_url("//cdn.example/x.png", request=request) == expected

    @override_next_settings(**WITH_BASE)
    def test_a_protocol_relative_url_takes_the_site_scheme_without_a_request(
        self,
    ) -> None:
        assert absolute_url("//cdn.example/x.png") == "https://cdn.example/x.png"

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://a.example/\u00fc x", "https://a.example/%C3%BC%20x"),
            ("https://a.example/%C3%BC", "https://a.example/%C3%BC"),
            ("//cdn.example/\u00fc", "https://cdn.example/%C3%BC"),
        ],
        ids=["absolute", "already_encoded", "protocol_relative"],
    )
    @override_next_settings(**WITH_BASE)
    def test_a_url_with_a_host_is_encoded_like_a_relative_one(
        self, url: str, expected: str
    ) -> None:
        assert absolute_url(url) == expected

    def test_a_protocol_relative_url_needs_a_site_or_a_request(self) -> None:
        with pytest.raises(SiteOriginError) as info:
            absolute_url("//cdn.example/x.png")
        assert info.value.url == "//cdn.example/x.png"

    def test_a_callable_site_url_answers_per_request(self) -> None:
        def tenant(request: HttpRequest | None) -> str:
            return "https://tenant.example" if request is not None else ""

        with override_settings(NEXT_FRAMEWORK={"SITE": {"URL": tenant}}):
            assert absolute_url("/a/", request=_request()) == (
                "https://tenant.example/a/"
            )

    @override_settings(SITE_ID=1)
    def test_the_sites_row_names_the_host_the_sitemap_lists_on(
        self, site_model
    ) -> None:
        site_model.objects.create(id=1, domain="sites.example", name="Sites")
        request = _request()
        assert absolute_url("/a/", request=request) == "http://sites.example/a/"
        assert request_origin(request).url("/a/") == "http://sites.example/a/"


class TestCanonical:
    """`True` is the escaped self URL under the query allowlist."""

    def test_none_resolves_no_canonical(self) -> None:
        assert resolve_metadata(Metadata(), request=None).canonical is None

    def test_true_without_a_request_names_the_key(self) -> None:
        with pytest.raises(PageMetadataRequestError) as info:
            resolve_metadata(Metadata(canonical=True), request=None)
        assert info.value.key == "canonical"

    @override_next_settings(**WITH_BASE)
    def test_true_is_the_request_path_without_a_query(self) -> None:
        resolved = resolve_metadata(
            Metadata(canonical=True), request=_request("/wallet/?utm=1&page=2")
        )
        assert resolved.canonical == f"{BASE}/wallet/"

    @pytest.mark.parametrize(
        ("path", "href"),
        [
            ("/wallet/?utm=1&page=2&q=a%20b&q=c", "/wallet/?q=a+b&q=c&page=2"),
            ("/wallet/?page=1&q=x", "/wallet/?q=x"),
        ],
        ids=["allowlist_order", "first_page_dropped"],
    )
    def test_true_keeps_the_allowlisted_query(self, path: str, href: str) -> None:
        with override_next_settings(**WITH_BASE, **QUERY):
            resolved = resolve_metadata(
                Metadata(canonical=True), request=_request(path)
            )
        assert resolved.canonical == f"{BASE}{href}"

    @override_next_settings(**WITH_BASE)
    def test_true_keeps_the_script_name(self) -> None:
        request = RequestFactory().get("/wallet/", SCRIPT_NAME="/app")
        resolved = resolve_metadata(Metadata(canonical=True), request=request)
        assert resolved.canonical == f"{BASE}/app/wallet/"

    @pytest.mark.parametrize("site", [BASE, None], ids=["site_url", "request_host"])
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_a_decoded_path_is_escaped_the_same_on_both_branches(
        self, site: str | None
    ) -> None:
        request = _request("/a%3Fb/100%25/%D0%BC%D0%B8%D1%80/")
        with override_settings(NEXT_FRAMEWORK={"SITE": {"URL": site}}):
            resolved = resolve_metadata(Metadata(canonical=True), request=request)
        origin = site or "http://testserver"
        assert resolved.canonical == f"{origin}/a%3Fb/100%25/%D0%BC%D0%B8%D1%80/"

    @override_settings(NEXT_FRAMEWORK={"SITE": {"URL": f"{BASE}/"}})
    def test_a_string_is_left_as_declared(self) -> None:
        resolved = resolve_metadata(
            Metadata(canonical="/other"), request=_request("/x/?q=1")
        )
        assert resolved.canonical == f"{BASE}/other"

    @override_next_settings(**WITH_BASE)
    def test_the_self_path_is_escaped(self) -> None:
        assert self_path(_request("/%D0%BC%D0%B8%D1%80/")) == "/%D0%BC%D0%B8%D1%80/"


class TestRobots:
    """The robots fold to one content, and a closed site overrides everything."""

    @pytest.mark.parametrize(
        "case", ROBOTS_CASES, ids=[case.id for case in ROBOTS_CASES]
    )
    def test_the_directives_fold_into_one_content(self, case: RobotsCase) -> None:
        assert robots_content(case.robots) == case.expected
        resolved = resolve_metadata(Metadata(robots=case.robots), request=None)
        assert resolved.robots == (case.expected or None)

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [
            (
                Robots(index=True, googlebot=Robots(index=False, nosnippet=True)),
                ("index", "noindex, nosnippet"),
            ),
            (Robots(googlebot="none"), (None, "none")),
            (Robots(index=True, googlebot=Robots()), ("index", None)),
        ],
        ids=["folded", "text", "empty_fold"],
    )
    def test_googlebot_resolves_on_its_own(
        self, robots: Robots, expected: tuple[str | None, str | None]
    ) -> None:
        resolved = resolve_metadata(Metadata(robots=robots), request=None)
        assert (resolved.robots, resolved.googlebot) == expected

    @pytest.mark.parametrize(
        "meta",
        [Metadata(robots=Robots(index=True, follow=True, googlebot="all")), Metadata()],
        ids=["declared_robots", "no_robots"],
    )
    @override_settings(NEXT_FRAMEWORK=CLOSED_SITE)
    def test_a_closed_site_overrides_the_chain_and_drops_googlebot(
        self, meta: Metadata
    ) -> None:
        resolved = resolve_metadata(meta, request=None)
        assert (resolved.robots, resolved.googlebot) == (SITE_NOINDEX, None)
        assert resolved.noindex is True

    @override_settings(DEBUG=True)
    def test_the_auto_rule_closes_the_site_under_debug(self) -> None:
        assert resolve_metadata(Metadata(), request=None).robots == SITE_NOINDEX

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [("none", True), ("NOINDEX, follow", True), ("index", False)],
        ids=["none", "upper_case", "index"],
    )
    def test_noindex_reads_the_tokens(self, robots: str, *, expected: bool) -> None:
        assert resolve_metadata(Metadata(robots=robots), request=None).noindex is (
            expected
        )


class TestAlternates:
    """A mapping resolves as given, and x-default comes exactly once, last."""

    @override_next_settings(**WITH_BASE)
    def test_a_mapping_resolves_one_pair_per_entry(self) -> None:
        alternates = Alternates(
            languages=(("en", "/a/"), ("de", "https://de.example/a/"))
        )
        resolved = resolve_metadata(Metadata(alternates=alternates), request=None)
        assert resolved.alternates == (
            ("en", f"{BASE}/a/"),
            ("de", "https://de.example/a/"),
        )

    @override_next_settings(**WITH_BASE)
    def test_the_given_x_default_goes_last(self) -> None:
        alternates = Alternates(languages=(("de", "/de/"),), x_default="/")
        resolved = resolve_metadata(Metadata(alternates=alternates), request=None)
        assert resolved.alternates == (("de", f"{BASE}/de/"), ("x-default", f"{BASE}/"))

    @override_next_settings(**WITH_BASE)
    def test_an_x_default_language_and_x_default_resolve_to_one(self) -> None:
        alternates = Alternates(
            languages=(("x-default", "/lang/"), ("de", "/de/")), x_default="/"
        )
        resolved = resolve_metadata(Metadata(alternates=alternates), request=None)
        assert resolved.alternates == (("de", f"{BASE}/de/"), ("x-default", f"{BASE}/"))

    @override_next_settings(**WITH_BASE)
    def test_an_x_default_language_alone_is_the_fallback(self) -> None:
        alternates = Alternates(languages=(("x-default", "/lang/"), ("de", "/de/")))
        resolved = resolve_metadata(Metadata(alternates=alternates), request=None)
        assert resolved.alternates[-1] == ("x-default", f"{BASE}/lang/")

    @pytest.mark.parametrize(
        "languages", [None, False, ()], ids=["none", "false", "empty"]
    )
    @override_next_settings(**WITH_BASE)
    def test_nothing_to_list_resolves_none(
        self, *, languages: tuple[tuple[str, str], ...] | bool | None
    ) -> None:
        alternates = Alternates(languages=languages, x_default="/")
        assert (
            resolve_metadata(Metadata(alternates=alternates), request=None).alternates
            == ()
        )

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE)
    def test_true_lists_every_language_and_the_default_as_x_default(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        resolved = resolve_metadata(meta, request=_request("/headed/"))
        assert resolved.alternates == (
            ("en", f"{BASE}/headed/"),
            ("de", f"{BASE}/de/headed/"),
            ("x-default", f"{BASE}/headed/"),
        )

    @override_settings(**I18N, ROOT_URLCONF=I18N_PREFIXED_URLCONF)
    @override_next_settings(**WITH_BASE)
    def test_a_prefixed_default_language_keeps_its_prefix_everywhere(self) -> None:
        meta = Metadata(canonical=True, alternates=Alternates(languages=True))
        resolved = resolve_metadata(meta, request=_request("/en/headed/"))
        assert resolved.canonical == f"{BASE}/en/headed/"
        assert resolved.alternates == (
            ("en", f"{BASE}/en/headed/"),
            ("de", f"{BASE}/de/headed/"),
            ("x-default", f"{BASE}/en/headed/"),
        )

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE)
    def test_true_translates_the_canonical_string(self) -> None:
        meta = Metadata(canonical="/headed/", alternates=Alternates(languages=True))
        assert resolve_metadata(meta, request=None).alternates[1] == (
            "de",
            f"{BASE}/de/headed/",
        )

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE)
    def test_true_without_a_request_or_canonical_names_the_key(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        with pytest.raises(PageMetadataRequestError) as info:
            resolve_metadata(meta, request=None)
        assert info.value.key == "alternates"

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE)
    def test_true_takes_the_given_x_default(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True, x_default="/all/"))
        resolved = resolve_metadata(meta, request=_request("/headed/"))
        assert resolved.alternates[-1] == ("x-default", f"{BASE}/all/")

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE, **QUERY)
    def test_true_keeps_the_canonical_query(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        resolved = resolve_metadata(meta, request=_request("/headed/?page=2&x=1"))
        assert resolved.alternates[1] == ("de", f"{BASE}/de/headed/?page=2")

    @override_settings(**I18N_ROUTED)
    @override_next_settings(**WITH_BASE)
    def test_an_unrouted_path_lists_no_alternates(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        assert resolve_metadata(meta, request=_request("/nowhere/")).alternates == ()

    @override_next_settings(**WITH_BASE)
    def test_true_without_i18n_patterns_lists_none(self) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        assert resolve_metadata(meta, request=_request("/headed/")).alternates == ()


class TestOpenGraph:
    """A declared og block falls back to the page values, a twitter block never does."""

    def test_no_og_block_resolves_none(self) -> None:
        meta = Metadata(title="T", description="D", site_name="S", canonical="/x/")
        with override_next_settings(**WITH_BASE):
            assert resolve_metadata(meta, request=None).og is None

    @override_next_settings(**WITH_BASE)
    def test_an_empty_og_block_borrows_title_description_url_and_site_name(
        self,
    ) -> None:
        meta = Metadata(
            title="T", description="D", site_name="S", canonical=True, og=OpenGraph()
        )
        with translation.override(None):
            og = resolve_metadata(meta, request=_request()).og
        assert og == OpenGraph(
            title="T", description="D", url=f"{BASE}/wallet/", site_name="S"
        )

    def test_explicit_og_fields_win(self) -> None:
        meta = Metadata(
            title="T",
            description="D",
            og=OpenGraph(title="Share title", description="Share text"),
        )
        og = resolve_metadata(meta, request=None).og
        assert og is not None
        assert (og.title, og.description) == ("Share title", "Share text")

    @override_next_settings(**WITH_BASE)
    def test_urls_are_made_absolute(self) -> None:
        og = OpenGraph(
            url="/u/",
            images=(
                OpenGraphImage(url="/a.png", width=1),
                OpenGraphImage(url="https://cdn.example/b.png", width=3),
            ),
        )
        resolved = resolve_metadata(Metadata(og=og), request=None).og
        assert resolved is not None
        assert resolved.url == f"{BASE}/u/"
        assert resolved.images == (
            OpenGraphImage(url=f"{BASE}/a.png", width=1),
            OpenGraphImage(url="https://cdn.example/b.png", width=3),
        )

    def test_the_locale_comes_from_the_active_language(self) -> None:
        with translation.override("en-us"):
            og = resolve_metadata(Metadata(og=OpenGraph()), request=None).og
        assert og is not None
        assert og.locale == "en_US"

    def test_an_explicit_locale_wins(self) -> None:
        with translation.override("de"):
            og = resolve_metadata(
                Metadata(og=OpenGraph(locale="fr_FR")), request=None
            ).og
        assert og is not None
        assert og.locale == "fr_FR"

    def test_no_active_language_gives_no_locale(self) -> None:
        with translation.override(None):
            og = resolve_metadata(Metadata(og=OpenGraph()), request=None).og
        assert og == OpenGraph()

    @override_settings(TIME_ZONE="Europe/Berlin", USE_TZ=True)
    def test_the_article_times_become_iso_text_with_a_zone(self) -> None:
        article = Article(
            published_time=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).replace(
                tzinfo=None
            ),
            modified_time=date(2026, 2, 3),
        )
        og = resolve_metadata(Metadata(og=OpenGraph(article=article)), request=None).og
        assert og is not None
        assert og.article == Article(
            published_time="2026-01-02T03:04:05+01:00", modified_time="2026-02-03"
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (datetime(2026, 1, 2, tzinfo=UTC), "2026-01-02T00:00:00+00:00"),
            (
                datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("America/New_York")),
                "2026-07-01T12:00:00-04:00",
            ),
            (date(2026, 1, 2), "2026-01-02"),
            ("2026-01-02", "2026-01-02"),
        ],
        ids=["aware_utc", "aware_zone", "date", "text"],
    )
    def test_an_aware_or_textual_time_is_kept(
        self, value: date | str, expected: str
    ) -> None:
        assert iso_time(value) == expected

    def test_an_article_without_times_is_left_alone(self) -> None:
        og = resolve_metadata(
            Metadata(og=OpenGraph(article=Article(section="S"))), request=None
        ).og
        assert og is not None
        assert og.article == Article(section="S")

    @override_next_settings(**WITH_BASE)
    def test_twitter_images_are_absolute_and_nothing_is_borrowed(self) -> None:
        meta = Metadata(
            title="T", twitter=Twitter(card="summary", images=(TwitterImage("/t.png"),))
        )
        assert resolve_metadata(meta, request=None).twitter == Twitter(
            card="summary", images=(TwitterImage(f"{BASE}/t.png"),)
        )


class TestTheRest:
    """Verification flattens to pairs, other and JSON-LD pass through."""

    def test_verification_flattens_in_engine_order(self) -> None:
        meta = Metadata(
            verification=Verification(google=("g1", "g2"), yandex=("y",), bing=("b",))
        )
        assert resolve_metadata(meta, request=None).verification == (
            ("google-site-verification", "g1"),
            ("google-site-verification", "g2"),
            ("yandex-verification", "y"),
            ("msvalidate.01", "b"),
        )

    def test_the_source_fold_is_kept(self) -> None:
        meta = Metadata(other=(("a", "1"),), jsonld=({"@type": "Thing"},))
        resolved = resolve_metadata(meta, request=None)
        assert resolved.source is meta
        assert resolved.other == (("a", "1"),)
        assert resolved.jsonld == ({"@type": "Thing"},)
        assert resolved.jsonld[0] is not meta.jsonld[0]
        assert resolved.verification == ()


class TestPublishing:
    """A render publishes its resolve on the request for the response layer."""

    def test_a_published_resolve_is_read_back(self) -> None:
        request = _request()
        assert published_metadata(request) is None
        resolved = resolve_metadata(Metadata(), request=request)
        publish_metadata(request, resolved)
        assert published_metadata(request) is resolved

    def test_no_request_and_a_foreign_value_read_as_nothing(self) -> None:
        assert published_metadata(None) is None
        assert published_metadata(build_mock_http_request()) is None


class TestOgLocale:
    """A language code maps to the `ll_CC` Open Graph locale, or to none."""

    @pytest.mark.parametrize(
        ("language", "expected"),
        [
            ("en", "en_US"),
            ("en-gb", "en_GB"),
            ("pt", "pt_PT"),
            ("pt-br", "pt_BR"),
            ("zh-hans", "zh_CN"),
            ("zh-Hant", "zh_TW"),
            ("ar", "ar_AR"),
            ("no", "nb_NO"),
            ("nb", "nb_NO"),
            ("sr-latn", "sr_RS"),
            ("he", "he_IL"),
            ("fa", "fa_IR"),
            ("es", "es_ES"),
            ("sw", "sw_KE"),
            ("ast", None),
            ("", None),
            (None, None),
        ],
    )
    def test_the_locale_of_a_language(
        self, language: str | None, expected: str | None
    ) -> None:
        assert og_locale(language) == expected

    @override_next_settings(**WITH_BASE)
    def test_the_active_language_names_the_locale(self) -> None:
        with translation.override("de"):
            og = resolve_metadata(Metadata(og=OpenGraph()), request=None).og
        assert og is not None
        assert og.locale == "de_DE"

    @override_next_settings(**WITH_BASE)
    def test_a_language_without_a_locale_renders_none(self) -> None:
        with translation.override("ast"):
            og = resolve_metadata(Metadata(og=OpenGraph()), request=None).og
        assert og is not None
        assert og.locale is None

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("ast", "A")])
    @override_next_settings(**WITH_BASE)
    def test_true_alternates_are_every_other_language(self) -> None:
        meta = Metadata(og=OpenGraph(locale_alternates=True))
        with translation.override("en"):
            og = resolve_metadata(meta, request=None).og
        assert og is not None
        assert og.locale_alternates == ("de_DE",)

    @pytest.mark.parametrize(
        ("alternates", "expected"),
        [(("fr_FR",), ("fr_FR",)), (False, ()), ((), ())],
        ids=["declared", "false", "unset"],
    )
    @override_next_settings(**WITH_BASE)
    def test_declared_alternates_stay_as_given(
        self, alternates: object, expected: tuple[str, ...]
    ) -> None:
        meta = Metadata(
            og=OpenGraph(locale="en_GB", locale_alternates=cast("bool", alternates))
        )
        og = resolve_metadata(meta, request=None).og
        assert og is not None
        assert (og.locale, og.locale_alternates) == ("en_GB", expected)


@pytest.mark.usefixtures("with_base")
class TestMedia:
    """Every media URL is absolute and the book date is ISO."""

    def test_the_og_media_urls_are_absolute(self) -> None:
        meta = Metadata(
            og=OpenGraph(
                images=(OpenGraphImage(url="/a.png", secure_url="/s.png"),),
                videos=(OpenGraphVideo("/v.mp4", secure_url="https://cdn.example/v"),),
                audio=(OpenGraphAudio("/a.mp3"),),
                book=Book(release_date=date(2026, 1, 2)),
            )
        )
        og = resolve_metadata(meta, request=None).og
        assert og is not None
        assert og.images == (
            OpenGraphImage(url=f"{BASE}/a.png", secure_url=f"{BASE}/s.png"),
        )
        assert og.videos == (
            OpenGraphVideo(f"{BASE}/v.mp4", secure_url="https://cdn.example/v"),
        )
        assert og.audio == (OpenGraphAudio(f"{BASE}/a.mp3"),)
        assert og.book == Book(release_date="2026-01-02")

    def test_a_book_without_a_date_is_kept(self) -> None:
        og = resolve_metadata(
            Metadata(og=OpenGraph(book=Book(isbn="978"))), request=None
        ).og
        assert og is not None
        assert og.book == Book(isbn="978")

    @pytest.mark.parametrize(
        ("stream", "expected"),
        [("/s.mp4", f"{BASE}/s.mp4"), (None, None)],
        ids=["stream", "no_stream"],
    )
    def test_the_twitter_player_is_absolute(
        self, stream: str | None, expected: str | None
    ) -> None:
        meta = Metadata(twitter=Twitter(player=TwitterPlayer("/p", 1, 2, stream)))
        twitter = resolve_metadata(meta, request=None).twitter
        assert twitter is not None
        assert twitter.player == TwitterPlayer(f"{BASE}/p", 1, 2, expected)


@pytest.mark.usefixtures("with_base")
class TestHeadSections:
    """The head sections resolve their URLs, content and verification pairs."""

    @pytest.mark.parametrize(
        ("viewport", "expected"),
        [
            ("width=device-width", "width=device-width"),
            (
                Viewport(
                    width="device-width",
                    initial_scale=1.0,
                    maximum_scale=2.5,
                    user_scalable=False,
                    viewport_fit="cover",
                    interactive_widget="resizes-content",
                ),
                (
                    "width=device-width, initial-scale=1, maximum-scale=2.5, "
                    "user-scalable=no, viewport-fit=cover, "
                    "interactive-widget=resizes-content"
                ),
            ),
            (Viewport(height=600, user_scalable=True), "height=600, user-scalable=yes"),
        ],
        ids=["string", "mapping", "numbers"],
    )
    def test_the_viewport_content(
        self, viewport: Viewport | str, expected: str
    ) -> None:
        assert viewport_content(viewport) == expected
        assert resolve_metadata(Metadata(viewport=viewport), request=None).viewport == (
            expected
        )

    def test_icons_links_and_feeds_are_absolute_but_origins_kept(self) -> None:
        meta = Metadata(
            icons=(Icon("icon", "/i.svg", sizes="any"),),
            links=(
                Link("preconnect", "https://fonts.gstatic.com"),
                Link("DNS-Prefetch", "//cdn.example"),
                Link("preload", "/font.woff2", (("as", "font"),)),
            ),
            alternates=Alternates(
                feeds=(
                    Feed("/feed.xml", "rss", "Blog"),
                    Feed("/atom.xml", "atom"),
                    Feed("/feed.json", "json"),
                    Feed("/custom", "application/x-custom"),
                )
            ),
        )
        resolved = resolve_metadata(meta, request=None)
        assert resolved.icons == (Icon("icon", f"{BASE}/i.svg", sizes="any"),)
        assert resolved.links == (
            Link("preconnect", "https://fonts.gstatic.com"),
            Link("DNS-Prefetch", "//cdn.example"),
            Link("preload", f"{BASE}/font.woff2", (("as", "font"),)),
        )
        assert resolved.feeds == (
            Feed(f"{BASE}/feed.xml", "application/rss+xml", "Blog"),
            Feed(f"{BASE}/atom.xml", "application/atom+xml"),
            Feed(f"{BASE}/feed.json", "application/feed+json"),
            Feed(f"{BASE}/custom", "application/x-custom"),
        )

    def test_the_new_engines_and_other_names_follow_the_engines(self) -> None:
        meta = Metadata(
            verification=Verification(
                google=("g",),
                pinterest=("p",),
                facebook=("f",),
                other=(("baidu-site-verification", "b"),),
            )
        )
        assert resolve_metadata(meta, request=None).verification == (
            ("google-site-verification", "g"),
            ("p:domain_verify", "p"),
            ("facebook-domain-verification", "f"),
            ("baidu-site-verification", "b"),
        )

    def test_the_plain_sections_pass_through(self) -> None:
        meta = Metadata(
            keywords=("a", "b"),
            theme_color=(ThemeColor("#fff"),),
            color_scheme="dark",
            properties=(("fb:app_id", "1"),),
        )
        resolved = resolve_metadata(meta, request=None)
        assert resolved.keywords == ("a", "b")
        assert resolved.theme_color == (ThemeColor("#fff"),)
        assert resolved.color_scheme == "dark"
        assert resolved.properties == (("fb:app_id", "1"),)


def _resolved_raw(raw: object) -> object:
    """Resolve a raw dict with the fold left out, so lazy and plain inputs compare."""
    meta = normalize_metadata(raw, source="page.py").metadata
    resolved = resolve_metadata(meta, request=_request())
    return dataclasses.replace(resolved, source=Metadata())


# A lone x-default renders no alternates, so its lazy URL is never forced.
_FORCED_URL_CASES = tuple(case for case in URL_SCHEME_CASES if case.id != "x_default")


@pytest.mark.usefixtures("with_base")
class TestLazyUrls:
    """A lazy URL is forced per resolve and resolves to what the plain string would."""

    @pytest.mark.parametrize(
        "case", URL_SCHEME_CASES, ids=[case.id for case in URL_SCHEME_CASES]
    )
    def test_a_lazy_url_resolves_like_the_plain_one(self, case: UrlSchemeCase) -> None:
        lazy_url = lazy(str, str)("/feed/")
        assert _resolved_raw(case.build(lazy_url)) == _resolved_raw(
            case.build("/feed/")
        )

    @override_settings(ROOT_URLCONF=FEED_URLCONF)
    def test_a_reverse_lazy_canonical_renders_the_reversed_url(self) -> None:
        raw = {"canonical": reverse_lazy("feed")}
        meta = normalize_metadata(raw, source="page.py").metadata
        resolved = resolve_metadata(meta, request=_request())
        assert resolved.canonical == f"{BASE}/feed/"

    def test_a_lazy_url_is_forced_on_every_resolve(self) -> None:
        calls: list[str] = []

        def build() -> str:
            calls.append("forced")
            return "/feed/"

        raw = {"og": {"url": lazy(build, str)()}}
        meta = normalize_metadata(raw, source="page.py").metadata
        for _ in range(2):
            og = resolve_metadata(meta, request=_request()).og
            assert og is not None
            assert og.url == f"{BASE}/feed/"
        assert calls == ["forced", "forced"]

    @pytest.mark.parametrize(
        "case", _FORCED_URL_CASES, ids=[case.id for case in _FORCED_URL_CASES]
    )
    def test_a_foreign_scheme_drops_its_tag_and_logs_once(
        self, case: UrlSchemeCase, caplog: pytest.LogCaptureFixture
    ) -> None:
        _failures.clear()
        raw = case.build(lazy(str, str)("javascript:alert(1)"))
        with caplog.at_level("ERROR", logger="next.pages.metadata.resolve"):
            first = HtmlMetadataRenderer().render(_resolved_raw(raw))
            second = HtmlMetadataRenderer().render(_resolved_raw(raw))
        assert "javascript" not in first
        assert first == second
        assert len(caplog.records) == 1
        assert "so its tag is left out" in caplog.text

    @override_settings(DEBUG=True)
    def test_a_foreign_scheme_fails_loudly_under_debug(self) -> None:
        raw = {"canonical": lazy(str, str)("javascript:alert(1)")}
        meta = normalize_metadata(raw, source="page.py").metadata
        with pytest.raises(PageMetadataShapeError) as caught:
            resolve_metadata(meta, request=_request())
        assert caught.value.source == "page.py"
        assert caught.value.detail == (
            "declares metadata key 'canonical' as the URL 'javascript:alert(1)', "
            "expected an http or https URL or a path"
        )
        assert "so its tag is left out" in caught.value.__notes__[0]


class TestManifest:
    """A declared manifest resolves to an absolute URL."""

    @override_next_settings(**WITH_BASE)
    def test_a_declared_manifest_is_absolute(self) -> None:
        resolved = resolve_metadata(Metadata(manifest="/app.json"), request=None)
        assert resolved.manifest == f"{BASE}/app.json"

    def test_no_declared_manifest_resolves_to_none(self) -> None:
        assert resolve_metadata(Metadata(), request=None).manifest is None


@pytest.mark.usefixtures("with_base")
class TestGraph:
    """The JSON-LD nodes resolve into one graph, `@id` absolute on the site."""

    def test_a_node_json_cannot_hold_is_left_out_and_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _failures.clear()
        bad = ld.Node(type="Offer", extra={"price": float("nan")})
        good = {"@type": "Thing", "tags": {"a"}}
        meta = Metadata(jsonld=(bad, good, ld.Node(id="#ok")))
        with caplog.at_level("ERROR", logger="next.pages.metadata.resolve"):
            first = resolve_metadata(meta, request=_request()).jsonld
            second = resolve_metadata(meta, request=_request()).jsonld
        assert first == second == ({"@type": "Thing", "@id": f"{BASE}/#ok"},)
        messages = [record.getMessage() for record in caplog.records]
        assert len(messages) == 2
        assert "The JSON-LD Offer node holds a value JSON cannot write" in messages[0]
        assert "The JSON-LD Thing node" in messages[1]

    @override_settings(DEBUG=True)
    def test_a_node_json_cannot_hold_fails_loudly_under_debug(self) -> None:
        meta = Metadata(jsonld=(ld.Node(extra={"price": float("inf")}),))
        with pytest.raises(ValueError, match="a finite number") as caught:
            resolve_metadata(meta, request=_request())
        assert "left out of the graph" in caught.value.__notes__[0]

    def test_a_typed_node_resolves_its_fragment_id_on_the_site_root(self) -> None:
        trail = ld.BreadcrumbList(
            id="#trail", items=(ld.ListItem(name="Home", position=1, item="/"),)
        )
        meta = Metadata(jsonld=(trail,))
        assert resolve_metadata(meta, request=_request("/deep/page/")).jsonld == (
            {
                "@type": "BreadcrumbList",
                "@id": f"{BASE}/#trail",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "name": "Home",
                        "position": 1,
                        "item": f"{BASE}/",
                    }
                ],
            },
        )

    def test_a_raw_mapping_drops_the_schema_context_and_resolves_ids(self) -> None:
        meta = Metadata(
            jsonld=(
                {
                    "@context": "https://schema.org",
                    "@id": "/p/#product",
                    "@type": "Product",
                    "brand": ld.Ref("#org"),
                    "isRelatedTo": [{"@id": "#other"}, "x"],
                    "offers": ({"price": "1"},),
                },
                {"@context": "http://schema.org/", "@type": "Thing"},
            )
        )
        assert resolve_metadata(meta, request=None).jsonld == (
            {
                "@id": f"{BASE}/p/#product",
                "@type": "Product",
                "brand": {"@id": f"{BASE}/#org"},
                "isRelatedTo": [{"@id": f"{BASE}/#other"}, "x"],
                "offers": [{"price": "1"}],
            },
            {"@type": "Thing"},
        )

    @pytest.mark.parametrize(
        "context",
        ["https://example.org/vocab", ["https://schema.org", {"x": "y"}]],
        ids=["foreign", "list"],
    )
    def test_a_foreign_context_is_kept_whole(self, context: object) -> None:
        node = {"@context": context, "@id": "#x", "@type": "Custom"}
        assert resolve_metadata(Metadata(jsonld=(node,)), request=None).jsonld == (
            node,
        )

    def test_a_typed_value_in_a_foreign_context_renders_its_id_as_written(self) -> None:
        node = {
            "@context": "https://example.org/vocab",
            "about": ld.Ref("#org"),
            "parts": [ld.Ref("#a")],
        }
        resolved = resolve_metadata(Metadata(jsonld=(node,)), request=None).jsonld
        assert resolved == (
            {
                "@context": "https://example.org/vocab",
                "about": {"@id": "#org"},
                "parts": [{"@id": "#a"}],
            },
        )
        assert json.dumps(resolved)


class TestBreadcrumbUrls:
    """A crumb reverses its trail against the match of the request, else keeps none."""

    CRUMBS = (Crumb("Home", ""), Crumb("Post", "blog/[slug]"))

    def _resolved(self, request: object) -> tuple[Breadcrumb, ...]:
        meta = Metadata(breadcrumbs=self.CRUMBS)
        return resolve_metadata(meta, request=cast("HttpRequest", request)).breadcrumbs

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    def test_a_request_without_a_match_links_nothing(self) -> None:
        assert self._resolved(build_mock_http_request()) == (
            Breadcrumb("Home", None),
            Breadcrumb("Post", None),
        )

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    def test_a_trail_the_match_has_no_kwargs_for_links_nothing(self) -> None:
        request = _request("/")
        request.resolver_match = ResolverMatch(
            handler_declared_here, (), {}, namespaces=["next"]
        )
        assert self._resolved(request)[1] == Breadcrumb("Post", None)

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    def test_a_trail_no_route_answers_links_nothing(self) -> None:
        request = _request("/blog/x/")
        request.resolver_match = ResolverMatch(
            handler_declared_here, (), {"slug": "x"}, namespaces=["next"]
        )
        assert self._resolved(request) == (
            Breadcrumb("Home", "/"),
            Breadcrumb("Post", None),
        )

    @override_settings(ROOT_URLCONF=NAMESPACED_URLCONF)
    @override_next_settings(**WITH_BASE)
    def test_an_unlinked_ancestor_keeps_the_trail_out_of_the_graph(self) -> None:
        request = _request("/")
        request.resolver_match = ResolverMatch(
            handler_declared_here, (), {}, namespaces=["next"]
        )
        linked = (Crumb("Gone", "nope"), Crumb("Home", ""))
        assert (
            resolve_metadata(Metadata(breadcrumbs=linked), request=request).jsonld == ()
        )
        (trail,) = resolve_metadata(
            Metadata(breadcrumbs=linked[::-1]), request=request
        ).jsonld
        assert trail["@id"] == f"{BASE}/#breadcrumb"

    def test_a_match_without_a_namespace_reverses_the_bare_name(self) -> None:
        request = _request("/")
        request.resolver_match = ResolverMatch(handler_declared_here, (), {})
        with patch(
            "next.pages.metadata.resolve.reverse", return_value="/"
        ) as reversed_:
            crumbs = resolve_metadata(
                Metadata(breadcrumbs=(Crumb("Home", ""),)), request=request
            ).breadcrumbs
        assert crumbs == (Breadcrumb("Home", "/", current=True),)
        reversed_.assert_called_once_with("page_", urlconf=None, kwargs=None)


class TestLazyBreadcrumbs:
    """A crumb reverses only once something reads it, under the state of its resolve."""

    @staticmethod
    def _matched(path: str = "/", **kwargs: str) -> HttpRequest:
        request = _request(path)
        request.resolver_match = ResolverMatch(
            handler_declared_here, (), kwargs, namespaces=["next"]
        )
        return request

    def test_a_crumb_reverses_on_the_first_read_alone(self) -> None:
        meta = Metadata(breadcrumbs=(Crumb("Home", ""),))
        with patch(
            "next.pages.metadata.resolve.reverse", return_value="/"
        ) as reversed_:
            resolved = resolve_metadata(meta, request=self._matched())
            assert reversed_.call_count == 0
            assert resolved.breadcrumbs == resolved.breadcrumbs
        assert resolved.breadcrumbs == (Breadcrumb("Home", "/", current=True),)
        assert reversed_.call_count == 1

    @override_next_settings(**WITH_BASE)
    def test_a_declared_breadcrumb_list_reverses_nothing(self) -> None:
        own = ld.BreadcrumbList(items=(ld.ListItem(name="X", position=1),))
        meta = Metadata(
            breadcrumbs=(Crumb("Home", ""), Crumb("Blog", "blog")), jsonld=(own,)
        )
        with patch("next.pages.metadata.resolve.reverse") as reversed_:
            resolved = resolve_metadata(meta, request=self._matched())
        assert len(resolved.jsonld) == 1
        reversed_.assert_not_called()

    def test_the_route_of_a_trail_is_parsed_once(self) -> None:
        name = f"t{uuid4().hex}"
        meta = Metadata(breadcrumbs=(Crumb("Once", f"once/[{name}]"),))
        with (
            patch.object(
                default_url_parser,
                "parse_url_pattern",
                wraps=default_url_parser.parse_url_pattern,
            ) as parsed,
            patch("next.pages.metadata.resolve.reverse", return_value="/once/"),
        ):
            for _ in range(2):
                request = self._matched(**{name: "x"})
                assert resolve_metadata(meta, request=request).breadcrumbs[0].url
        assert parsed.call_count == 1

    def test_a_crumb_reverses_under_the_urlconf_of_its_request(self) -> None:
        request = self._matched()
        request.urlconf = NAMESPACED_URLCONF
        resolved = resolve_metadata(
            Metadata(breadcrumbs=(Crumb("Home", ""),)), request=request
        )
        with patch(
            "next.pages.metadata.resolve.reverse", return_value="/"
        ) as reversed_:
            assert resolved.breadcrumbs[0].url == "/"
        reversed_.assert_called_once_with(
            "next:page_", urlconf=NAMESPACED_URLCONF, kwargs=None
        )
