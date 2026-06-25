from pathlib import Path
from unittest.mock import patch

import pytest

import next.partial.render as render_module
from next.partial import UnknownZoneError, ZoneRenderResult, render_zone
from next.partial.signals import zone_rendered
from next.partial.zone import ZONE_ATTR
from tests.support import plain_get


PAGES_ROOT = Path(__file__).resolve().parent.parent / "site_pages"
ZONED_PAGE = PAGES_ROOT / "zoned" / "page.py"


def _request():
    """Return a plain GET request for the zoned page URL."""
    return plain_get("/zoned/")


class TestRenderZoneBatch:
    """`render_zone` renders the named zones with the full page context."""

    def test_renders_each_named_zone_standalone(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha", "beta"), _request())
        assert isinstance(result, ZoneRenderResult)
        assert result.html["alpha"] == f'<div {ZONE_ATTR}="alpha"><p>alpha hi</p></div>'
        assert (
            result.html["beta"]
            == f'<section {ZONE_ATTR}="beta"><p>beta hi</p></section>'
        )

    def test_only_requested_zones_render(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha",), _request())
        assert set(result.html) == {"alpha"}

    def test_full_page_context_reaches_zone_body(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha",), _request())
        assert "hi" in result.html["alpha"]

    def test_lazy_zone_renders_real_body_without_lazy_hint(self) -> None:
        result = render_zone(ZONED_PAGE, ("later",), _request())
        assert result.html["later"] == f'<div {ZONE_ATTR}="later"><p>later hi</p></div>'
        assert "data-next-lazy" not in result.html["later"]

    def test_context_collected_once_for_the_batch(self) -> None:
        original = render_module.page.build_render_context
        with patch.object(
            render_module.page,
            "build_render_context",
            side_effect=original,
        ) as spy:
            render_zone(ZONED_PAGE, ("alpha", "beta", "later"), _request())
        assert spy.call_count == 1


class TestRenderZoneOverrides:
    """`overrides` merges into the context the zone body reads."""

    def test_override_replaces_context_value(self) -> None:
        result = render_zone(
            ZONED_PAGE,
            ("alpha",),
            _request(),
            overrides={"greeting": "override"},
        )
        assert "override" in result.html["alpha"]


class TestRenderZoneUnknown:
    """An unknown zone name fails before any body renders."""

    def test_unknown_zone_raises(self) -> None:
        with pytest.raises(UnknownZoneError) as exc:
            render_zone(ZONED_PAGE, ("ghost",), _request())
        assert exc.value.zone_name == "ghost"

    def test_unknown_zone_in_batch_raises_before_render(self) -> None:
        with pytest.raises(UnknownZoneError):
            render_zone(ZONED_PAGE, ("alpha", "ghost"), _request())

    def test_unknown_zone_message_lists_declared_zones(self) -> None:
        with pytest.raises(UnknownZoneError) as exc:
            render_zone(ZONED_PAGE, ("ghost",), _request())
        assert "alpha" in exc.value.declared
        assert "Declared zones" in str(exc.value)

    def test_unknown_zone_without_declared_names_stays_terse(self) -> None:
        error = UnknownZoneError("ghost")
        assert error.declared == ()
        assert str(error) == 'Unknown zone "ghost".'


class TestRenderZoneCollector:
    """The collector travels outward so its assets become a manifest."""

    def test_result_carries_a_collector(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha",), _request())
        assert result.collector is not None

    def test_collector_holds_co_located_assets(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha",), _request())
        styles = result.collector.assets_in_slot("styles")
        assert [asset.url for asset in styles] == ["/static/next/zoned.css"]


class TestZoneRenderedSignal:
    """`zone_rendered` fires for each rendered zone behind a receiver gate."""

    def test_signal_fires_per_zone(self) -> None:
        seen: list[dict[str, object]] = []

        def receiver(sender: object, **kwargs: object) -> None:
            seen.append({"sender": sender, **kwargs})

        zone_rendered.connect(receiver)
        try:
            render_zone(ZONED_PAGE, ("alpha", "beta"), _request())
        finally:
            zone_rendered.disconnect(receiver)

        names = sorted(str(entry["zone_name"]) for entry in seen)
        assert names == ["alpha", "beta"]
        assert seen[0]["page_path"] == ZONED_PAGE
        assert "duration_ms" in seen[0]

    def test_quiet_without_receivers(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha",), _request())
        assert result.html
