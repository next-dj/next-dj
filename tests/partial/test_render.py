from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

import next.partial.render as render_module
from next.partial import UnknownZoneError, ZoneRenderResult, render_zone
from next.partial.signals import zone_rendered
from next.partial.zone import POLL_ATTR, ZONE_ATTR
from tests.support import counting_provider, plain_get


PAGES_ROOT = Path(__file__).resolve().parent.parent / "site_pages"
ZONED_PAGE = PAGES_ROOT / "zoned" / "page.py"


def _request():
    """Return a plain GET request for the zoned page URL."""
    return plain_get("/zoned/")


@pytest.fixture()
def scoped_zone_page(tmp_path) -> Iterator[tuple[Path, list[str]]]:
    """Build a two-zone page whose providers record their own calls.

    The providers register against the module-level page the renderer
    drives, so the registration is dropped again on teardown.
    """
    page_file = tmp_path / "page.py"
    page_file.write_text("x = 1\n")
    (tmp_path / "template.djx").write_text(
        '{% zone "left" %}<p>{{ left }}</p>{% endzone %}'
        '{% zone "right" %}<p>{{ right }}</p>{% endzone %}'
    )
    calls: list[str] = []
    registry = render_module.page._context_manager
    registry.register_context(
        page_file, "left", counting_provider(calls, "left"), zone="left"
    )
    registry.register_context(
        page_file, "right", counting_provider(calls, "right"), zone="right"
    )
    registry.register_context(page_file, "plain", counting_provider(calls, "plain"))
    yield page_file, calls
    registry._context_registry.pop(page_file, None)


@pytest.fixture()
def nested_zone_page(tmp_path) -> Iterator[tuple[Path, list[str]]]:
    """Build a page whose outer zone body holds two levels of nested zones."""
    page_file = tmp_path / "page.py"
    page_file.write_text("x = 1\n")
    (tmp_path / "template.djx").write_text(
        '{% zone "outer" %}<p>{{ outer }}</p>'
        '{% zone "inner" %}<b>{{ inner }}</b>'
        '{% zone "deep" %}<i>{{ deep }}</i>{% endzone %}'
        "{% endzone %}{% endzone %}"
        '{% zone "away" %}<p>{{ away }}</p>{% endzone %}'
    )
    calls: list[str] = []
    registry = render_module.page._context_manager
    for name in ("outer", "inner", "deep", "away"):
        registry.register_context(
            page_file, name, counting_provider(calls, name), zone=name
        )
    registry.register_context(page_file, "plain", counting_provider(calls, "plain"))
    yield page_file, calls
    registry._context_registry.pop(page_file, None)


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

    def test_poll_zone_delivery_keeps_the_poll_interval(self) -> None:
        result = render_zone(ZONED_PAGE, ("ticker",), _request())
        assert result.html["ticker"] == (
            f'<div {ZONE_ATTR}="ticker" {POLL_ATTR}="5000"><p>tick hi</p></div>'
        )

    def test_context_collected_once_for_the_batch(self) -> None:
        original = render_module.page.build_render_context
        with patch.object(
            render_module.page, "build_render_context", side_effect=original
        ) as spy:
            render_zone(ZONED_PAGE, ("alpha", "beta", "later"), _request())
        assert spy.call_count == 1


class TestRenderZoneOverrides:
    """`overrides` merges into the context the zone body reads."""

    def test_override_replaces_context_value(self) -> None:
        result = render_zone(
            ZONED_PAGE, ("alpha",), _request(), overrides={"greeting": "override"}
        )
        assert "override" in result.html["alpha"]


class TestRenderZoneUnknown:
    """A lone unknown zone raises, but a stale name in a batch is skipped."""

    def test_unknown_zone_raises(self) -> None:
        with pytest.raises(UnknownZoneError) as exc:
            render_zone(ZONED_PAGE, ("ghost",), _request())
        assert exc.value.zone_name == "ghost"

    def test_unknown_zone_in_batch_is_skipped(self) -> None:
        result = render_zone(ZONED_PAGE, ("alpha", "ghost"), _request())
        assert set(result.html) == {"alpha"}
        assert set(result.bodies) == {"alpha"}

    def test_empty_batch_renders_nothing(self) -> None:
        result = render_zone(ZONED_PAGE, (), _request())
        assert result.html == {}
        assert result.bodies == {}

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

        def receiver(sender: object, **kwargs) -> None:
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


class TestRenderZoneScopedContext:
    """A zone GET runs the providers of its own zones plus the zone-less ones."""

    def test_foreign_zone_provider_is_never_called(self, scoped_zone_page) -> None:
        page_file, calls = scoped_zone_page
        result = render_zone(page_file, ("left",), _request())
        assert sorted(calls) == ["left", "plain"]
        assert "left-value" in result.html["left"]

    def test_batch_calls_the_providers_of_every_named_zone(
        self, scoped_zone_page
    ) -> None:
        page_file, calls = scoped_zone_page
        result = render_zone(page_file, ("left", "right"), _request())
        assert sorted(calls) == ["left", "plain", "right"]
        assert "right-value" in result.html["right"]


class TestRenderZoneNestedContext:
    """A zone GET also runs the providers of the zones nested in its body."""

    def test_nested_provider_runs_and_reaches_the_html(self, nested_zone_page) -> None:
        page_file, calls = nested_zone_page
        result = render_zone(page_file, ("outer",), _request())
        assert "inner" in calls
        assert "inner-value" in result.html["outer"]

    def test_two_levels_deep_provider_runs(self, nested_zone_page) -> None:
        page_file, calls = nested_zone_page
        result = render_zone(page_file, ("outer",), _request())
        assert sorted(calls) == ["deep", "inner", "outer", "plain"]
        assert "deep-value" in result.html["outer"]

    def test_sibling_zone_provider_stays_out(self, nested_zone_page) -> None:
        page_file, calls = nested_zone_page
        render_zone(page_file, ("inner",), _request())
        assert sorted(calls) == ["deep", "inner", "plain"]

    def test_leaf_zone_batch_runs_no_nested_provider(self, nested_zone_page) -> None:
        page_file, calls = nested_zone_page
        render_zone(page_file, ("away",), _request())
        assert sorted(calls) == ["away", "plain"]

    def test_batch_of_nesting_zones_widens_once(self, nested_zone_page) -> None:
        page_file, calls = nested_zone_page
        result = render_zone(page_file, ("outer", "inner"), _request())
        assert sorted(calls) == ["deep", "inner", "outer", "plain"]
        assert "deep-value" in result.html["inner"]
