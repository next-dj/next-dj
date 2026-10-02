from dataclasses import FrozenInstanceError, fields

import pytest

from next.pages.metadata import (
    RESET,
    Alternates,
    Article,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphImage,
    Replace,
    Robots,
    Twitter,
    Verification,
    Viewport,
)
from next.pages.metadata.markers import (
    MERGE_KEY,
    NO_BREADCRUMBS,
    Breadcrumb,
    Breadcrumbs,
    Merge,
    ResolvedMetadata,
    Segment,
    robots_directives,
    robots_noindex,
)
from next.pages.metadata.normalize import normalize_metadata


SOURCE = "pages/wallet/page.py"


class TestValueObjects:
    """The folded value is immutable and the segment mirrors it."""

    @pytest.mark.parametrize(
        ("value", "attribute"),
        [(Metadata(), "title"), (Segment(SOURCE), "title"), (RESET, "value")],
        ids=["metadata", "segment", "reset"],
    )
    def test_the_values_are_frozen(self, value: object, attribute: str) -> None:
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, "x")

    def test_empty_metadata_is_the_default_value(self) -> None:
        assert Metadata() == Metadata()

    def test_a_segment_holds_empty_metadata_by_default(self) -> None:
        assert Segment(SOURCE).metadata == Metadata()

    def test_resolved_metadata_carries_the_fold_it_came_from(self) -> None:
        names = {field.name for field in fields(ResolvedMetadata)}
        assert {"source", "noindex", "canonical", "alternates"} <= names


class TestHashing:
    """A fold with hreflang alternates hashes, equal folds alike."""

    def test_a_fold_with_languages_hashes(self) -> None:
        languages = (("en", "/en/"), ("de", "/de/"))
        first = Metadata(alternates=Alternates(languages=languages, x_default="/"))
        second = Metadata(alternates=Alternates(languages=languages, x_default="/"))
        assert first == second
        assert hash(first) == hash(second)

    def test_a_normalised_language_map_hashes(self) -> None:
        raw = {"alternates": {"languages": {"en": "/en/", "x-default": "/"}}}
        meta = normalize_metadata(raw, source=SOURCE).metadata
        assert hash(meta) == hash(
            Metadata(alternates=Alternates(languages=(("en", "/en/"),), x_default="/"))
        )


class TestBreadcrumbs:
    """The resolved crumbs build once, on the first read, and compare by value."""

    def test_the_build_runs_on_the_first_read_alone(self) -> None:
        calls: list[int] = []

        def build() -> tuple[Breadcrumb, ...]:
            calls.append(1)
            return (Breadcrumb("Home", "/"),)

        crumbs = Breadcrumbs(build)
        assert calls == []
        assert crumbs.items() == crumbs.items() == (Breadcrumb("Home", "/"),)
        assert calls == [1]

    def test_two_builds_of_the_same_crumbs_are_equal(self) -> None:
        first = Breadcrumbs(lambda: (Breadcrumb("Home", "/"),))
        second = Breadcrumbs(lambda: (Breadcrumb("Home", "/"),))
        assert first == second
        assert hash(first) == hash(second)
        assert first != Breadcrumbs(lambda: (Breadcrumb("Blog", "/blog/"),))

    def test_crumbs_differ_from_a_plain_tuple(self) -> None:
        assert Breadcrumbs(tuple) != ()

    def test_the_repr_shows_the_crumbs(self) -> None:
        crumbs = Breadcrumbs(lambda: (Breadcrumb("Home", "/"),))
        assert repr(crumbs) == f"Breadcrumbs({(Breadcrumb('Home', '/'),)!r})"

    def test_a_resolve_without_crumbs_reads_none(self) -> None:
        assert NO_BREADCRUMBS.items() == ()


class TestReplace:
    """`Replace` wraps a value to take whole, and `RESET` wraps nothing."""

    def test_reset_is_an_empty_replace(self) -> None:
        assert Replace() == RESET
        assert RESET.value is None

    def test_replace_keeps_its_value(self) -> None:
        assert Replace({"title": "T"}).value == {"title": "T"}


class TestMergeStrategies:
    """Each block field names its strategy, the rest fold by the nearest value."""

    @pytest.mark.parametrize(
        ("cls", "name", "strategy"),
        [
            (Metadata, "robots", Merge.DEEP),
            (Metadata, "alternates", Merge.DEEP),
            (Metadata, "og", Merge.DEEP),
            (Metadata, "twitter", Merge.DEEP),
            (Metadata, "verification", Merge.DEEP),
            (Metadata, "other", Merge.BY_NAME),
            (Metadata, "properties", Merge.BY_NAME),
            (Metadata, "jsonld", Merge.BY_ID),
            (Robots, "googlebot", Merge.DEEP),
            (OpenGraph, "article", Merge.DEEP),
            (OpenGraph, "profile", Merge.DEEP),
            (OpenGraph, "book", Merge.DEEP),
            (Verification, "other", Merge.BY_NAME),
        ],
    )
    def test_a_block_field_declares_its_strategy(
        self, cls: type, name: str, strategy: Merge
    ) -> None:
        declared = {field.name: field.metadata.get(MERGE_KEY) for field in fields(cls)}
        assert declared[name] is strategy

    @pytest.mark.parametrize(
        "cls", [OpenGraphImage, Article, Twitter, Alternates, Viewport, Icon, Link]
    )
    def test_a_leaf_block_declares_no_strategy(self, cls: type) -> None:
        assert all(MERGE_KEY not in field.metadata for field in fields(cls))


class TestRobotsDirectives:
    """One robots value reads as its directive names, whatever agent it names."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("noindex, nofollow", {"noindex", "nofollow"}),
            (" NONE ", {"none"}),
            ("googlebot: noindex, nofollow", {"noindex", "nofollow"}),
            ("", {""}),
        ],
        ids=["plain", "padded", "agent", "empty"],
    )
    def test_the_names(self, value: str, expected: set[str]) -> None:
        assert robots_directives(value) == expected


class TestNoindex:
    """`Metadata.noindex` reads the robots directives token by token."""

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [
            (None, False),
            (Robots(index=False), True),
            (Robots(index=True), False),
            (Robots(), False),
            (Robots(index=True, googlebot="noindex"), False),
            ("noindex, follow", True),
            ("NOINDEX, follow", True),
            ("none", True),
            (" None ", True),
            ("index, follow", False),
            ("noindexer", False),
            ("", False),
        ],
        ids=[
            "none",
            "index_false",
            "index_true",
            "unset",
            "googlebot_alone",
            "text_noindex",
            "upper_case",
            "none_token",
            "padded_none",
            "text",
            "longer_token",
            "empty",
        ],
    )
    def test_the_robots_decide(
        self, robots: Robots | str | None, *, expected: bool
    ) -> None:
        assert Metadata(robots=robots).noindex is expected
        assert robots_noindex(robots) is expected
