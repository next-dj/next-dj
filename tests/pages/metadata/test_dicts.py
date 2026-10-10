from dataclasses import fields

import pytest

from next.pages import PageMetadataShapeError
from next.pages.metadata import (
    Alternates,
    AlternatesDict,
    Article,
    ArticleDict,
    Book,
    BookDict,
    Feed,
    FeedDict,
    Icon,
    Metadata,
    MetadataDict,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphAudioDict,
    OpenGraphDict,
    OpenGraphImage,
    OpenGraphImageDict,
    OpenGraphVideo,
    OpenGraphVideoDict,
    OtherIconDict,
    Profile,
    ProfileDict,
    Robots,
    RobotsDict,
    SiteMetadataDict,
    ThemeColor,
    ThemeColorDict,
    Twitter,
    TwitterDict,
    TwitterImage,
    TwitterImageDict,
    TwitterPlayer,
    TwitterPlayerDict,
    Verification,
    VerificationDict,
    Viewport,
    ViewportDict,
)
from next.pages.metadata.markers import Segment
from tests.support import SCHEMA_PARITY_CASES, SchemaParityCase


SOURCE = "pages/wallet/page.py"


def _declared_keys(typed_dict: type) -> frozenset[str]:
    return typed_dict.__required_keys__ | typed_dict.__optional_keys__


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
        (OpenGraphVideoDict, OpenGraphVideo),
        (OpenGraphAudioDict, OpenGraphAudio),
        (ProfileDict, Profile),
        (BookDict, Book),
        (TwitterImageDict, TwitterImage),
        (TwitterPlayerDict, TwitterPlayer),
        (FeedDict, Feed),
        (OtherIconDict, Icon),
        (ViewportDict, Viewport),
        (ThemeColorDict, ThemeColor),
    ],
    ids=[
        "robots",
        "og_image",
        "article",
        "og",
        "twitter",
        "alternates",
        "verification",
        "og_video",
        "og_audio",
        "profile",
        "book",
        "twitter_image",
        "twitter_player",
        "feed",
        "other_icon",
        "viewport",
        "theme_color",
    ],
)
def test_each_block_dict_names_the_fields_of_its_dataclass(
    typed_dict: type, result: type
) -> None:
    assert _declared_keys(typed_dict) == {field.name for field in fields(result)}


@pytest.mark.parametrize(
    ("typed_dict", "own"),
    [(MetadataDict, {"title", "breadcrumb"}), (SiteMetadataDict, {"title"})],
    ids=["page", "site"],
)
def test_the_page_dicts_split_into_the_metadata_and_the_segment_fields(
    typed_dict: type, own: set[str]
) -> None:
    declared = _declared_keys(typed_dict)
    folded = {field.name for field in fields(Metadata)} - {"title", "breadcrumbs"}
    assert declared & {field.name for field in fields(Segment)} == own
    assert declared - own == folded


@pytest.mark.parametrize(
    "case", SCHEMA_PARITY_CASES, ids=[case.id for case in SCHEMA_PARITY_CASES]
)
class TestSchemaParity:
    """Each metadata `TypedDict` names exactly the keys its normaliser accepts."""

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
