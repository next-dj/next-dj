from pathlib import Path

import pytest

from next.pages.registry import PageContextRegistry
from next.utils import defining_file
from tests.support import counting_provider


class TestZoneScopedContext:
    """`@context(zone=)` keeps a page callable out of a foreign zone GET."""

    @pytest.fixture()
    def registry(self) -> PageContextRegistry:
        """Return a fresh registry for each test."""
        return PageContextRegistry()

    @pytest.fixture()
    def page_path(self, tmp_path) -> Path:
        """Return the page path every provider in a test registers against."""
        return tmp_path / "page.py"

    def test_zone_tagged_provider_runs_on_a_full_render(
        self, registry, page_path
    ) -> None:
        """A full render carries no zone batch, so a tagged provider still runs."""
        calls: list[str] = []
        registry.register_context(
            page_path, "a", counting_provider(calls, "a"), zone="a"
        )

        result = registry.collect_context(page_path)

        assert calls == ["a"]
        assert result.context_data["a"] == "a-value"

    def test_zone_tagged_provider_skipped_for_a_foreign_zone(
        self, registry, page_path
    ) -> None:
        """A GET for another zone never calls the provider at all."""
        calls: list[str] = []
        registry.register_context(
            page_path, "a", counting_provider(calls, "a"), zone="a"
        )

        result = registry.collect_context(page_path, requested_zones=frozenset({"b"}))

        assert calls == []
        assert "a" not in result.context_data

    def test_zone_tagged_provider_runs_for_its_own_zone(
        self, registry, page_path
    ) -> None:
        """A GET naming the provider's zone runs it."""
        calls: list[str] = []
        registry.register_context(
            page_path, "a", counting_provider(calls, "a"), zone="a"
        )

        result = registry.collect_context(page_path, requested_zones=frozenset({"a"}))

        assert calls == ["a"]
        assert result.context_data["a"] == "a-value"

    @pytest.mark.parametrize(
        "requested_zones",
        [None, frozenset(), frozenset({"a"}), frozenset({"a", "b"})],
        ids=["full_render", "empty_batch", "single_zone", "batch"],
    )
    def test_zone_less_provider_runs_for_every_batch(
        self, registry, page_path, requested_zones
    ) -> None:
        """An untagged provider stays unconditional, an empty batch included."""
        calls: list[str] = []
        registry.register_context(page_path, "plain", counting_provider(calls, "plain"))

        result = registry.collect_context(page_path, requested_zones=requested_zones)

        assert calls == ["plain"]
        assert result.context_data["plain"] == "plain-value"

    def test_batch_runs_the_providers_of_both_zones(self, registry, page_path) -> None:
        """A two-zone batch runs the provider of each zone in it."""
        calls: list[str] = []
        registry.register_context(
            page_path, "a", counting_provider(calls, "a"), zone="a"
        )
        registry.register_context(
            page_path, "b", counting_provider(calls, "b"), zone="b"
        )
        registry.register_context(
            page_path, "c", counting_provider(calls, "c"), zone="c"
        )

        result = registry.collect_context(
            page_path, requested_zones=frozenset({"a", "b"})
        )

        assert sorted(calls) == ["a", "b"]
        assert result.context_data["a"] == "a-value"
        assert result.context_data["b"] == "b-value"
        assert "c" not in result.context_data

    def test_zone_with_inherit_context_is_rejected(self, registry, page_path) -> None:
        """An ancestor cannot claim a zone declared by a descendant template."""
        calls: list[str] = []

        with pytest.raises(ValueError, match="cannot combine"):
            registry.register_context(
                page_path,
                "a",
                counting_provider(calls, "a"),
                zone="a",
                inherit_context=True,
            )

        result = registry.collect_context(page_path)

        assert calls == []
        assert "a" not in result.context_data

    def test_decorator_binds_the_zone_it_names(self, page_instance) -> None:
        """`page.context(zone=)` carries the binding through to collection."""

        @page_instance.context("scoped", zone="a")
        def scoped() -> str:
            return "scoped-value"

        declaring_file = defining_file(scoped)
        own = page_instance.build_render_context(
            declaring_file, requested_zones=frozenset({"a"})
        )
        foreign = page_instance.build_render_context(
            declaring_file, requested_zones=frozenset({"b"})
        )

        assert own["scoped"] == "scoped-value"
        assert "scoped" not in foreign

    def test_untagged_decorator_leaves_the_callable_unbound(
        self, page_instance
    ) -> None:
        """`page.context` without `zone=` runs the callable for every batch."""

        @page_instance.context("plain")
        def plain() -> str:
            return "plain-value"

        context_data = page_instance.build_render_context(
            defining_file(plain), requested_zones=frozenset({"b"})
        )

        assert context_data["plain"] == "plain-value"


class TestRequestedZonesStaysOutOfTheContext:
    """The zone batch is a filter, never a value pages or JavaScript can read."""

    def test_collect_context_never_exposes_the_batch(self, tmp_path) -> None:
        """Neither context map of the result carries the batch as a key."""
        registry = PageContextRegistry()
        page_path = tmp_path / "page.py"
        registry.register_context(
            page_path, "a", lambda: "a-value", serialize=True, zone="a"
        )

        result = registry.collect_context(page_path, requested_zones=frozenset({"a"}))

        assert "requested_zones" not in result.context_data
        assert "requested_zones" not in result.js_context

    def test_render_context_never_exposes_the_batch(
        self, page_instance, tmp_path
    ) -> None:
        """The render context and its JS subset stay free of the batch."""
        page_path = tmp_path / "page.py"
        page_instance._context_manager.register_context(
            page_path, "a", lambda: "a-value", serialize=True, zone="a"
        )

        context_data = page_instance.build_render_context(
            page_path, requested_zones=frozenset({"a"})
        )

        assert "requested_zones" not in context_data
        assert "requested_zones" not in context_data["_next_js_context"]
