from dataclasses import fields

import pytest
from django.utils import translation
from django.utils.functional import Promise, lazy
from django.utils.translation import gettext, gettext_lazy

from next.pages.metadata import Metadata, Segment, normalize_metadata
from next.pages.metadata.merge import MERGED_FIELDS, fold_metadata
from next.pages.metadata.schema import EMPTY_METADATA
from tests.support import METADATA_MERGE_CASES, MetadataMergeCase


def _chain(*raw: object) -> list[Segment]:
    return [
        normalize_metadata(item, source=f"segment[{index}]")
        for index, item in enumerate(raw)
    ]


class TestFold:
    """The chain folds root to leaf, the nearer segment winning per field."""

    @pytest.mark.parametrize(
        "case", METADATA_MERGE_CASES, ids=[case.id for case in METADATA_MERGE_CASES]
    )
    def test_the_nearer_segment_wins_per_field(self, case: MetadataMergeCase) -> None:
        meta = fold_metadata(_chain(*case.segments_raw))
        title = None if meta.title is None else str(meta.title)
        assert title == case.expected_title
        for name, expected in case.expected_fields.items():
            assert getattr(meta, name) == expected, name

    def test_empty_chain_is_the_empty_value(self) -> None:
        assert fold_metadata(()) == EMPTY_METADATA

    def test_fold_accepts_any_iterable(self) -> None:
        meta = fold_metadata(iter(_chain({"title": "Wallet"})))
        assert meta.title == "Wallet"

    def test_merged_fields_are_every_field_but_the_title(self) -> None:
        assert set(MERGED_FIELDS) == {f.name for f in fields(Metadata)} - {"title"}


class TestLaziness:
    """A templated title leaves the fold as a `Promise` nobody has evaluated."""

    def test_templated_title_is_a_promise(self) -> None:
        meta = fold_metadata(
            _chain({"title": {"template": "{title} · Acme"}}, {"title": "Wallet"})
        )
        assert isinstance(meta.title, Promise)

    def test_fold_never_evaluates_the_promises(self) -> None:
        calls: list[str] = []

        def build(value: str) -> str:
            calls.append(value)
            return value

        template = lazy(build, str)("{title} · {site_name}")
        text = lazy(build, str)("Wallet")
        site_name = lazy(build, str)("Acme")
        meta = fold_metadata(
            _chain(
                {"title": {"template": template}, "site_name": site_name},
                {"title": text},
            )
        )
        assert calls == []
        assert str(meta.title) == "Wallet · Acme"
        assert sorted(calls) == ["Acme", "Wallet", "{title} · {site_name}"]

    def test_one_folded_value_answers_each_language(self) -> None:
        template = lazy(lambda: "{title} · " + gettext("Yes"), str)()
        meta = fold_metadata(
            _chain(
                {"title": {"template": template, "default": "Acme"}},
                {"title": gettext_lazy("No")},
            )
        )
        with translation.override("de"):
            assert str(meta.title) == "Nein · Ja"
        with translation.override("en"):
            assert str(meta.title) == "No · Yes"

    def test_site_name_decision_waits_for_the_translation(self) -> None:
        template = lazy(lambda: "{title} · {site_name}", str)()
        meta = fold_metadata(
            _chain({"title": {"template": template}}, {"title": "Wallet"})
        )
        assert isinstance(meta.title, Promise)
        assert str(meta.title) == "Wallet"
