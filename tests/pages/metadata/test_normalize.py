import dataclasses
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest
from django.utils.functional import Promise, lazy
from django.utils.translation import gettext_lazy

from next.pages import ld
from next.pages.errors import PageMetadataShapeError
from next.pages.metadata import (
    RESET,
    Alternates,
    Article,
    Book,
    Feed,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Profile,
    Replace,
    Robots,
    ThemeColor,
    Twitter,
    TwitterImage,
    TwitterPlayer,
    Verification,
    Viewport,
    resolve_metadata,
)
from next.pages.metadata.backends import metadata_renderer
from next.pages.metadata.markers import Segment, TitleSpec
from next.pages.metadata.normalize import normalize_metadata, normalize_site_metadata
from next.testing import override_next_settings
from tests.support import (
    METADATA_SHAPE_CASES,
    URL_SCHEME_CASES,
    WITH_BASE,
    MetadataShapeCase,
    UrlSchemeCase,
)


SOURCE = "pages/wallet/page.py"


def _segment(raw: object, *, site: bool = False) -> Segment:
    normalize = normalize_site_metadata if site else normalize_metadata
    return normalize(raw, source=SOURCE)


def _meta(raw: object) -> Metadata:
    return _segment(raw).metadata


def _strings(value: object) -> Iterator[str]:
    """Yield every string a normalized segment holds, at any depth."""
    if isinstance(value, str):
        yield value
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            yield from _strings(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


class TestShapeErrors:
    """`normalize_metadata` names the source, the key path and the expected shape."""

    @pytest.mark.parametrize(
        "case", METADATA_SHAPE_CASES, ids=[case.id for case in METADATA_SHAPE_CASES]
    )
    def test_a_rejected_shape_names_where_it_went_wrong(
        self, case: MetadataShapeCase
    ) -> None:
        with pytest.raises(PageMetadataShapeError) as excinfo:
            _segment(case.raw, site=case.site)
        assert case.detail_fragment in excinfo.value.detail
        assert excinfo.value.source == SOURCE
        assert str(excinfo.value) == f"{SOURCE} {excinfo.value.detail}"

    def test_shape_error_is_a_type_error(self) -> None:
        with pytest.raises(TypeError):
            _segment(["x"])


class TestTitle:
    """The title arrives bare or as a dict and leaves as one `TitleSpec`."""

    def test_bare_text_becomes_the_spec_text(self) -> None:
        assert _segment({"title": "Wallet"}).title == TitleSpec(text="Wallet")

    def test_bare_promise_is_kept_unevaluated(self) -> None:
        calls: list[str] = []

        def build() -> str:
            calls.append("evaluated")
            return "Wallet"

        title = lazy(build, str)()
        segment = _segment({"title": title})
        assert segment.title is not None
        assert segment.title.text is title
        assert calls == []

    def test_dict_form_maps_every_key(self) -> None:
        segment = _segment(
            {"title": {"template": "{title} · A", "default": "A", "absolute": "B"}}
        )
        assert segment.title == TitleSpec(
            template="{title} · A", default="A", absolute="B"
        )

    def test_site_tier_takes_template_and_default(self) -> None:
        segment = _segment(
            {"title": {"template": "{title} · A", "default": "A"}}, site=True
        )
        assert segment.title == TitleSpec(template="{title} · A", default="A")


class TestScalars:
    """Top-level scalars are kept as given, and `None` means unset."""

    @pytest.mark.parametrize(
        "raw",
        [{}, {"title": None, "description": None, "og": None, "other": None}],
        ids=["empty", "none_values"],
    )
    def test_nothing_set_is_an_empty_segment(self, raw: dict[str, object]) -> None:
        assert _segment(raw) == Segment(SOURCE)

    def test_source_is_recorded(self) -> None:
        assert _segment({}).source == SOURCE

    def test_lazy_description_is_kept_as_a_promise(self) -> None:
        description = gettext_lazy("Yes")
        assert _meta({"description": description}).description is description

    @pytest.mark.parametrize(
        "canonical",
        [True, "/wallet/", "https://acme.example/wallet/", "//cdn.example/a/"],
        ids=["true", "path", "absolute", "protocol_relative"],
    )
    def test_canonical_takes_true_or_a_url(self, canonical: object) -> None:
        assert _meta({"canonical": canonical}).canonical == canonical

    def test_site_name_is_kept(self) -> None:
        assert _meta({"site_name": "Acme"}).site_name == "Acme"


class TestRobots:
    """Robots arrives as a string or a dict, and `googlebot` nests one level."""

    def test_string_is_kept(self) -> None:
        assert _meta({"robots": "noindex, nofollow"}).robots == "noindex, nofollow"

    def test_dict_builds_the_flags(self) -> None:
        raw = {
            "robots": {
                "index": False,
                "follow": True,
                "noarchive": True,
                "nosnippet": False,
                "noimageindex": True,
                "notranslate": False,
                "unavailable_after": "2026-12-31",
                "max_snippet": 20,
                "max_image_preview": "large",
                "max_video_preview": -1,
            }
        }
        assert _meta(raw).robots == Robots(
            index=False,
            follow=True,
            noarchive=True,
            nosnippet=False,
            noimageindex=True,
            notranslate=False,
            unavailable_after="2026-12-31",
            max_snippet=20,
            max_image_preview="large",
            max_video_preview=-1,
        )

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [
            ({"googlebot": "noindex"}, Robots(googlebot="noindex")),
            (
                {"index": True, "googlebot": {"index": False}},
                Robots(index=True, googlebot=Robots(index=False)),
            ),
        ],
        ids=["text", "dict"],
    )
    def test_googlebot_takes_a_string_or_a_dict(
        self, robots: dict[str, object], expected: Robots
    ) -> None:
        assert _meta({"robots": robots}).robots == expected


class TestOpenGraph:
    """The `og` block builds images and the article sub-block."""

    def test_the_scalars_are_kept_as_given(self) -> None:
        raw = {
            "og": {
                "title": "T",
                "description": "D",
                "url": "/u/",
                "type": "article",
                "site_name": "Acme",
                "locale": "en_GB",
            }
        }
        assert _meta(raw).og == OpenGraph(
            title="T",
            description="D",
            url="/u/",
            type="article",
            site_name="Acme",
            locale="en_GB",
        )

    def test_images_take_strings_and_dicts(self) -> None:
        raw = {
            "og": {
                "images": [
                    "/a.png",
                    {"url": "/b.png", "width": 1200, "height": 630, "alt": "B"},
                ]
            }
        }
        assert _meta(raw).og == OpenGraph(
            images=(
                OpenGraphImage(url="/a.png"),
                OpenGraphImage(url="/b.png", width=1200, height=630, alt="B"),
            )
        )

    def test_the_article_takes_a_bare_date(self) -> None:
        raw = {"og": {"article": {"published_time": date(2026, 1, 2)}}}
        assert _meta(raw).og == OpenGraph(
            article=Article(published_time=date(2026, 1, 2))
        )

    def test_the_article_turns_lists_into_tuples_and_keeps_promises(self) -> None:
        published = datetime(2026, 1, 1, tzinfo=UTC)
        raw = {
            "og": {
                "article": {
                    "published_time": published,
                    "modified_time": "2026-02-01",
                    "authors": ["ada", "grace"],
                    "section": "Finance",
                    "tags": ["money", gettext_lazy("Yes")],
                }
            }
        }
        article = _meta(raw).og.article
        assert article == Article(
            published_time=published,
            modified_time="2026-02-01",
            authors=("ada", "grace"),
            section="Finance",
            tags=("money", article.tags[1]),
        )
        assert isinstance(article.tags[1], Promise)


class TestOtherBlocks:
    """Twitter, alternates, verification, other and JSON-LD normalise to tuples."""

    def test_twitter_keeps_its_scalars_and_tuples_the_images(self) -> None:
        raw = {
            "twitter": {
                "card": "summary",
                "site": "@acme",
                "creator": "@ada",
                "title": "T",
                "description": "D",
                "images": ["/a.png"],
            }
        }
        assert _meta(raw).twitter == Twitter(
            card="summary",
            site="@acme",
            creator="@ada",
            title="T",
            description="D",
            images=(TwitterImage("/a.png"),),
        )

    @pytest.mark.parametrize(
        ("languages", "expected"),
        [
            (True, True),
            (False, False),
            ({"en": "/en/", "de": "/de/"}, (("en", "/en/"), ("de", "/de/"))),
        ],
        ids=["true", "false", "mapping"],
    )
    def test_alternates_take_a_bool_or_a_language_map(
        self, languages: object, expected: object
    ) -> None:
        raw = {"alternates": {"languages": languages, "x_default": "/"}}
        assert _meta(raw).alternates == Alternates(languages=expected, x_default="/")

    def test_an_x_default_language_moves_to_x_default(self) -> None:
        raw = {"alternates": {"languages": {"en": "/en/", "x-default": "/"}}}
        assert _meta(raw).alternates == Alternates(
            languages=(("en", "/en/"),), x_default="/"
        )

    def test_verification_scalars_and_sequences_become_tuples(self) -> None:
        raw = {"verification": {"google": "g", "yandex": ["y1", "y2"], "bing": ()}}
        assert _meta(raw).verification == Verification(
            google=("g",), yandex=("y1", "y2"), bing=()
        )

    def test_other_fans_out_to_pairs(self) -> None:
        lazy_value = gettext_lazy("Yes")
        raw = {"other": {"a": "1", "b": ["2", "3"], "c": lazy_value}}
        assert _meta(raw).other == (
            ("a", "1"),
            ("b", "2"),
            ("b", "3"),
            ("c", lazy_value),
        )

    @pytest.mark.parametrize(
        ("jsonld", "expected"),
        [
            ({"@type": "Thing"}, ({"@type": "Thing"},)),
            ([{"@type": "A"}, {"@type": "B"}], ({"@type": "A"}, {"@type": "B"})),
        ],
        ids=["mapping", "sequence"],
    )
    def test_jsonld_becomes_a_tuple_of_blocks(
        self, jsonld: object, expected: tuple[dict[str, str], ...]
    ) -> None:
        assert _meta({"jsonld": jsonld}).jsonld == expected

    def test_jsonld_is_copied_into_plain_dicts_and_lists(self) -> None:
        name = gettext_lazy("Yes")
        raw = MappingProxyType(
            {
                "@type": "Thing",
                "sameAs": ("https://a.example", "https://b.example"),
                "offers": MappingProxyType({"price": 1.5, "name": name}),
            }
        )
        (copied,) = _meta({"jsonld": raw}).jsonld
        assert type(copied) is dict
        assert copied == {
            "@type": "Thing",
            "sameAs": ["https://a.example", "https://b.example"],
            "offers": {"price": 1.5, "name": name},
        }
        assert type(copied["offers"]) is dict
        assert copied["offers"]["name"] is name

    @override_next_settings(**WITH_BASE)
    def test_jsonld_keeps_every_value_the_encoder_writes(self) -> None:
        raw = {
            "@type": "Event",
            "name": gettext_lazy("Launch"),
            "free": True,
            "seats": 3,
            "rating": 4.5,
            "sponsor": None,
            "startDate": date(2026, 1, 2),
            "doorTime": time(18, 30),
            "duration": timedelta(hours=2),
            "price": Decimal("9.50"),
            "identifier": UUID(int=1),
            "organizer": ld.Ref("#org"),
        }
        (copied,) = _meta({"jsonld": raw}).jsonld
        assert copied == raw
        html = metadata_renderer().render(
            resolve_metadata(Metadata(jsonld=(copied,)), request=None)
        )
        assert '"doorTime": "18:30:00"' in html
        assert '"price": "9.50"' in html


class TestHeadKeys:
    """The head keys beside title and robots normalise into their value objects."""

    def test_keywords_take_text_or_a_sequence(self) -> None:
        lazy_word = gettext_lazy("rockets")
        assert _meta({"keywords": "acme"}).keywords == ("acme",)
        assert _meta({"keywords": ["acme", lazy_word]}).keywords == ("acme", lazy_word)

    def test_icons_flatten_in_render_order_with_their_rels(self) -> None:
        raw = {
            "icons": {
                "other": [{"rel": "mask-icon", "url": "/m.svg", "color": "#123"}],
                "apple": {"url": "/a.png", "sizes": "180x180"},
                "icon": ["/i.ico", {"url": "/i.svg", "type": "image/svg+xml"}],
            }
        }
        assert _meta(raw).icons == (
            Icon("icon", "/i.ico"),
            Icon("icon", "/i.svg", type="image/svg+xml"),
            Icon("apple-touch-icon", "/a.png", sizes="180x180"),
            Icon("mask-icon", "/m.svg", color="#123"),
        )

    def test_a_bare_icon_url_is_one_icon(self) -> None:
        assert _meta({"icons": {"apple": "/a.png"}}).icons == (
            Icon("apple-touch-icon", "/a.png"),
        )

    def test_the_manifest_and_the_color_scheme_are_kept(self) -> None:
        meta = _meta({"manifest": "/app.webmanifest", "color_scheme": "dark"})
        assert (meta.manifest, meta.color_scheme) == ("/app.webmanifest", "dark")

    def test_a_link_keeps_its_attributes_in_render_order(self) -> None:
        raw = {
            "links": [
                {
                    "href": "/font.woff2",
                    "crossorigin": True,
                    "type": "font/woff2",
                    "as": "font",
                    "rel": "preload",
                },
                {
                    "rel": "preconnect",
                    "href": "https://cdn.example",
                    "crossorigin": False,
                },
                {"rel": "me", "href": "/me", "crossorigin": "use-credentials"},
            ]
        }
        assert _meta(raw).links == (
            Link(
                "preload",
                "/font.woff2",
                (("as", "font"), ("type", "font/woff2"), ("crossorigin", "anonymous")),
            ),
            Link("preconnect", "https://cdn.example"),
            Link("me", "/me", (("crossorigin", "use-credentials"),)),
        )

    def test_properties_fan_out_to_pairs(self) -> None:
        raw = {"properties": {"fb:app_id": "1", "product:tag": ["a", "b"]}}
        assert _meta(raw).properties == (
            ("fb:app_id", "1"),
            ("product:tag", "a"),
            ("product:tag", "b"),
        )

    @pytest.mark.parametrize(
        ("viewport", "expected"),
        [
            ("width=device-width", "width=device-width"),
            (
                {"width": "device-width", "initial_scale": 1, "user_scalable": False},
                Viewport(width="device-width", initial_scale=1, user_scalable=False),
            ),
            (
                {"height": 600, "maximum_scale": 2.5},
                Viewport(height=600, maximum_scale=2.5),
            ),
        ],
        ids=["string", "mapping", "numbers"],
    )
    def test_the_viewport_takes_a_string_or_a_mapping(
        self, viewport: object, expected: object
    ) -> None:
        assert _meta({"viewport": viewport}).viewport == expected

    def test_a_theme_color_string_is_one_color(self) -> None:
        assert _meta({"theme_color": "#fff"}).theme_color == (ThemeColor("#fff"),)

    def test_theme_colors_take_a_media_query_each(self) -> None:
        raw = {
            "theme_color": [
                {"color": "#fff", "media": "(prefers-color-scheme: light)"},
                {"color": "#000"},
            ]
        }
        assert _meta(raw).theme_color == (
            ThemeColor("#fff", "(prefers-color-scheme: light)"),
            ThemeColor("#000"),
        )

    @pytest.mark.parametrize(
        "breadcrumb",
        ["Rocket", False, gettext_lazy("Rocket")],
        ids=["text", "false", "lazy"],
    )
    def test_the_breadcrumb_takes_text_or_false(self, breadcrumb: object) -> None:
        assert _segment({"breadcrumb": breadcrumb}).breadcrumb is breadcrumb

    def test_the_feeds_ride_in_the_alternates(self) -> None:
        raw = {
            "alternates": {
                "feeds": [
                    {"url": "/feed.xml", "type": "rss", "title": "Blog"},
                    {"url": "/feed.json", "type": "json"},
                ]
            }
        }
        assert _meta(raw).alternates == Alternates(
            feeds=(Feed("/feed.xml", "rss", "Blog"), Feed("/feed.json", "json"))
        )

    def test_the_og_media_and_objects_normalise(self) -> None:
        raw = {
            "og": {
                "locale_alternates": ["de_DE"],
                "determiner": "the",
                "images": [
                    {"url": "/a.png", "secure_url": "/s.png", "type": "image/png"}
                ],
                "videos": ["/v.mp4", {"url": "/w.mp4", "width": 640, "height": 360}],
                "audio": ["/a.mp3", {"url": "/b.mp3", "type": "audio/mpeg"}],
                "profile": {"first_name": "Ann", "gender": "female"},
                "book": {
                    "authors": ["Ann"],
                    "isbn": "978",
                    "release_date": date(2026, 1, 2),
                },
            }
        }
        assert _meta(raw).og == OpenGraph(
            locale_alternates=("de_DE",),
            determiner="the",
            images=(
                OpenGraphImage(url="/a.png", secure_url="/s.png", type="image/png"),
            ),
            videos=(
                OpenGraphVideo("/v.mp4"),
                OpenGraphVideo("/w.mp4", width=640, height=360),
            ),
            audio=(
                OpenGraphAudio("/a.mp3"),
                OpenGraphAudio("/b.mp3", type="audio/mpeg"),
            ),
            profile=Profile(first_name="Ann", gender="female"),
            book=Book(authors=("Ann",), isbn="978", release_date=date(2026, 1, 2)),
        )

    def test_the_og_locale_alternates_take_a_bool(self) -> None:
        og = _meta({"og": {"locale_alternates": True}}).og
        assert og is not None
        assert og.locale_alternates is True

    def test_the_twitter_ids_images_and_player_normalise(self) -> None:
        raw = {
            "twitter": {
                "site_id": "1",
                "creator_id": "2",
                "images": ["/a.png", {"url": "/b.png", "alt": "B"}],
                "player": {"url": "/p", "width": 640, "height": 360, "stream": "/s"},
            }
        }
        assert _meta(raw).twitter == Twitter(
            site_id="1",
            creator_id="2",
            images=(TwitterImage("/a.png"), TwitterImage("/b.png", "B")),
            player=TwitterPlayer("/p", 640, 360, "/s"),
        )

    def test_verification_takes_the_new_engines_and_other_names(self) -> None:
        raw = {
            "verification": {
                "pinterest": "p",
                "facebook": ["f1", "f2"],
                "other": {"baidu-site-verification": "b"},
            }
        }
        assert _meta(raw).verification == Verification(
            pinterest=("p",),
            facebook=("f1", "f2"),
            other=(("baidu-site-verification", "b"),),
        )

    def test_a_typed_node_is_kept_as_it_is(self) -> None:
        node = ld.Node(id="#org", type="Organization")
        raw = {"jsonld": [node, {"@type": "Thing", "author": ld.Ref("#ann")}]}
        first, second = _meta(raw).jsonld
        assert first is node
        assert second == {"@type": "Thing", "author": ld.Ref("#ann")}
        assert _meta({"jsonld": node}).jsonld == (node,)


class TestUrlSchemes:
    """Every URL field takes http, https or a relative URL, naming the key otherwise."""

    @pytest.mark.parametrize(
        "case", URL_SCHEME_CASES, ids=[case.id for case in URL_SCHEME_CASES]
    )
    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "ftp://x/"],
        ids=["javascript", "padded_upper_case", "data", "ftp"],
    )
    def test_a_foreign_scheme_names_the_key_path(
        self, case: UrlSchemeCase, url: str
    ) -> None:
        with pytest.raises(PageMetadataShapeError) as caught:
            _segment(case.build(url))
        assert caught.value.source == SOURCE
        assert caught.value.detail == (
            f"declares metadata key {case.path!r} as the URL {url!r}, expected an "
            "http or https URL or a path"
        )

    @pytest.mark.parametrize(
        "case", URL_SCHEME_CASES, ids=[case.id for case in URL_SCHEME_CASES]
    )
    @pytest.mark.parametrize(
        "url",
        ["https://acme.example/a/", "http://acme.example/", "/a/", "a/b", "//cdn/a"],
        ids=["https", "http", "root", "relative", "protocol_relative"],
    )
    def test_a_web_url_is_kept(self, case: UrlSchemeCase, url: str) -> None:
        assert url in _strings(_segment(case.build(url)))

    def test_a_malformed_url_is_refused(self) -> None:
        with pytest.raises(PageMetadataShapeError, match="as the URL 'http://\\['"):
            _segment({"canonical": "http://["})


def _promises(value: object) -> Iterator[Promise]:
    """Yield every lazy value a normalized segment holds, at any depth."""
    if isinstance(value, Promise):
        yield value
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            yield from _promises(getattr(value, field.name))
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _promises(item)


class TestLazyUrls:
    """A lazy URL is kept unforced and checked each time a render forces it."""

    @pytest.mark.parametrize(
        "case", URL_SCHEME_CASES, ids=[case.id for case in URL_SCHEME_CASES]
    )
    def test_a_lazy_url_is_kept_unforced(self, case: UrlSchemeCase) -> None:
        calls: list[str] = []

        def build() -> str:
            calls.append("forced")
            return "/feed/"

        (kept,) = _promises(_segment(case.build(lazy(build, str)())))
        assert calls == []
        assert str(kept) == "/feed/"
        assert calls == ["forced"]

    @pytest.mark.parametrize(
        "case", URL_SCHEME_CASES, ids=[case.id for case in URL_SCHEME_CASES]
    )
    def test_a_foreign_scheme_fails_once_forced(self, case: UrlSchemeCase) -> None:
        (kept,) = _promises(_segment(case.build(lazy(str, str)("ftp://x/"))))
        with pytest.raises(PageMetadataShapeError) as caught:
            str(kept)
        assert caught.value.source == SOURCE
        assert caught.value.detail == (
            f"declares metadata key {case.path!r} as the URL 'ftp://x/', expected an "
            "http or https URL or a path"
        )

    def test_a_plain_url_stays_the_string_declared(self) -> None:
        url = "/wallet/"
        assert _meta({"canonical": url}).canonical is url


class TestReplace:
    """A `Replace` is unwrapped and its dotted key path recorded, at any depth."""

    def test_nothing_replaced_records_no_path(self) -> None:
        assert _segment({"title": "T"}).replaced == frozenset()

    @pytest.mark.parametrize(
        ("raw", "field", "value", "paths"),
        [
            ({"canonical": RESET}, "canonical", None, {"canonical"}),
            ({"og": Replace({"title": "T"})}, "og", OpenGraph(title="T"), {"og"}),
            ({"og": {"images": RESET}}, "og", OpenGraph(), {"og.images"}),
            (
                {"robots": {"googlebot": {"index": RESET}}},
                "robots",
                Robots(googlebot=Robots()),
                {"robots.googlebot.index"},
            ),
            (
                {"jsonld": Replace([{"@type": "A"}])},
                "jsonld",
                ({"@type": "A"},),
                {"jsonld"},
            ),
            (
                {"other": {"application-name": RESET}},
                "other",
                (),
                {"other.application-name"},
            ),
            (
                {"other": {"a": Replace(["1", "2"])}},
                "other",
                (("a", "1"), ("a", "2")),
                {"other.a"},
            ),
        ],
        ids=[
            "canonical_reset",
            "og_whole",
            "og_images_reset",
            "googlebot_flag_reset",
            "jsonld_whole",
            "other_name_reset",
            "other_name_replaced",
        ],
    )
    def test_the_path_is_recorded_and_the_value_unwrapped(
        self, raw: dict[str, object], field: str, value: object, paths: set[str]
    ) -> None:
        segment = _segment(raw)
        assert getattr(segment.metadata, field) == value
        assert segment.replaced == paths

    def test_a_reset_title_leaves_the_segment_without_one(self) -> None:
        segment = _segment({"title": RESET})
        assert segment.title is None
        assert segment.replaced == {"title"}

    def test_the_site_tier_records_a_replace_too(self) -> None:
        segment = _segment({"description": RESET}, site=True)
        assert segment.replaced == {"description"}
