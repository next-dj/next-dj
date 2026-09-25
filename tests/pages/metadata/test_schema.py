from dataclasses import FrozenInstanceError, fields

import pytest
from django.test import override_settings

from next.pages import PageMetadataShapeError
from next.pages.metadata import Metadata, Segment
from next.pages.metadata.schema import (
    EMPTY_METADATA,
    Alternates,
    AlternatesDict,
    Article,
    ArticleDict,
    OpenGraph,
    OpenGraphDict,
    OpenGraphImage,
    OpenGraphImageDict,
    Robots,
    RobotsDict,
    Twitter,
    TwitterDict,
    Verification,
    VerificationDict,
)
from tests.support import SCHEMA_PARITY_CASES, SchemaParityCase


SOURCE = "pages/wallet/page.py"


def _declared_keys(typed_dict: type) -> frozenset[str]:
    return typed_dict.__required_keys__ | typed_dict.__optional_keys__


class TestValueObjects:
    """The folded value is immutable and its schema mirrors the input contract."""

    def test_metadata_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            EMPTY_METADATA.title = "x"  # type: ignore[misc]

    def test_segment_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            Segment(SOURCE).title = None  # type: ignore[misc]

    def test_empty_metadata_is_the_default_value(self) -> None:
        assert Metadata() == EMPTY_METADATA

    def test_segment_mirrors_metadata_plus_its_source(self) -> None:
        segment_fields = {field.name for field in fields(Segment)}
        metadata_fields = {field.name for field in fields(Metadata)}
        assert segment_fields == metadata_fields | {"source"}

    @pytest.mark.parametrize(
        ("typed_dict", "result"),
        [
            (RobotsDict, Robots),
            (OpenGraphImageDict, OpenGraphImage),
            (ArticleDict, Article),
            (OpenGraphDict, OpenGraph),
            (TwitterDict, Twitter),
            (AlternatesDict, Alternates),
            (VerificationDict, Verification),
        ],
        ids=[
            "robots",
            "og_image",
            "article",
            "og",
            "twitter",
            "alternates",
            "verification",
        ],
    )
    def test_result_dataclass_carries_every_input_key(self, typed_dict, result) -> None:
        assert set(typed_dict.__annotations__) == {f.name for f in fields(result)}


@pytest.mark.parametrize(
    "case", SCHEMA_PARITY_CASES, ids=[case.id for case in SCHEMA_PARITY_CASES]
)
class TestSchemaParity:
    """Each metadata ``TypedDict`` names exactly the keys its normaliser accepts."""

    def test_every_declared_key_is_accepted(self, case: SchemaParityCase) -> None:
        for key in _declared_keys(case.typed_dict):
            case.normalize(case.nest({key: None}), source=SOURCE)

    def test_an_unknown_key_is_refused_naming_the_declared_ones(
        self, case: SchemaParityCase
    ) -> None:
        with pytest.raises(PageMetadataShapeError) as caught:
            case.normalize(case.nest({"bogus": 1}), source=SOURCE)
        declared = ", ".join(sorted(_declared_keys(case.typed_dict)))
        assert caught.value.detail.endswith(f"expected one of {declared}")


class TestNoindex:
    """`Metadata.noindex` reads the robots directives alone."""

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [
            (None, False),
            (Robots(index=False), True),
            (Robots(index=True), False),
            (Robots(), False),
            ("noindex, follow", True),
            ("index, follow", False),
        ],
        ids=["none", "index_false", "index_true", "unset", "text_noindex", "text"],
    )
    def test_the_robots_decide(
        self, robots: Robots | str | None, *, expected: bool
    ) -> None:
        assert Metadata(robots=robots).noindex is expected

    @override_settings(NEXT_FRAMEWORK={"METADATA": {"NOINDEX": True}})
    def test_the_option_leaves_the_property_alone(self) -> None:
        assert Metadata(robots=Robots(index=True)).noindex is False
        assert EMPTY_METADATA.noindex is False
