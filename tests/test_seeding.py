from pathlib import Path
from unittest.mock import MagicMock, patch

from next.seeding import (
    COLLECTOR_KEY,
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    seed_collector,
)
from next.static import StaticCollector
from next.static.serializers import JsonJsContextSerializer


class _LoudSerializer(JsonJsContextSerializer):
    """Serializer that marks the values it encoded, so an override is visible."""

    def dumps(self, value: object) -> str:
        """Encode `value` behind a marker naming this serializer."""
        return super().dumps({"loud": value})


class TestSeedCollector:
    """The one seed both a page render and a zone render hand their bodies."""

    def test_the_collector_is_bound_into_the_context(self, tmp_path: Path) -> None:
        context_data: dict[str, object] = {}

        collector = seed_collector(tmp_path / "page.py", context_data)

        assert context_data[COLLECTOR_KEY] is collector

    def test_the_js_context_keys_are_taken_out_of_the_context(
        self, tmp_path: Path
    ) -> None:
        context_data: dict[str, object] = {
            JS_CONTEXT_KEY: {"user": "ada"},
            JS_SERIALIZERS_KEY: {},
        }

        collector = seed_collector(tmp_path / "page.py", context_data)

        assert JS_CONTEXT_KEY not in context_data
        assert JS_SERIALIZERS_KEY not in context_data
        assert collector.js_context_wire() == {"user": "ada"}

    def test_a_keyed_serializer_encodes_its_own_value(self, tmp_path: Path) -> None:
        context_data: dict[str, object] = {
            JS_CONTEXT_KEY: {"loud": 1, "plain": 2},
            JS_SERIALIZERS_KEY: {"loud": _LoudSerializer()},
        }

        collector = seed_collector(tmp_path / "page.py", context_data)

        assert collector.js_context_wire() == {"loud": {"loud": 1}, "plain": 2}

    def test_a_context_carrying_no_js_context_seeds_an_empty_one(
        self, tmp_path: Path
    ) -> None:
        collector = seed_collector(tmp_path / "page.py", {})

        assert collector.js_context_wire() == {}

    def test_a_js_context_of_the_wrong_type_is_dropped(self, tmp_path: Path) -> None:
        """A zone render is handed whatever context its caller kept."""
        context_data: dict[str, object] = {JS_CONTEXT_KEY: "not a mapping"}

        collector = seed_collector(tmp_path / "page.py", context_data)

        assert collector.js_context_wire() == {}

    def test_serializers_of_the_wrong_type_leave_the_values_standing(
        self, tmp_path: Path
    ) -> None:
        context_data: dict[str, object] = {
            JS_CONTEXT_KEY: {"user": "ada"},
            JS_SERIALIZERS_KEY: "not a mapping",
        }

        collector = seed_collector(tmp_path / "page.py", context_data)

        assert collector.js_context_wire() == {"user": "ada"}

    def test_the_static_port_is_the_one_door(self, tmp_path: Path) -> None:
        """A rebound port serves the zone render exactly as it serves the page."""
        page_path = tmp_path / "page.py"
        assets = MagicMock()
        assets.create_collector.return_value = StaticCollector()
        slot = MagicMock()
        slot.get.return_value = assets

        with patch("next.seeding.static_assets_slot", slot):
            collector = seed_collector(page_path, {})

        assets.discover_page_assets.assert_called_once_with(page_path, collector)
