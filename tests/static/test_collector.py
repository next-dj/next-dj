from __future__ import annotations

from pathlib import Path

import pytest

from next.static import StaticAsset, StaticCollector
from next.static.collector import (
    HEAD_CLOSE,
    SCRIPTS_PLACEHOLDER,
    STYLES_PLACEHOLDER,
    DeepMergePolicy,
    FirstWinsPolicy,
    HashContentDedup,
    IdentityDedup,
    LastWinsPolicy,
    PlaceholderRegistry,
    PlaceholderSlot,
    RaiseOnConflictPolicy,
    UrlDedup,
    default_placeholders,
)


CSS_URL = "https://example.com/a.css"
JS_URL = "https://example.com/a.js"


class TestStaticCollectorOrdering:
    """Collector keeps CSS and JS in distinct buckets and preserves insertion order."""

    def test_css_and_js_go_to_separate_lists(self, collector: StaticCollector) -> None:
        css = StaticAsset(url=CSS_URL, kind="css")
        js = StaticAsset(url=JS_URL, kind="js")
        collector.add(css)
        collector.add(js)
        assert collector.styles() == [css]
        assert collector.scripts() == [js]

    def test_preserves_insertion_order(self, collector: StaticCollector) -> None:
        urls = [f"https://cdn/{i}.css" for i in range(3)]
        for url in urls:
            collector.add(StaticAsset(url=url, kind="css"))
        assert [asset.url for asset in collector.styles()] == urls

    def test_accessors_return_same_list(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert collector.styles() is collector.styles()


class TestStaticCollectorDedup:
    """Default UrlDedup dedups by URL (or by inline body for block assets)."""

    def test_duplicate_url_ignored(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert len(collector.styles()) == 1

    def test_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="js"))
        assert [a.url for a in collector.styles()] == [CSS_URL]
        assert [a.url for a in collector.scripts()] == [CSS_URL]

    def test_dedup_survives_source_path_variation(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css", source_path=Path("/a.css")))
        assert len(collector.styles()) == 1

    def test_unknown_kind_dropped_with_debug_log(
        self,
        collector: StaticCollector,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("DEBUG", logger="next.static.collector"):
            collector.add(StaticAsset(url="weird://x", kind="weird"))
        assert collector.styles() == []
        assert collector.scripts() == []
        assert any("unknown kind" in rec.message for rec in caplog.records)


class TestStaticCollectorPrepend:
    """prepend=True inserts URL-form assets before previously appended ones."""

    def test_prepend_moves_asset_to_front(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == ["/dep.css", "/own.css"]

    def test_prepend_preserves_order_across_calls(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        for url in ("/a.css", "/b.css", "/c.css"):
            collector.add(StaticAsset(url=url, kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == [
            "/a.css",
            "/b.css",
            "/c.css",
            "/own.css",
        ]

    def test_prepend_cursor_independent_per_kind(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/own.js", kind="js"))
        collector.add(StaticAsset(url="/a.js", kind="js"), prepend=True)
        collector.add(StaticAsset(url="/b.js", kind="js"), prepend=True)
        assert [a.url for a in collector.scripts()] == [
            "/a.js",
            "/b.js",
            "/own.js",
        ]

    def test_prepend_respects_dedup(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep2.css", kind="css"), prepend=True)
        assert [a.url for a in collector.styles()] == [
            "/dep.css",
            "/dep2.css",
            "/own.css",
        ]


class TestStaticCollectorInline:
    """Inline assets always append and dedup by body."""

    @pytest.mark.parametrize(
        ("kind", "body", "accessor"),
        [("js", "console.log(1)", "scripts"), ("css", "body{color:red}", "styles")],
    )
    def test_inline_asset_lands_in_bucket(
        self,
        collector: StaticCollector,
        kind: str,
        body: str,
        accessor: str,
    ) -> None:
        collector.add(StaticAsset(url="", kind=kind, inline=body))
        items = getattr(collector, accessor)()
        assert len(items) == 1
        assert items[0].inline == body

    def test_identical_inline_bodies_dedupe(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="", kind="js", inline="x()"))
        collector.add(StaticAsset(url="", kind="js", inline="x()"))
        assert len(collector.scripts()) == 1

    def test_different_inline_bodies_kept_distinct(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="", kind="js", inline="one()"))
        collector.add(StaticAsset(url="", kind="js", inline="two()"))
        assert [a.inline for a in collector.scripts()] == ["one()", "two()"]

    def test_inline_asset_ignores_prepend(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=JS_URL, kind="js"), prepend=True)
        collector.add(StaticAsset(url="", kind="js", inline="inline()"), prepend=True)
        scripts = collector.scripts()
        assert scripts[0].url == JS_URL
        assert scripts[-1].inline == "inline()"

    def test_inline_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="", kind="css", inline="same"))
        collector.add(StaticAsset(url="", kind="js", inline="same"))
        assert [a.inline for a in collector.styles()] == ["same"]
        assert [a.inline for a in collector.scripts()] == ["same"]


class TestUrlDedup:
    """UrlDedup returns stable tuple keys that partition inline vs URL."""

    def test_url_key(self) -> None:
        dedup = UrlDedup()
        key = dedup.key(StaticAsset(url="/x.css", kind="css"))
        assert key == ("url", "css", "/x.css")

    def test_inline_key(self) -> None:
        dedup = UrlDedup()
        key = dedup.key(StaticAsset(url="", kind="js", inline="x()"))
        assert key == ("inline", "js", "x()")


class TestHashContentDedup:
    """HashContentDedup dedups by sha256 of source file bytes."""

    def test_same_content_dedupes(self, tmp_path: Path) -> None:
        a = tmp_path / "a.css"
        b = tmp_path / "b.css"
        a.write_text(".x {}")
        b.write_text(".x {}")
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        collector.add(StaticAsset(url="/a.css", kind="css", source_path=a))
        collector.add(StaticAsset(url="/b.css", kind="css", source_path=b))
        assert len(collector.styles()) == 1

    def test_different_content_kept(self, tmp_path: Path) -> None:
        a = tmp_path / "a.css"
        b = tmp_path / "b.css"
        a.write_text(".one {}")
        b.write_text(".two {}")
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        collector.add(StaticAsset(url="/a.css", kind="css", source_path=a))
        collector.add(StaticAsset(url="/b.css", kind="css", source_path=b))
        assert len(collector.styles()) == 2

    def test_fallback_to_url_when_no_source(self) -> None:
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        collector.add(StaticAsset(url="/a.css", kind="css"))
        collector.add(StaticAsset(url="/a.css", kind="css"))
        assert len(collector.styles()) == 1


class TestIdentityDedup:
    """IdentityDedup keeps every registration — no deduplication."""

    def test_duplicates_are_kept(self) -> None:
        collector = StaticCollector(dedup=IdentityDedup())
        collector.add(StaticAsset(url="/a.css", kind="css"))
        collector.add(StaticAsset(url="/a.css", kind="css"))
        assert len(collector.styles()) == 2


class TestFirstWinsPolicy:
    """FirstWinsPolicy preserves earliest registration for each JS context key."""

    def test_first_write_wins(self) -> None:
        policy = FirstWinsPolicy()
        existing: dict[str, object] = {}
        policy.merge(existing, "k", "first")
        policy.merge(existing, "k", "second")
        assert existing["k"] == "first"


class TestLastWinsPolicy:
    def test_last_write_wins(self) -> None:
        policy = LastWinsPolicy()
        existing: dict[str, object] = {}
        policy.merge(existing, "k", "first")
        policy.merge(existing, "k", "second")
        assert existing["k"] == "second"


class TestRaiseOnConflictPolicy:
    def test_raises_on_duplicate(self) -> None:
        policy = RaiseOnConflictPolicy()
        existing: dict[str, object] = {}
        policy.merge(existing, "k", "first")
        with pytest.raises(KeyError, match="Duplicate JS context key"):
            policy.merge(existing, "k", "second")


class TestDeepMergePolicy:
    def test_deep_merges_dicts(self) -> None:
        policy = DeepMergePolicy()
        existing: dict[str, object] = {}
        policy.merge(existing, "cfg", {"a": 1, "nested": {"x": 1}})
        policy.merge(existing, "cfg", {"b": 2, "nested": {"y": 2}})
        assert existing["cfg"] == {"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}

    def test_scalar_overrides_previous(self) -> None:
        policy = DeepMergePolicy()
        existing: dict[str, object] = {}
        policy.merge(existing, "k", "v1")
        policy.merge(existing, "k", "v2")
        assert existing["k"] == "v2"

    def test_dict_overrides_scalar(self) -> None:
        policy = DeepMergePolicy()
        existing: dict[str, object] = {"k": "scalar"}
        policy.merge(existing, "k", {"x": 1})
        assert existing["k"] == {"x": 1}


class TestJsContextPolicyIntegration:
    """Collector.add_js_context routes through the configured policy."""

    def test_default_first_wins(self, collector: StaticCollector) -> None:
        collector.add_js_context("k", "first")
        collector.add_js_context("k", "second")
        assert collector.js_context()["k"] == "first"

    def test_custom_policy_applies(self) -> None:
        collector = StaticCollector(js_context_policy=LastWinsPolicy())
        collector.add_js_context("k", "first")
        collector.add_js_context("k", "second")
        assert collector.js_context()["k"] == "second"


class TestPlaceholderRegistry:
    """PlaceholderRegistry stores slot metadata keyed by slot name."""

    def test_register_and_get(self) -> None:
        reg = PlaceholderRegistry()
        slot = PlaceholderSlot(
            name="meta",
            token="<!-- next:meta -->",
            bucket="_meta",
            renderer="render_meta_tag",
        )
        reg.register(slot)
        assert reg.get("meta") is slot
        assert reg.get("missing") is None

    def test_iteration(self) -> None:
        reg = PlaceholderRegistry()
        slot = PlaceholderSlot(name="a", token="<!--a-->", bucket="_a", renderer="r")
        reg.register(slot)
        assert list(reg) == [slot]

    def test_len(self) -> None:
        reg = PlaceholderRegistry()
        assert len(reg) == 0
        reg.register(
            PlaceholderSlot(name="a", token="<!--a-->", bucket="_a", renderer="r")
        )
        assert len(reg) == 1


class TestDefaultPlaceholders:
    """The default registry ships with styles and scripts slots."""

    def test_styles_slot(self) -> None:
        slot = default_placeholders.get("styles")
        assert slot is not None
        assert slot.token == STYLES_PLACEHOLDER
        assert slot.bucket == "_styles"
        assert slot.renderer == "render_link_tag"

    def test_scripts_slot(self) -> None:
        slot = default_placeholders.get("scripts")
        assert slot is not None
        assert slot.token == SCRIPTS_PLACEHOLDER
        assert slot.renderer == "render_script_tag"


class TestPlaceholderConstants:
    """Placeholder strings stay stable across refactors."""

    def test_styles_placeholder(self) -> None:
        assert STYLES_PLACEHOLDER == "<!-- next:styles -->"

    def test_scripts_placeholder(self) -> None:
        assert SCRIPTS_PLACEHOLDER == "<!-- next:scripts -->"

    def test_head_close(self) -> None:
        assert HEAD_CLOSE == "</head>"
