from collections.abc import Callable

import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy

from next.checks import reset_check_caches
from next.conf import next_framework_settings
from next.diagnostics import QUIET_PERIOD, degraded, watch_degraded
from next.pages import ld
from next.pages.errors import PageMetadataShapeError
from next.pages.metadata import Metadata, Robots, noindexed
from next.pages.metadata.markers import REFUSED_ROBOTS, Segment, TitleSpec
from next.pages.metadata.normalize import normalize_site_metadata
from next.pages.metadata.scope import (
    SITE_SOURCE,
    MetadataOptions,
    contain_site_refusal,
    forget_metadata_scope,
    metadata_options,
    site_segment,
    site_tier,
)
from tests.support import next_framework_settings_stand_in


def _live_only(request: HttpRequest | None) -> bool:
    return request is None or request.get_host() == "acme.example"


class TestMetadataOptions:
    """The upper-case options are read leniently beside the defaults."""

    def test_default_options(self) -> None:
        assert metadata_options() == MetadataOptions(canonical_query=())

    def test_options_are_read_from_the_scope(self) -> None:
        scope = {"CANONICAL_QUERY": ["page", 1, "sort"]}
        with override_settings(NEXT_FRAMEWORK={"METADATA": scope}):
            options = metadata_options()
        assert options == MetadataOptions(canonical_query=("page", "sort"))

    @pytest.mark.parametrize(
        "scope",
        [{"CANONICAL_QUERY": "page"}, {"CANONICAL_QUERY": 1}],
        ids=["query_is_a_string", "query_is_an_int"],
    )
    def test_unusable_values_fall_back(self, scope: dict[str, object]) -> None:
        with override_settings(NEXT_FRAMEWORK={"METADATA": scope}):
            assert metadata_options() == MetadataOptions()


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
        meta = segment.metadata
        assert meta.site_name is site_name
        assert isinstance(meta.site_name, Promise)
        assert meta.robots is not None
        assert meta.robots.index is True

    def test_typed_nodes_survive_the_frozen_settings(self) -> None:
        organization = ld.Node(
            id="#org", type="Organization", extra={"foundingDate": "2020"}
        )
        item = ld.ListItem(name="Acme", position=1, item="/")
        defaults = {"jsonld": [organization, item]}
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": defaults}}):
            segment = site_segment()
        assert segment.metadata.jsonld == (organization, item)

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
        with pytest.raises(PageMetadataShapeError, match=fragment) as excinfo:
            normalize_site_metadata(defaults, source=SITE_SOURCE)
        assert excinfo.value.source == SITE_SOURCE

    def test_a_refused_scope_folds_to_noindex_alone(self) -> None:
        defaults = {"canonical": "javascript:x", "robots": "index"}
        framework = {"SITE": {"NAME": "Acme"}, "METADATA": {"DEFAULTS": defaults}}
        with override_settings(NEXT_FRAMEWORK=framework):
            first = site_tier()
            again = site_segment()
        assert first.segment == Segment(
            SITE_SOURCE, Metadata(robots=REFUSED_ROBOTS, site_name="Acme")
        )
        assert again is first.segment
        assert isinstance(first.refusal, PageMetadataShapeError)

    def test_every_contained_read_of_a_refused_scope_degrades(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = [1000.0]
        monkeypatch.setattr("next.diagnostics.monotonic", lambda: clock[0])
        defaults = {"canonical": "javascript:x"}
        with (
            override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": defaults}}),
            caplog.at_level("WARNING", logger="next.pages.metadata.scope"),
        ):
            marks = []
            for _ in range(2):
                watch_degraded()
                contain_site_refusal()
                marks.append(degraded())
            clock[0] += QUIET_PERIOD
            contain_site_refusal()
        assert marks == [True, True]
        assert [record.suppressed for record in caplog.records] == [0, 1]
        assert "renders under noindex" in caplog.records[0].message

    def test_an_accepted_scope_leaves_the_render_alone(self) -> None:
        watch_degraded()
        contain_site_refusal()
        assert not degraded()

    @pytest.mark.parametrize(
        ("django", "strict"),
        [({"DEBUG": True}, {}), ({}, {"STRICT_LOADING": True})],
        ids=["debug", "strict"],
    )
    def test_a_refused_scope_raises_when_loud(
        self, django: dict[str, object], strict: dict[str, object]
    ) -> None:
        metadata = {"DEFAULTS": {"canonical": "javascript:x"}}
        with (
            override_settings(
                NEXT_FRAMEWORK={"METADATA": metadata, **strict}, **django
            ),
            pytest.raises(PageMetadataShapeError, match="canonical"),
        ):
            site_segment()

    def test_the_site_name_falls_back_to_the_site_scope(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"SITE": {"NAME": "Acme"}}):
            assert site_segment().metadata.site_name == "Acme"
        defaults = {"site_name": "Own"}
        with override_settings(
            NEXT_FRAMEWORK={
                "SITE": {"NAME": "Acme"},
                "METADATA": {"DEFAULTS": defaults},
            }
        ):
            assert site_segment().metadata.site_name == "Own"

    def test_a_non_mapping_defaults_value_folds_to_an_empty_segment(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"METADATA": {"DEFAULTS": "x"}}):
            assert site_segment() == Segment(SITE_SOURCE)

    def test_a_non_mapping_scope_folds_to_an_empty_segment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stand_in = next_framework_settings_stand_in(METADATA="x")
        monkeypatch.setattr("next.conf.scopes.next_framework_settings", stand_in)
        assert site_segment() == Segment(SITE_SOURCE)
        assert metadata_options() == MetadataOptions()


class TestNoindexed:
    """A page stays out of the index by its robots or by the site rule."""

    @pytest.mark.parametrize(
        ("robots", "expected"),
        [(None, False), (Robots(index=False), True), ("none", True)],
        ids=["none", "robots_noindex", "text_none"],
    )
    def test_the_robots_decide_on_an_open_site(
        self, robots: Robots | str | None, *, expected: bool
    ) -> None:
        assert noindexed(Metadata(robots=robots)) is expected

    @override_settings(NEXT_FRAMEWORK={"SITE": {"INDEXABLE": False}})
    def test_a_closed_site_keeps_every_page_out(self) -> None:
        assert noindexed(Metadata(robots=Robots(index=True))) is True
        assert noindexed(Metadata()) is True

    @override_settings(
        ALLOWED_HOSTS=["*"], NEXT_FRAMEWORK={"SITE": {"INDEXABLE": _live_only}}
    )
    def test_the_request_names_the_host_the_rule_reads(self) -> None:
        factory = RequestFactory()
        preview = factory.get("/", HTTP_HOST="preview.acme.example")
        live = factory.get("/", HTTP_HOST="acme.example")
        assert noindexed(Metadata(), request=preview) is True
        assert noindexed(Metadata(), request=live) is False
        assert noindexed(Metadata()) is False


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
        assert site_tier.cache_info().currsize == 0
        assert metadata_options.cache_info().currsize == 0
