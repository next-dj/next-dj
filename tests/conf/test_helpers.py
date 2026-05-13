import pytest
from django.core.exceptions import ImproperlyConfigured

from next.conf import extend_default_backend


class TestExtendDefaultBackend:
    """extend_default_backend returns a deep copy of defaults with overrides."""

    def test_replaces_single_top_level_key(self) -> None:
        patched = extend_default_backend(
            "DEFAULT_COMPONENT_BACKENDS",
            COMPONENTS_DIR="_widgets",
        )
        assert patched[0]["COMPONENTS_DIR"] == "_widgets"
        assert patched[0]["BACKEND"] == "next.components.FileComponentsBackend"

    def test_merges_nested_options_instead_of_replacing(self) -> None:
        patched = extend_default_backend(
            "DEFAULT_STATIC_BACKENDS",
            OPTIONS={"DEDUP_STRATEGY": "next.static.collector.HashContentDedup"},
        )
        assert patched[0]["OPTIONS"] == {
            "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
        }

    def test_preserves_unrelated_entries(self) -> None:
        patched = extend_default_backend(
            "DEFAULT_PAGE_BACKENDS",
            PAGES_DIR="routes",
        )
        assert patched[0]["APP_DIRS"] is True
        assert patched[0]["DIRS"] == []
        assert patched[0]["PAGES_DIR"] == "routes"

    def test_returns_deep_copy_not_references(self) -> None:
        a = extend_default_backend("DEFAULT_PAGE_BACKENDS", PAGES_DIR="routes")
        b = extend_default_backend("DEFAULT_PAGE_BACKENDS", PAGES_DIR="screens")
        assert a[0]["PAGES_DIR"] == "routes"
        assert b[0]["PAGES_DIR"] == "screens"
        assert a[0] is not b[0]

    def test_raises_for_unknown_key(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="Unknown backend list"):
            extend_default_backend("URL_NAME_TEMPLATE")

    def test_raises_for_out_of_range_index(self) -> None:
        with pytest.raises(IndexError, match="out of range"):
            extend_default_backend("DEFAULT_PAGE_BACKENDS", index=7, PAGES_DIR="r")

    def test_raises_for_negative_index(self) -> None:
        with pytest.raises(IndexError, match="out of range"):
            extend_default_backend("DEFAULT_PAGE_BACKENDS", index=-1, PAGES_DIR="r")
