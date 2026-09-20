from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpRequest

from next.seeding import (
    ACTION_ANCHOR_KEY,
    COLLECTOR_KEY,
    EMPTY_FRAME,
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
    RenderFrame,
    ambient_frame,
    current_ambient_frame,
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


class TestRenderFrame:
    """The ambient keys a caller building its own context has to carry."""

    def test_the_anchor_lands_under_the_template_path_key(self, tmp_path: Path) -> None:
        context_data: dict[str, object] = {}
        anchor = tmp_path / "page.djx"

        RenderFrame(template_path=anchor).seed(context_data)

        assert context_data[TEMPLATE_PATH_KEY] is anchor

    def test_the_page_anchor_and_the_collector_ride_along(self, tmp_path: Path) -> None:
        context_data: dict[str, object] = {}
        collector = StaticCollector()
        frame = RenderFrame(page_module_path=tmp_path / "page.py", collector=collector)

        frame.seed(context_data)

        assert context_data[PAGE_MODULE_PATH_KEY] == tmp_path / "page.py"
        assert context_data[COLLECTOR_KEY] is collector

    def test_an_empty_frame_seeds_every_key_empty(self) -> None:
        """The frame is plain data, so an unbound one names no path of its own."""
        context_data: dict[str, object] = {}

        EMPTY_FRAME.seed(context_data)

        assert context_data[TEMPLATE_PATH_KEY] is None
        assert context_data[PAGE_MODULE_PATH_KEY] is None
        assert context_data[ACTION_ANCHOR_KEY] is None
        assert context_data[COLLECTOR_KEY] is None

    def test_the_request_is_left_to_the_render(self) -> None:
        """The render strategies stamp the request, so the seed keeps off it."""
        context_data: dict[str, object] = {}

        request = MagicMock(spec=HttpRequest)

        RenderFrame(request=request).seed(context_data)

        assert REQUEST_KEY not in context_data

    def test_the_frame_is_frozen(self, tmp_path: Path) -> None:
        frame = RenderFrame(template_path=tmp_path)

        with pytest.raises(FrozenInstanceError):
            frame.template_path = tmp_path / "other.djx"  # type: ignore[misc]

    def test_the_action_anchor_of_the_enclosing_form_rides_along(
        self, tmp_path: Path
    ) -> None:
        context_data: dict[str, object] = {}
        anchor = tmp_path / "component.py"

        RenderFrame(action_anchor=anchor).seed(context_data)

        assert context_data[ACTION_ANCHOR_KEY] is anchor


class TestAmbientFrame:
    """The frame a render publishes for the widgets a bind had no instance to reach."""

    def test_nothing_is_published_by_default(self) -> None:
        assert current_ambient_frame() is EMPTY_FRAME

    def test_the_frame_stands_for_the_block(self, tmp_path: Path) -> None:
        frame = RenderFrame(template_path=tmp_path / "page.djx")

        with ambient_frame(frame):
            assert current_ambient_frame() is frame

    def test_the_frame_around_it_comes_back(self, tmp_path: Path) -> None:
        """A nested form publishes its own frame and leaves the outer one standing."""
        outer = RenderFrame(template_path=tmp_path / "outer.djx")
        inner = RenderFrame(template_path=tmp_path / "inner.djx")

        with ambient_frame(outer):
            with ambient_frame(inner):
                assert current_ambient_frame() is inner
            assert current_ambient_frame() is outer

        assert current_ambient_frame() is EMPTY_FRAME
