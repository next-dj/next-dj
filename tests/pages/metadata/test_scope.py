from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest
from django.test import override_settings
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy

from next.checks import reset_check_caches
from next.conf import next_framework_settings
from next.pages.errors import PageMetadataShapeError
from next.pages.metadata import (
    SITE_SOURCE,
    Metadata,
    Segment,
    forget_metadata_scope,
    metadata_options,
    page_noindex,
    site_segment,
)
from next.pages.metadata.schema import Robots, TitleSpec
from next.pages.metadata.scope import MetadataOptions
from tests.support import next_framework_settings_stand_in


class TestMetadataOptions:
    """The upper-case options are read leniently beside the defaults."""

    def test_default_options(self) -> None:
        assert metadata_options() == MetadataOptions(
            noindex=False, canonical_query=(), checks={}
        )

    def test_options_are_read_from_the_scope(self) -> None:
        scope = {
            "NOINDEX": True,
            "CANONICAL_QUERY": ["page", 1, "sort"],
            "CHECKS": {"TITLE_MAX": 60},
        }
        with override_settings(NEXT_FRAMEWORK={"METADATA": scope}):
            options = metadata_options()
        assert options == MetadataOptions(
            noindex=True, canonical_query=("page", "sort"), checks={"TITLE_MAX": 60}
        )

    @pytest.mark.parametrize(
        "scope",
        [{"CANONICAL_QUERY": "page"}, {"CANONICAL_QUERY": 1}, {"CHECKS": "x"}],
        ids=["query_is_a_string", "query_is_an_int", "checks_is_a_string"],
    )
    def test_unusable_values_fall_back(self, scope: dict[str, object]) -> None:
        with override_settings(NEXT_FRAMEWORK={"METADATA": scope}):
            assert metadata_options() == MetadataOptions()

    def test_options_are_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            MetadataOptions().noindex = True  # type: ignore[misc]


class TestSiteSegment:
    """The settings defaults normalise into the outermost segment of every chain."""

    def test_default_scope_is_an_empty_segment(self) -> None:
        assert site_segment() == Segment(SITE_SOURCE)

    def test_defaults_are_read_through_the_site_schema(self) -> None:
        site_name = gettext_lazy("Yes")
        defaults = {
            "title": {"template": "{title} · {site_name}", "default": "Acme"},
            "site_name": site_name,
            "robots": {"index": True},
        }
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": defaults}}):
            segment = site_segment()
        assert segment.title == TitleSpec(
            template="{title} · {site_name}", default="Acme"
        )
        assert segment.site_name is site_name
        assert isinstance(segment.site_name, Promise)
        assert segment.robots is not None
        assert segment.robots.index is True

    def test_the_segment_is_memoised_until_the_settings_reload(self) -> None:
        first = site_segment()
        assert site_segment() is first
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": {}}}):
            assert site_segment() is not first
            inside = site_segment()
        assert site_segment() is not inside

    @pytest.mark.parametrize(
        ("defaults", "fragment"),
        [
            ({"title": "Acme"}, "declares metadata key 'title' as 'str'"),
            (
                {"title": {"absolute": "Acme"}},
                "declares metadata key 'title.absolute', expected one of",
            ),
        ],
        ids=["bare_title", "absolute_title"],
    )
    def test_the_site_tier_rejects_page_only_title_forms(
        self, defaults: dict[str, object], fragment: str
    ) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": defaults}}),
            pytest.raises(PageMetadataShapeError, match=fragment) as excinfo,
        ):
            site_segment()
        assert excinfo.value.source == SITE_SOURCE

    def test_a_non_mapping_defaults_value_folds_to_an_empty_segment(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": "x"}}):
            assert site_segment() == Segment(SITE_SOURCE)

    def test_a_non_mapping_scope_folds_to_an_empty_segment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stand_in = next_framework_settings_stand_in(METADATA="x")
        monkeypatch.setattr(
            "next.pages.metadata.scope.next_framework_settings", stand_in
        )
        assert site_segment() == Segment(SITE_SOURCE)
        assert metadata_options() == MetadataOptions()


class TestPageNoindex:
    """A page stays out of the index by its robots or by the `NOINDEX` switch."""

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [(None, False), (Robots(index=False), True), ("noindex", True)],
        ids=["none", "robots_noindex", "text_noindex"],
    )
    def test_the_robots_decide_without_the_switch(
        self, robots: Robots | str | None, *, expected: bool
    ) -> None:
        assert page_noindex(Metadata(robots=robots)) is expected

    @override_settings(NEXT_FRAMEWORK={"METADATA": {"NOINDEX": True}})
    def test_the_switch_keeps_every_page_out(self) -> None:
        assert page_noindex(Metadata(robots=Robots(index=True))) is True
        assert page_noindex(Metadata()) is True


class TestForgetMetadataScope:
    """Both memos drop on an explicit call, a settings reload and a check reset."""

    @pytest.mark.parametrize(
        "reset",
        [forget_metadata_scope, next_framework_settings.reload, reset_check_caches],
        ids=["explicit", "settings_reload", "check_reset"],
    )
    def test_the_trigger_clears_both_memos(self, reset: Callable[[], object]) -> None:
        site_segment()
        metadata_options()
        reset()
        assert site_segment.cache_info().currsize == 0
        assert metadata_options.cache_info().currsize == 0
