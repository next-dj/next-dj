from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from next.static import StaticAsset, StaticCollector
from next.static.collector import (
    _EMPTY,
    HEAD_CLOSE,
    DedupStrategy,
    DeepMergePolicy,
    FirstWinsPolicy,
    HashContentDedup,
    IdentityDedup,
    JsContextPolicy,
    LastWinsPolicy,
    PlaceholderRegistry,
    PlaceholderSlot,
    RaiseOnConflictPolicy,
    UrlDedup,
    default_placeholders,
)
from next.static.runtime import BUNDLE_NOTE, URGENT_NOTE
from next.static.serializers import JsonJsContextSerializer


CSS_URL = "https://example.com/a.css"
JS_URL = "https://example.com/a.js"


class TestStaticCollectorOrdering:
    """Collector buckets assets per slot and preserves insertion order."""

    def test_css_and_js_go_to_separate_buckets(
        self, collector: StaticCollector
    ) -> None:
        css = StaticAsset(url=CSS_URL, kind="css")
        js = StaticAsset(url=JS_URL, kind="js")
        collector.add(css)
        collector.add(js)
        assert collector.assets_in_slot("styles") == [css]
        assert collector.assets_in_slot("scripts") == [js]

    def test_preserves_insertion_order(self, collector: StaticCollector) -> None:
        urls = [f"https://cdn/{i}.css" for i in range(3)]
        for url in urls:
            collector.add(StaticAsset(url=url, kind="css"))
        assert [asset.url for asset in collector.assets_in_slot("styles")] == urls

    def test_unknown_slot_returns_an_empty_sequence(
        self, collector: StaticCollector
    ) -> None:
        assert collector.assets_in_slot("never-registered") == ()

    def test_a_missed_slot_hands_back_one_shared_immutable_answer(
        self, collector: StaticCollector
    ) -> None:
        """Nothing registered means no allocation and nothing to append to."""
        first = collector.assets_in_slot("never-registered")

        assert first is _EMPTY
        assert first is collector.assets_in_slot("also-never-registered")
        with pytest.raises(AttributeError):
            first.append(StaticAsset(url=CSS_URL, kind="css"))


class TestStaticCollectorDedup:
    """Default UrlDedup dedups by URL (or by inline body for block assets)."""

    def test_duplicate_url_ignored(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert len(collector.assets_in_slot("styles")) == 1

    def test_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="js"))
        assert [a.url for a in collector.assets_in_slot("styles")] == [CSS_URL]
        assert [a.url for a in collector.assets_in_slot("scripts")] == [CSS_URL]

    def test_dedup_survives_source_path_variation(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css", source_path=Path("/a.css")))
        assert len(collector.assets_in_slot("styles")) == 1

    def test_add_reports_whether_the_asset_landed(
        self, collector: StaticCollector
    ) -> None:
        """A caller announcing an asset announces the one the collector kept."""
        asset = StaticAsset(url=CSS_URL, kind="css")
        assert collector.add(asset) is True
        assert collector.add(asset) is False

    def test_a_prepended_duplicate_reports_no_landing_either(
        self, collector: StaticCollector
    ) -> None:
        """The dedup key decides before the bucket does, prepend or not."""
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert (
            collector.add(StaticAsset(url=CSS_URL, kind="css"), prepend=True) is False
        )

    def test_unregistered_kind_raises(self, collector: StaticCollector) -> None:
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            collector.add(StaticAsset(url="weird://x", kind="weird"))


class TestStaticCollectorPrepend:
    """prepend=True inserts URL-form assets before previously appended ones."""

    def test_prepend_moves_asset_to_front(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/dep.css",
            "/own.css",
        ]

    def test_prepend_preserves_order_across_calls(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        for url in ("/a.css", "/b.css", "/c.css"):
            collector.add(StaticAsset(url=url, kind="css"), prepend=True)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/a.css",
            "/b.css",
            "/c.css",
            "/own.css",
        ]

    def test_prepend_cursor_independent_per_slot(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/own.js", kind="js"))
        collector.add(StaticAsset(url="/a.js", kind="js"), prepend=True)
        collector.add(StaticAsset(url="/b.js", kind="js"), prepend=True)
        assert [a.url for a in collector.assets_in_slot("scripts")] == [
            "/a.js",
            "/b.js",
            "/own.js",
        ]

    def test_prepend_respects_dedup(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url="/dep2.css", kind="css"), prepend=True)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/dep.css",
            "/dep2.css",
            "/own.css",
        ]


class TestStaticCollectorInline:
    """Inline assets always append and dedup by body."""

    @pytest.mark.parametrize(
        ("kind", "body", "slot"),
        [("js", "console.log(1)", "scripts"), ("css", "body{color:red}", "styles")],
    )
    def test_inline_asset_lands_in_bucket(
        self, collector: StaticCollector, kind: str, body: str, slot: str
    ) -> None:
        collector.add(StaticAsset(url="", kind=kind, inline=body))
        items = collector.assets_in_slot(slot)
        assert len(items) == 1
        assert items[0].inline == body

    def test_identical_inline_bodies_dedupe(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="", kind="js", inline="x()"))
        collector.add(StaticAsset(url="", kind="js", inline="x()"))
        assert len(collector.assets_in_slot("scripts")) == 1

    def test_different_inline_bodies_kept_distinct(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="", kind="js", inline="one()"))
        collector.add(StaticAsset(url="", kind="js", inline="two()"))
        scripts = collector.assets_in_slot("scripts")
        assert [a.inline for a in scripts] == ["one()", "two()"]

    def test_inline_asset_ignores_prepend(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=JS_URL, kind="js"), prepend=True)
        collector.add(StaticAsset(url="", kind="js", inline="inline()"), prepend=True)
        scripts = collector.assets_in_slot("scripts")
        assert scripts[0].url == JS_URL
        assert scripts[-1].inline == "inline()"

    def test_inline_dedup_is_kind_scoped(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url="", kind="css", inline="same"))
        collector.add(StaticAsset(url="", kind="js", inline="same"))
        assert [a.inline for a in collector.assets_in_slot("styles")] == ["same"]
        assert [a.inline for a in collector.assets_in_slot("scripts")] == ["same"]


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
        assert len(collector.assets_in_slot("styles")) == 1

    def test_different_content_kept(self, tmp_path: Path) -> None:
        a = tmp_path / "a.css"
        b = tmp_path / "b.css"
        a.write_text(".one {}")
        b.write_text(".two {}")
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        collector.add(StaticAsset(url="/a.css", kind="css", source_path=a))
        collector.add(StaticAsset(url="/b.css", kind="css", source_path=b))
        assert len(collector.assets_in_slot("styles")) == 2

    def test_fallback_to_url_when_no_source(self) -> None:
        dedup = HashContentDedup()
        collector = StaticCollector(dedup=dedup)
        collector.add(StaticAsset(url="/a.css", kind="css"))
        collector.add(StaticAsset(url="/a.css", kind="css"))
        assert len(collector.assets_in_slot("styles")) == 1

    def test_inline_bodies_dedupe_by_body(self) -> None:
        """An inline block carries no file, so its body is the whole key."""
        collector = StaticCollector(dedup=HashContentDedup())
        collector.add(StaticAsset(url="", kind="css", inline=".x {}"))
        collector.add(StaticAsset(url="", kind="css", inline=".x {}"))
        assert len(collector.assets_in_slot("styles")) == 1

        collector.add(StaticAsset(url="", kind="css", inline=".y {}"))
        assert len(collector.assets_in_slot("styles")) == 2


class TestIdentityDedup:
    """IdentityDedup keeps every registration and deduplicates nothing."""

    def test_duplicates_are_kept(self) -> None:
        collector = StaticCollector(dedup=IdentityDedup())
        collector.add(StaticAsset(url="/a.css", kind="css"))
        collector.add(StaticAsset(url="/a.css", kind="css"))
        assert len(collector.assets_in_slot("styles")) == 2


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

    def test_rejects_non_json_serialisable_value(
        self, collector: StaticCollector
    ) -> None:
        """Non-serialisable values raise at add time, not at inject time."""
        with pytest.raises(TypeError, match="not serialisable"):
            collector.add_js_context("k", object())
        assert "k" not in collector.js_context()


class TestJsContextSerializerOverride:
    """`add_js_context(serializer=)` routes one key through a custom encoder."""

    class _CustomSerializer:
        """Serializer that wraps the value into a sentinel envelope."""

        def __init__(self) -> None:
            self.calls: list[object] = []

        def dumps(self, value: object) -> str:
            self.calls.append(value)
            return f'{{"_custom":{value!r}}}'.replace("'", '"')

    def test_override_recorded_for_key(self, collector: StaticCollector) -> None:
        """The override is recorded under the same key in the sibling map."""
        custom = self._CustomSerializer()
        collector.add_js_context("special", "payload", serializer=custom)
        assert collector.js_context_serializers() == {"special": custom}

    def test_override_validates_with_supplied_serializer(self) -> None:
        """Validation goes through the override, not the global default."""
        custom = self._CustomSerializer()
        collector = StaticCollector()
        collector.add_js_context("k", "v", serializer=custom)
        assert custom.calls == ["v"]

    def test_override_is_per_key_only(self, collector: StaticCollector) -> None:
        """Other keys keep using the global serializer when no override is set."""
        custom = self._CustomSerializer()
        collector.add_js_context("a", 1, serializer=custom)
        collector.add_js_context("b", 2)
        assert collector.js_context_serializers() == {"a": custom}
        assert collector.js_context() == {"a": 1, "b": 2}

    def test_no_override_leaves_serializer_map_empty(
        self, collector: StaticCollector
    ) -> None:
        """When every call omits `serializer=`, the override map stays empty."""
        collector.add_js_context("a", 1)
        collector.add_js_context("b", 2)
        assert collector.js_context_serializers() == {}


class TestJsContextWire:
    """`js_context_wire` reuses the cached fragment and reflects the merge."""

    def test_single_add_serves_wire_from_cache(
        self, collector: StaticCollector
    ) -> None:
        collector.add_js_context("k", {"x": 1})
        assert collector.js_context_wire() == {"k": {"x": 1}}

    def test_wire_matches_direct_serialisation(
        self, collector: StaticCollector
    ) -> None:
        serializer = collector._get_js_serializer()
        collector.add_js_context("a", [1, 2, 3])
        encoded = serializer.dumps([1, 2, 3])
        assert collector.js_context_wire() == {"a": json.loads(encoded)}

    def test_wire_keeps_html_delimiters_raw(self, collector: StaticCollector) -> None:
        collector.add_js_context("k", "</script><b>pwn")
        assert collector.js_context_wire() == {"k": "</script><b>pwn"}

    def test_cache_holds_the_validated_fragment(
        self, collector: StaticCollector
    ) -> None:
        collector.add_js_context("k", {"x": 1})
        assert collector._js_context_encoded["k"] == '{"x":1}'

    @pytest.mark.parametrize(
        ("policy", "first", "second", "expected"),
        [
            (FirstWinsPolicy(), {"a": 1}, {"a": 2}, {"a": 1}),
            (DeepMergePolicy(), {"a": 1}, {"b": 2}, {"a": 1, "b": 2}),
        ],
        ids=["first_wins", "deep_merge"],
    )
    def test_second_write_pops_cache_and_wire_reflects_merge(
        self,
        policy: object,
        first: dict[str, int],
        second: dict[str, int],
        expected: dict[str, int],
    ) -> None:
        collector = StaticCollector(js_context_policy=policy)
        collector.add_js_context("k", first)
        collector.add_js_context("k", second)
        assert "k" not in collector._js_context_encoded
        assert collector.js_context_wire() == {"k": expected}

    def test_wire_fallback_uses_recorded_override(self) -> None:
        calls: list[object] = []

        class Wrapper:
            def dumps(self, value: object) -> str:
                calls.append(value)
                return f'{{"wrapped":{value}}}'

        collector = StaticCollector(js_context_policy=FirstWinsPolicy())
        override = Wrapper()
        collector.add_js_context("k", 1, serializer=override)
        collector.add_js_context("k", 2, serializer=override)
        assert collector.js_context_wire() == {"k": {"wrapped": 1}}
        assert calls == [1, 2, 1]

    def test_non_serialisable_value_still_raises_at_add(
        self, collector: StaticCollector
    ) -> None:
        with pytest.raises(TypeError, match="not serialisable"):
            collector.add_js_context("k", object())
        assert collector.js_context_wire() == {}


class TestJsContextEncoded:
    """`js_context_encoded` serves per-key fragments from cache or re-encodes."""

    def test_serves_fragment_from_cache(self, collector: StaticCollector) -> None:
        collector.add_js_context("k", {"x": 1})
        assert collector.js_context_encoded() == {"k": '{"x":1}'}

    def test_reencodes_on_cache_miss(self) -> None:
        collector = StaticCollector(js_context_policy=FirstWinsPolicy())
        collector.add_js_context("k", {"a": 1})
        collector.add_js_context("k", {"a": 2})
        assert "k" not in collector._js_context_encoded
        assert collector.js_context_encoded() == {"k": '{"a":1}'}


class TestJsContextPayload:
    """`js_context_payload` drops a reserved key from all three mappings at once."""

    class _CustomSerializer:
        """Serializer that wraps the value into a sentinel envelope."""

        def dumps(self, value: object) -> str:
            return f'{{"_custom":{value!r}}}'.replace("'", '"')

    def test_nothing_reserved_hands_every_key_back(
        self, collector: StaticCollector
    ) -> None:
        collector.add_js_context("theme", "dark")

        payload = collector.js_context_payload()

        assert payload.values == {"theme": "dark"}
        assert payload.encoded == {"theme": '"dark"'}
        assert payload.serializers == {}

    def test_a_colliding_key_leaves_no_trace(self, collector: StaticCollector) -> None:
        """Its fragment and its serializer go with its value, not one without."""
        custom = self._CustomSerializer()
        collector.add_js_context("$csrf", "stolen", serializer=custom)
        collector.add_js_context("theme", "dark")

        payload = collector.js_context_payload(reserved={"$csrf"})

        assert payload.values == {"theme": "dark"}
        assert payload.encoded == {"theme": '"dark"'}
        assert payload.serializers == {}

    def test_a_reserved_key_nobody_registered_costs_nothing(
        self, collector: StaticCollector
    ) -> None:
        collector.add_js_context("theme", "dark")

        payload = collector.js_context_payload(reserved={"$csrf"})

        assert payload.values is collector.js_context()


class TestPlaceholderRegistry:
    """PlaceholderRegistry stores slot metadata keyed by slot name."""

    def test_register_and_get(self) -> None:
        reg = PlaceholderRegistry()
        reg.register("meta", token="<!-- next:meta -->")
        slot = reg.get("meta")
        assert isinstance(slot, PlaceholderSlot)
        assert slot.name == "meta"
        assert slot.token == "<!-- next:meta -->"
        assert reg.get("missing") is None

    def test_register_is_idempotent(self) -> None:
        reg = PlaceholderRegistry()
        reg.register("a", token="<!--a-->")
        reg.register("a", token="<!--a-->")
        assert len(reg) == 1

    def test_register_rejects_conflicting_token(self) -> None:
        reg = PlaceholderRegistry()
        reg.register("a", token="<!--a-->")
        with pytest.raises(ValueError, match="already registered"):
            reg.register("a", token="<!--changed-->")

    def test_register_rejects_empty_name(self) -> None:
        reg = PlaceholderRegistry()
        with pytest.raises(ValueError, match="Slot name"):
            reg.register("", token="<!--x-->")

    def test_register_rejects_empty_token(self) -> None:
        reg = PlaceholderRegistry()
        with pytest.raises(ValueError, match="Slot token"):
            reg.register("a", token="")

    def test_iteration(self) -> None:
        reg = PlaceholderRegistry()
        reg.register("a", token="<!--a-->")
        slots = list(reg)
        assert [s.name for s in slots] == ["a"]

    def test_len(self) -> None:
        reg = PlaceholderRegistry()
        assert len(reg) == 0
        reg.register("a", token="<!--a-->")
        assert len(reg) == 1


class TestDefaultPlaceholders:
    """Bootstrap registers the styles and scripts slots."""

    def test_styles_slot(self) -> None:
        slot = default_placeholders.get("styles")
        assert slot is not None
        assert slot.token == "<!-- next:styles -->"

    def test_scripts_slot(self) -> None:
        slot = default_placeholders.get("scripts")
        assert slot is not None
        assert slot.token == "<!-- next:scripts -->"


class TestPlaceholderConstants:
    """HEAD_CLOSE remains a module-level constant for the preload hint."""

    def test_head_close(self) -> None:
        assert HEAD_CLOSE == "</head>"


class _CopyingPolicy:
    """Merge into a fresh mapping, as a policy that never mutates in place would."""

    def merge(
        self, existing: dict[str, object], key: str, value: object
    ) -> dict[str, object]:
        return {**existing, key: value}


def _snapshot(collector: StaticCollector) -> tuple[object, ...]:
    return (
        set(collector._seen_keys),
        {slot: list(bucket) for slot, bucket in collector._buckets.items()},
        dict(collector._prepend_idx),
        copy.deepcopy(collector._js_context),
        dict(collector._js_context_encoded),
        dict(collector._js_context_serializers),
        {key: list(values) for key, values in collector._notes.items()},
    )


class _Draws:
    """A seeded linear congruential stream, enough to vary the mutation sequences."""

    def __init__(self, seed: int) -> None:
        self._state = seed

    def below(self, bound: int) -> int:
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) % 2**64
        return (self._state >> 33) % bound

    def pick[T](self, options: list[T]) -> T:
        return options[self.below(len(options))]


def _mutate(collector: StaticCollector, draws: _Draws, step: int) -> None:
    choice = draws.below(6)
    kind = draws.pick(["css", "js"])
    if choice == 0:
        collector.add(StaticAsset(url=f"/{draws.below(8)}.{kind}", kind=kind))
    elif choice == 1:
        url = f"/{draws.below(8)}.{kind}"
        collector.add(StaticAsset(url=url, kind=kind), prepend=True)
    elif choice == 2:
        inline = f"body{draws.below(4)}()"
        collector.add(StaticAsset(url="", kind=kind, inline=inline), prepend=True)
    elif choice == 3:
        collector.note(draws.pick(["a", "b"]), step)
    elif choice == 4:
        collector.add_js_context(draws.pick(["p", "q"]), {str(step % 3): step})
    else:
        serializer = draws.pick([None, JsonJsContextSerializer()])
        collector.add_js_context(draws.pick(["q", "r"]), step, serializer=serializer)


def _subtree(collector: StaticCollector, draws: _Draws, depth: int) -> None:
    for step in range(1 + draws.below(6)):
        if depth < 3 and draws.below(10) < 3:
            before = _snapshot(collector)
            mark = collector.mark()
            _subtree(collector, draws, depth + 1)
            if draws.below(2):
                collector.rollback(mark)
                assert _snapshot(collector) == before
            else:
                collector.release()
        else:
            _mutate(collector, draws, depth * 10 + step)


class TestCollectorJournal:
    """A mark journals every change so a rollback restores the collector exactly."""

    def test_no_mark_keeps_no_journal(self, collector: StaticCollector) -> None:
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.note("k", 1)
        collector.add_js_context("k", 1)
        assert collector._journal is None

    def test_a_mark_returns_the_journal_length(
        self, collector: StaticCollector
    ) -> None:
        assert collector.mark() == 0
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        assert collector.mark() == 1

    def test_an_inner_release_keeps_its_records_for_the_outer_rollback(
        self, collector: StaticCollector
    ) -> None:
        outer = collector.mark()
        collector.mark()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.release()
        assert collector._journal is not None
        assert len(collector._journal) == 1
        collector.rollback(outer)
        assert collector.assets_in_slot("styles") == ()
        assert collector._journal is None

    def test_the_last_release_drops_the_journal(
        self, collector: StaticCollector
    ) -> None:
        collector.mark()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.release()
        assert collector._journal is None
        assert [a.url for a in collector.assets_in_slot("styles")] == [CSS_URL]

    def test_a_rollback_keeps_what_came_before_the_mark(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/own.css", kind="css"))
        collector.note("k", "kept")
        mark = collector.mark()
        collector.add(StaticAsset(url="/dep.css", kind="css"), prepend=True)
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        collector.note("k", "dropped")
        collector.note("other", "dropped")
        collector.rollback(mark)
        assert [a.url for a in collector.assets_in_slot("styles")] == ["/own.css"]
        assert collector.assets_in_slot("scripts") == ()
        assert collector.notes("k") == ["kept"]
        assert collector.notes("other") == ()
        assert collector._prepend_idx == {}

    def test_a_rolled_back_asset_can_be_added_again(
        self, collector: StaticCollector
    ) -> None:
        mark = collector.mark()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.rollback(mark)
        assert collector.add(StaticAsset(url=CSS_URL, kind="css")) is True

    def test_a_rollback_restores_the_prepend_cursor(
        self, collector: StaticCollector
    ) -> None:
        collector.add(StaticAsset(url="/a.css", kind="css"), prepend=True)
        mark = collector.mark()
        collector.add(StaticAsset(url="/b.css", kind="css"), prepend=True)
        collector.rollback(mark)
        collector.add(StaticAsset(url="/c.css", kind="css"), prepend=True)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/a.css",
            "/c.css",
        ]

    def test_a_rollback_restores_an_overwritten_value(self) -> None:
        collector = StaticCollector(js_context_policy=LastWinsPolicy())
        custom = JsonJsContextSerializer()
        collector.add_js_context("k", "first", serializer=custom)
        mark = collector.mark()
        collector.add_js_context("k", "second")
        collector.add_js_context("new", 1, serializer=custom)
        collector.rollback(mark)
        assert collector.js_context() == {"k": "first"}
        assert collector.js_context_encoded() == {"k": '"first"'}
        assert collector.js_context_serializers() == {"k": custom}

    def test_a_rollback_restores_a_dropped_fragment(self) -> None:
        collector = StaticCollector(js_context_policy=DeepMergePolicy())
        collector.add_js_context("k", {"a": 1})
        mark = collector.mark()
        collector.add_js_context("k", {"b": 2})
        assert "k" not in collector._js_context_encoded
        collector.rollback(mark)
        assert collector.js_context() == {"k": {"a": 1}}
        assert collector._js_context_encoded == {"k": '{"a":1}'}

    def test_a_rollback_restores_the_mapping_a_policy_replaced(self) -> None:
        collector = StaticCollector(js_context_policy=_CopyingPolicy())
        collector.add_js_context("k", 1)
        original = collector.js_context()
        mark = collector.mark()
        collector.add_js_context("k", 2)
        assert collector.js_context() is not original
        collector.rollback(mark)
        assert collector.js_context() is original
        assert original == {"k": 1}

    def test_a_refused_merge_journals_nothing(self) -> None:
        collector = StaticCollector(js_context_policy=RaiseOnConflictPolicy())
        collector.add_js_context("k", 1)
        mark = collector.mark()
        with pytest.raises(KeyError, match="Duplicate JS context key"):
            collector.add_js_context("k", 2)
        assert collector._journal == []
        collector.rollback(mark)
        assert collector.js_context() == {"k": 1}

    def test_an_unregistered_kind_records_no_key(
        self, collector: StaticCollector
    ) -> None:
        collector.mark()
        with pytest.raises(KeyError, match="Unsupported asset kind"):
            collector.add(StaticAsset(url="weird://x", kind="weird"))
        assert collector._seen_keys == set()
        assert collector._journal == []

    def test_release_without_a_mark_raises(self, collector: StaticCollector) -> None:
        with pytest.raises(RuntimeError, match="No collector mark is open"):
            collector.release()

    def test_rollback_without_a_mark_raises(self, collector: StaticCollector) -> None:
        with pytest.raises(RuntimeError, match="No collector mark is open"):
            collector.rollback(0)

    @pytest.mark.parametrize("seed", range(60))
    @pytest.mark.parametrize(
        "policy",
        [FirstWinsPolicy(), LastWinsPolicy(), DeepMergePolicy()],
        ids=["first_wins", "last_wins", "deep_merge"],
    )
    @pytest.mark.parametrize(
        "dedup", [UrlDedup(), IdentityDedup()], ids=["url", "identity"]
    )
    def test_random_nested_marks_restore_the_state_exactly(
        self, seed: int, policy: JsContextPolicy, dedup: DedupStrategy
    ) -> None:
        draws = _Draws(seed)
        collector = StaticCollector(dedup=dedup, js_context_policy=policy)
        _subtree(collector, draws, 2)
        before = _snapshot(collector)
        mark = collector.mark()
        _subtree(collector, draws, 0)
        collector.rollback(mark)
        assert _snapshot(collector) == before
        assert collector._journal is None


def _bundle_url(name: str) -> str | None:
    return None if name == "more" else f"/static/next/next.{name}.min.js"


class TestCollectorRequire:
    """A declared bundle need is a noted `js` asset, journalled like any change."""

    def test_a_need_is_a_js_asset_noted_by_url(self) -> None:
        collector = StaticCollector(bundle_url=_bundle_url)
        collector.require("defer")
        url = "/static/next/next.defer.min.js"
        assert list(collector.assets_in_slot("scripts")) == [
            StaticAsset(url=url, kind="js")
        ]
        assert collector.notes(BUNDLE_NOTE) == [url]
        assert collector.notes(URGENT_NOTE) == ()

    def test_a_repeated_need_is_recorded_once(self) -> None:
        collector = StaticCollector(dedup=IdentityDedup(), bundle_url=_bundle_url)
        collector.require("reload", urgent=True)
        collector.require("reload", urgent=True)
        assert len(collector.assets_in_slot("scripts")) == 1
        assert collector.notes(BUNDLE_NOTE) == ["/static/next/next.reload.min.js"]
        assert collector.notes(URGENT_NOTE) == [True]

    def test_a_bundle_the_storage_lacks_records_only_the_urgency(self) -> None:
        collector = StaticCollector(bundle_url=_bundle_url)
        collector.require("more", urgent=True)
        assert collector.assets_in_slot("scripts") == ()
        assert collector.notes(BUNDLE_NOTE) == ()
        assert collector.notes(URGENT_NOTE) == [True]

    def test_a_collector_without_a_resolver_records_no_bundle(
        self, collector: StaticCollector
    ) -> None:
        collector.require("defer")
        assert collector.bundle_url is None
        assert collector.assets_in_slot("scripts") == ()
        assert collector.notes(BUNDLE_NOTE) == ()

    def test_a_chunk_listed_in_the_payload_is_refused(
        self, collector: StaticCollector
    ) -> None:
        with pytest.raises(ValueError, match="'csrf' names no declared bundle"):
            collector.require("csrf")

    def test_a_rollback_takes_the_need_and_its_urgency_back(self) -> None:
        collector = StaticCollector(bundle_url=_bundle_url)
        mark = collector.mark()
        collector.require("defer", urgent=True)
        collector.rollback(mark)
        assert collector.assets_in_slot("scripts") == ()
        assert collector.notes(BUNDLE_NOTE) == ()
        assert collector.notes(URGENT_NOTE) == ()
        collector.require("defer")
        assert collector.notes(BUNDLE_NOTE) == ["/static/next/next.defer.min.js"]

    def test_a_need_kept_outside_the_mark_survives_its_rollback(self) -> None:
        collector = StaticCollector(bundle_url=_bundle_url)
        collector.require("defer")
        mark = collector.mark()
        collector.require("defer", urgent=True)
        collector.require("reload")
        collector.rollback(mark)
        assert collector.notes(BUNDLE_NOTE) == ["/static/next/next.defer.min.js"]
        assert collector.notes(URGENT_NOTE) == ()
