from dataclasses import fields, replace

import pytest
from django.utils import translation
from django.utils.functional import Promise, lazy
from django.utils.translation import gettext, gettext_lazy

from next.pages import ld
from next.pages.metadata import (
    RESET,
    Alternates,
    Article,
    Metadata,
    OpenGraph,
    OpenGraphImage,
    Replace,
    Robots,
    Twitter,
    TwitterImage,
    Verification,
)
from next.pages.metadata.fold import (
    EMPTY_STATE,
    finish,
    fold_metadata,
    fold_segment,
    fold_segments,
    merge_field,
    merge_segments,
    strategies,
    trace_origins,
)
from next.pages.metadata.markers import Crumb, Merge, Segment, TitleSpec
from next.pages.metadata.normalize import normalize_metadata
from tests.support import METADATA_MERGE_CASES, MetadataMergeCase


def _chain(*raw: object) -> list[Segment]:
    return [
        normalize_metadata(item, source=f"segment[{index}]")
        for index, item in enumerate(raw)
    ]


def _fold(*raw: object) -> Metadata:
    return fold_metadata(_chain(*raw))


class TestFold:
    """The chain folds root to leaf, each field under its merge strategy."""

    @pytest.mark.parametrize(
        "case", METADATA_MERGE_CASES, ids=[case.id for case in METADATA_MERGE_CASES]
    )
    def test_the_chain_settles_on_the_expected_values(
        self, case: MetadataMergeCase
    ) -> None:
        meta = fold_metadata(_chain(*case.segments_raw))
        title = None if meta.title is None else str(meta.title)
        assert title == case.expected_title
        for name, expected in case.expected_fields.items():
            assert getattr(meta, name) == expected, name

    def test_empty_chain_is_the_empty_value(self) -> None:
        assert fold_metadata(()) == Metadata()
        assert finish(EMPTY_STATE) == Metadata()

    def test_fold_accepts_any_iterable(self) -> None:
        assert fold_metadata(iter(_chain({"title": "Wallet"}))).title == "Wallet"

    def test_a_state_folds_on_where_it_left_off(self) -> None:
        root, leaf = _chain({"description": "Root"}, {"title": "Leaf"})
        prefix = fold_segment(EMPTY_STATE, root)
        assert finish(fold_segments((leaf,), prefix)) == finish(
            fold_segments((root, leaf))
        )

    def test_the_strategies_are_read_once_per_class(self) -> None:
        assert strategies(Robots) is strategies(Robots)
        assert strategies(str) == ()
        assert {name for name, _ in strategies(Metadata)} == {
            field.name for field in fields(Metadata)
        }


class TestDeepMerge:
    """A block merges field by field, and every list inside it is replaced."""

    def test_og_keeps_the_site_images_and_type_under_a_page_title(self) -> None:
        meta = _fold(
            {"og": {"type": "website", "images": ["/og.png"]}},
            {"og": {"title": "Spring sale"}},
        )
        assert meta.og == OpenGraph(
            title="Spring sale", type="website", images=(OpenGraphImage(url="/og.png"),)
        )

    def test_og_images_are_replaced_whole(self) -> None:
        meta = _fold(
            {"og": {"images": ["/a.png", "/b.png"]}}, {"og": {"images": ["/c.png"]}}
        )
        assert meta.og == OpenGraph(images=(OpenGraphImage(url="/c.png"),))

    def test_the_article_merges_and_its_lists_are_replaced(self) -> None:
        meta = _fold(
            {"og": {"article": {"section": "News", "tags": ["a", "b"]}}},
            {"og": {"article": {"tags": ["c"], "authors": ["Ann"]}}},
        )
        assert meta.og == OpenGraph(
            article=Article(section="News", tags=("c",), authors=("Ann",))
        )

    def test_twitter_merges_and_its_images_are_replaced(self) -> None:
        meta = _fold(
            {"twitter": {"card": "summary", "images": ["/a.png"]}},
            {"twitter": {"title": "T", "images": ["/b.png"]}},
        )
        assert meta.twitter == Twitter(
            card="summary", title="T", images=(TwitterImage("/b.png"),)
        )

    def test_alternates_merge_and_languages_are_replaced(self) -> None:
        meta = _fold(
            {"alternates": {"languages": {"en": "/en/"}, "x_default": "/"}},
            {"alternates": {"languages": {"de": "/de/"}}},
        )
        assert meta.alternates == Alternates(languages=(("de", "/de/"),), x_default="/")

    def test_an_x_default_alone_keeps_the_inherited_languages(self) -> None:
        meta = _fold(
            {"alternates": {"languages": {"en": "/en/"}, "x_default": "/"}},
            {"alternates": {"languages": {"x-default": "/all/"}}},
        )
        assert meta.alternates == Alternates(
            languages=(("en", "/en/"),), x_default="/all/"
        )

    def test_verification_merges_per_engine(self) -> None:
        meta = _fold(
            {"verification": {"google": "g", "bing": "b"}},
            {"verification": {"google": ["g2"]}},
        )
        assert meta.verification == Verification(google=("g2",), bing=("b",))

    @pytest.mark.parametrize(
        ("root", "leaf", "expected"),
        [
            ("all", {"index": False}, Robots(index=False)),
            ({"index": False}, "all", "all"),
            ("all", "none", "none"),
            (
                {"googlebot": "noindex"},
                {"googlebot": {"index": True}},
                Robots(googlebot=Robots(index=True)),
            ),
            (
                {"googlebot": {"index": True}},
                {"googlebot": "none"},
                Robots(googlebot="none"),
            ),
        ],
        ids=[
            "dict_over_text",
            "text_over_dict",
            "text_over_text",
            "googlebot_dict",
            "googlebot_text",
        ],
    )
    def test_robots_and_text_replace_each_other_whole(
        self, root: object, leaf: object, expected: object
    ) -> None:
        assert _fold({"robots": root}, {"robots": leaf}).robots == expected

    def test_none_from_a_later_segment_inherits(self) -> None:
        meta = _fold(
            {"description": "Site", "og": {"type": "website"}},
            {"description": None, "og": {"type": None, "title": "T"}},
        )
        assert meta.description == "Site"
        assert meta.og == OpenGraph(type="website", title="T")


class TestByName:
    """`other` merges by name, keeping the older order and appending new names."""

    def test_a_nearer_name_replaces_every_value_in_place(self) -> None:
        meta = _fold(
            {"other": {"a": "1", "b": ["2", "3"], "c": "4"}},
            {"other": {"b": "5", "d": "6"}},
        )
        assert meta.other == (("a", "1"), ("b", "5"), ("c", "4"), ("d", "6"))

    def test_reset_drops_one_inherited_name(self) -> None:
        meta = _fold(
            {"other": {"application-name": "#111", "a": "1"}},
            {"other": {"application-name": RESET}},
        )
        assert meta.other == (("a", "1"),)

    def test_replace_on_a_name_keeps_the_new_values(self) -> None:
        meta = _fold(
            {"other": {"a": "1", "b": "2"}}, {"other": {"a": Replace(["x", "y"])}}
        )
        assert meta.other == (("a", "x"), ("a", "y"), ("b", "2"))

    def test_replace_on_the_whole_mapping_drops_every_inherited_name(self) -> None:
        meta = _fold({"other": {"a": "1"}}, {"other": Replace({"b": "2"})})
        assert meta.other == (("b", "2"),)


class TestById:
    """JSON-LD replaces an object in place by its `@id` and appends the rest."""

    def test_an_id_is_replaced_in_place(self) -> None:
        meta = _fold(
            {"jsonld": [{"@id": "#org", "name": "Old"}, {"@id": "#site"}]},
            {"jsonld": [{"@id": "#org", "name": "New"}, {"@type": "Product"}]},
        )
        assert meta.jsonld == (
            {"@id": "#org", "name": "New"},
            {"@id": "#site"},
            {"@type": "Product"},
        )

    def test_a_new_id_is_appended_once(self) -> None:
        meta = _fold(
            {"jsonld": {"@id": "#org"}},
            {"jsonld": [{"@id": "#p", "v": 1}, {"@id": "#p", "v": 2}]},
        )
        assert meta.jsonld == ({"@id": "#org"}, {"@id": "#p", "v": 2})

    def test_replace_drops_the_inherited_graph(self) -> None:
        meta = _fold({"jsonld": {"@id": "#org"}}, {"jsonld": Replace([{"@id": "#p"}])})
        assert meta.jsonld == ({"@id": "#p"},)

    def test_a_typed_node_is_matched_by_its_raw_id(self) -> None:
        site = ld.Node(id="#org", extra={"name": "Acme"})
        page = ld.Node(id="#org", extra={"name": "Acme GmbH"})
        ann = ld.ListItem(name="Ann", position=1)
        meta = _fold({"jsonld": [site, {"@id": "#site"}]}, {"jsonld": [page, ann]})
        assert meta.jsonld == (page, {"@id": "#site"}, ann)

    def test_a_typed_node_traces_under_its_raw_id(self) -> None:
        origins = trace_origins(
            _chain({"jsonld": [ld.Node(id="#ann"), ld.Node(type="Person")]})
        )
        assert origins == {"jsonld[#ann]": "segment[0]", "jsonld[0]": "segment[0]"}


class TestBreadcrumbs:
    """Each segment with a trail adds its own crumb, never inherited."""

    def _trailed(self, *raw: tuple[str, object]) -> list[Segment]:
        return [
            replace(normalize_metadata(item, source=trail), trail=trail)
            for trail, item in raw
        ]

    def test_a_crumb_takes_the_breadcrumb_then_the_own_title(self) -> None:
        meta = fold_metadata(
            self._trailed(
                ("", {"title": {"template": "{title} | A", "default": "A"}}),
                ("blog", {"title": "Blog", "breadcrumb": "All posts"}),
                ("blog/[slug]", {"title": {"absolute": "Post"}}),
            )
        )
        assert meta.breadcrumbs == (
            Crumb("All posts", "blog"),
            Crumb("Post", "blog/[slug]"),
        )

    def test_false_and_a_label_less_segment_add_no_crumb(self) -> None:
        meta = fold_metadata(
            self._trailed(
                ("", {"title": "Home", "breadcrumb": False}),
                ("docs", {"description": "D"}),
                ("docs/intro", {"breadcrumb": "Intro"}),
            )
        )
        assert meta.breadcrumbs == (Crumb("Intro", "docs/intro"),)

    def test_a_segment_without_a_trail_adds_no_crumb(self) -> None:
        assert _fold({"title": "Home"}).breadcrumbs == ()

    def test_merging_keeps_the_base_trail_and_label_under_an_overlay(self) -> None:
        (base,) = self._trailed(("blog", {"breadcrumb": "Blog"}))
        over = Segment("overlay", title=TitleSpec(text="New"))
        merged = merge_segments(base, over)
        assert (merged.trail, merged.breadcrumb) == ("blog", "Blog")
        relabelled = merge_segments(
            base, replace(over, breadcrumb=False, trail="other")
        )
        assert (relabelled.trail, relabelled.breadcrumb) == ("other", False)


class TestReplaceAndReset:
    """A replaced path takes the nearer value whole, at every depth."""

    @pytest.mark.parametrize(
        ("leaf", "field", "expected"),
        [
            ({"title": RESET}, "title", None),
            ({"description": RESET}, "description", None),
            ({"canonical": RESET}, "canonical", None),
            ({"og": RESET}, "og", None),
            ({"og": Replace({"title": "Only"})}, "og", OpenGraph(title="Only")),
            ({"og": {"images": RESET}}, "og", OpenGraph(type="website")),
            ({"robots": Replace({"follow": True})}, "robots", Robots(follow=True)),
            ({"robots": {"index": RESET}}, "robots", Robots(follow=False)),
            ({"jsonld": RESET}, "jsonld", ()),
            ({"other": RESET}, "other", ()),
        ],
        ids=[
            "title",
            "description",
            "canonical",
            "og_whole",
            "og_replaced",
            "og_images",
            "robots_replaced",
            "robots_flag",
            "jsonld",
            "other",
        ],
    )
    def test_the_inherited_value_is_dropped(
        self, leaf: dict[str, object], field: str, expected: object
    ) -> None:
        root = {
            "title": "Root",
            "description": "Root",
            "canonical": True,
            "og": {"type": "website", "images": ["/a.png"]},
            "robots": {"index": False, "follow": False},
            "jsonld": {"@id": "#org"},
            "other": {"a": "1"},
        }
        assert getattr(_fold(root, leaf), field) == expected

    def test_a_reset_title_drops_the_inherited_template(self) -> None:
        meta = _fold(
            {"title": {"template": "{title} · Acme"}},
            {"title": Replace({"default": "Fresh"})},
        )
        assert meta.title == "Fresh"
        assert _fold(
            {"title": {"template": "{title} · Acme"}}, {"title": RESET}, {"title": "X"}
        ).title == ("X")

    def test_merge_field_takes_a_replaced_value_even_unset(self) -> None:
        replaced = frozenset({"x"})
        assert (
            merge_field(Merge.REPLACE, "older", None, path="x", replaced=replaced)
            is None
        )
        assert merge_field(Merge.REPLACE, "older", None, path="x") == "older"


class TestLaziness:
    """A templated title leaves the fold as a `Promise` nobody has evaluated."""

    def test_templated_title_is_a_promise(self) -> None:
        meta = _fold({"title": {"template": "{title} · Acme"}}, {"title": "Wallet"})
        assert isinstance(meta.title, Promise)

    def test_fold_never_evaluates_the_promises(self) -> None:
        calls: list[str] = []

        def build(value: str) -> str:
            calls.append(value)
            return value

        template = lazy(build, str)("{title} · {site_name}")
        text = lazy(build, str)("Wallet")
        site_name = lazy(build, str)("Acme")
        meta = _fold(
            {
                "title": {"template": template},
                "site_name": site_name,
                "og": {"title": text},
                "other": {"a": text},
            },
            {"title": text, "og": {"description": text}, "other": {"a": text}},
        )
        assert calls == []
        assert str(meta.title) == "Wallet · Acme"
        assert sorted(calls) == ["Acme", "Wallet", "{title} · {site_name}"]

    def test_one_folded_value_answers_each_language(self) -> None:
        template = lazy(lambda: "{title} · " + gettext("Yes"), str)()
        meta = _fold(
            {"title": {"template": template, "default": "Acme"}},
            {"title": gettext_lazy("No")},
        )
        with translation.override("de"):
            assert str(meta.title) == "Nein · Ja"
        with translation.override("en"):
            assert str(meta.title) == "No · Yes"

    def test_site_name_decision_waits_for_the_translation(self) -> None:
        template = lazy(lambda: "{title} · {site_name}", str)()
        meta = _fold({"title": {"template": template}}, {"title": "Wallet"})
        assert isinstance(meta.title, Promise)
        assert str(meta.title) == "Wallet"


class TestTraceOrigins:
    """Every settled key names the segment that settled it, as `showmetadata` reads."""

    def test_the_nearest_source_of_each_key_is_named(self) -> None:
        origins = trace_origins(
            _chain(
                {
                    "title": {"template": "{title} · A", "default": "A"},
                    "og": {"type": "website", "images": ["/a.png"]},
                    "other": {"a": "1"},
                    "jsonld": [{"@id": "#org"}, {"@type": "Thing"}],
                    "robots": "all",
                },
                {
                    "title": "Leaf",
                    "og": {"title": "T"},
                    "other": {"b": "2"},
                    "jsonld": [{"@type": "Other"}],
                    "robots": {"index": False},
                },
            )
        )
        assert origins == {
            "title": "segment[1]",
            "og.type": "segment[0]",
            "og.images": "segment[0]",
            "og.title": "segment[1]",
            "other.a": "segment[0]",
            "other.b": "segment[1]",
            "jsonld[#org]": "segment[0]",
            "jsonld[0]": "segment[0]",
            "jsonld[1]": "segment[1]",
            "robots.index": "segment[1]",
        }

    def test_a_block_replaced_by_text_forgets_its_fields(self) -> None:
        origins = trace_origins(_chain({"robots": {"index": False}}, {"robots": "all"}))
        assert origins == {"robots": "segment[1]"}

    def test_a_reset_names_the_segment_that_dropped_the_key(self) -> None:
        origins = trace_origins(_chain({"og": {"type": "website"}}, {"og": RESET}))
        assert origins == {"og": "segment[1]"}
