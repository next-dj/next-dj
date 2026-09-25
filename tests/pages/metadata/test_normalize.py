from datetime import UTC, datetime

import pytest
from django.utils.functional import Promise, lazy
from django.utils.translation import gettext_lazy

from next.pages.errors import PageMetadataShapeError
from next.pages.metadata import Segment, normalize_metadata, normalize_site_metadata
from next.pages.metadata.schema import (
    Alternates,
    Article,
    OpenGraph,
    OpenGraphImage,
    Robots,
    TitleSpec,
    Twitter,
    Verification,
)
from tests.support import METADATA_SHAPE_CASES, MetadataShapeCase


SOURCE = "pages/wallet/page.py"


def _segment(raw: object, *, site: bool = False) -> Segment:
    normalize = normalize_site_metadata if site else normalize_metadata
    return normalize(raw, source=SOURCE)


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
        assert _segment({"description": description}).description is description

    @pytest.mark.parametrize(
        "canonical", [True, False, "/wallet/"], ids=["true", "false", "path"]
    )
    def test_canonical_takes_a_bool_or_a_string(self, canonical: object) -> None:
        assert _segment({"canonical": canonical}).canonical == canonical

    def test_base_and_site_name_are_kept(self) -> None:
        segment = _segment({"base": "https://acme.example", "site_name": "Acme"})
        assert segment.base == "https://acme.example"
        assert segment.site_name == "Acme"


class TestRobots:
    """Robots arrives as a string or a dict, and `googlebot` nests one level."""

    def test_string_is_kept(self) -> None:
        assert _segment({"robots": "noindex, nofollow"}).robots == "noindex, nofollow"

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
        assert _segment(raw).robots == Robots(
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
        assert _segment({"robots": robots}).robots == expected


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
        assert _segment(raw).og == OpenGraph(
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
        assert _segment(raw).og == OpenGraph(
            images=(
                OpenGraphImage(url="/a.png"),
                OpenGraphImage(url="/b.png", width=1200, height=630, alt="B"),
            )
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
        article = _segment(raw).og.article
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
        assert _segment(raw).twitter == Twitter(
            card="summary",
            site="@acme",
            creator="@ada",
            title="T",
            description="D",
            images=("/a.png",),
        )

    @pytest.mark.parametrize(
        "languages",
        [True, False, {"en": "/en/", "de": "/de/"}],
        ids=["true", "false", "mapping"],
    )
    def test_alternates_take_a_bool_or_a_language_map(self, languages: object) -> None:
        raw = {"alternates": {"languages": languages, "x_default": "/"}}
        assert _segment(raw).alternates == Alternates(
            languages=languages, x_default="/"
        )

    def test_verification_scalars_and_sequences_become_tuples(self) -> None:
        raw = {"verification": {"google": "g", "yandex": ["y1", "y2"], "bing": ()}}
        assert _segment(raw).verification == Verification(
            google=("g",), yandex=("y1", "y2"), bing=()
        )

    def test_other_fans_out_to_pairs(self) -> None:
        lazy_value = gettext_lazy("Yes")
        raw = {"other": {"a": "1", "b": ["2", "3"], "c": lazy_value}}
        assert _segment(raw).other == (
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
        assert _segment({"jsonld": jsonld}).jsonld == expected
