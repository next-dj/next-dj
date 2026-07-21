from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client

import next.partial.render as render_module
from next.pages.loaders import _MODULE_MEMO, _load_python_module_memo
from next.partial.headers import CONTENT_TYPE
from next.testing import NextClient, envelope_of
from tests.site_pages.counted import probe


SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
COUNTED_PAGE = SITE_PAGES / "counted" / "page.py"


@pytest.fixture(autouse=True)
def counted_page():
    """Re-register the counted page and zero its counters before each test.

    Dropping the memo re-executes the module so its providers survive a
    neighbouring test clearing the registry.
    """
    _MODULE_MEMO.pop(COUNTED_PAGE, None)
    _load_python_module_memo(COUNTED_PAGE)
    probe.reset_counters()
    return probe


class TestZoneGetAssetsManifest:
    """The zone GET envelope carries the manifest of the zone-body assets."""

    def test_collected_zone_asset_url_in_manifest(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        urls = [asset["url"] for asset in envelope_of(response).assets]
        assert "/static/next/zoned.css" in urls

    def test_asset_entry_declares_kind(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        css = next(
            asset
            for asset in envelope_of(response).assets
            if asset["url"] == "/static/next/zoned.css"
        )
        assert css["kind"] == "css"


class TestEarlyRenderResponseIsVerbatim:
    """An early `render()` response is returned verbatim, never an envelope."""

    def test_redirect_status_and_location_pass_through(self) -> None:
        response = NextClient().get_zones("/redirecting/", "alpha")
        assert response.status_code == 302
        assert response["Location"] == "/login/"

    def test_early_response_is_not_an_envelope(self) -> None:
        # a non-envelope content type is the client's navigation signal, so
        # the structural reader refuses to treat it as a patch envelope
        response = NextClient().get_zones("/redirecting/", "alpha")
        assert response["Content-Type"] != CONTENT_TYPE
        with pytest.raises(AssertionError):
            envelope_of(response)

    def test_early_response_skips_zone_render(self, counted_page) -> None:
        NextClient().get_zones("/redirecting/", "alpha")
        assert counted_page.counters == {"alpha": 0, "beta": 0, "gamma": 0, "db": 0}


class TestZoneBatchOneContextCollection:
    """A multi-zone GET collects the page context exactly once."""

    def test_context_built_once_for_a_two_zone_batch(self) -> None:
        original = render_module.page.build_render_context
        with patch.object(
            render_module.page, "build_render_context", side_effect=original
        ) as spy:
            response = NextClient().get_zones("/counted/", ("alpha", "beta"))
        assert envelope_of(response).zone_targets() == ["alpha", "beta"]
        assert spy.call_count == 1

    def test_context_built_once_for_a_three_zone_batch(self) -> None:
        original = render_module.page.build_render_context
        with patch.object(
            render_module.page, "build_render_context", side_effect=original
        ) as spy:
            NextClient().get_zones("/counted/", ("alpha", "beta", "gamma"))
        assert spy.call_count == 1


class TestUnrequestedZonesDoNotRender:
    """A zone GET renders only the named zone bodies and no others.

    A failing assertion here means a change started rendering zone bodies
    the request never asked for. The counters live in the zone bodies, so
    a body that did not execute leaves its counter at zero.
    """

    def test_single_zone_renders_only_itself(self, counted_page) -> None:
        NextClient().get_zones("/counted/", "alpha")
        assert counted_page.counters["alpha"] == 1
        assert counted_page.counters["beta"] == 0
        assert counted_page.counters["gamma"] == 0

    def test_batch_renders_only_named_zones(self, counted_page) -> None:
        NextClient().get_zones("/counted/", ("alpha", "beta"))
        assert counted_page.counters["alpha"] == 1
        assert counted_page.counters["beta"] == 1
        assert counted_page.counters["gamma"] == 0

    def test_each_named_zone_renders_exactly_once(self, counted_page) -> None:
        NextClient().get_zones("/counted/", ("alpha", "gamma"))
        assert counted_page.counters["alpha"] == 1
        assert counted_page.counters["gamma"] == 1
        assert counted_page.counters["beta"] == 0


class TestMergeIntentShipsBareBody:
    """An append or prepend intent grafts the bare zone body, not the wrapper."""

    @pytest.mark.parametrize("intent", ["append", "prepend"])
    def test_merge_intent_ships_the_bare_inner_body(self, intent: str) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", HTTP_X_NEXT_MERGE=intent)
        envelope = envelope_of(response)
        assert envelope.op_verbs()[0] == intent
        assert "data-next-zone" not in envelope.html_for_zone("alpha")

    def test_morph_keeps_the_wrapped_marker_element(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        envelope = envelope_of(response)
        assert envelope.op_verbs()[0] == "morph"
        assert 'data-next-zone="alpha"' in envelope.html_for_zone("alpha")


class TestBatchSkipsUnknownZone:
    """A stale name in a batch is skipped, a lone unknown name is a 400."""

    def test_batch_renders_the_valid_zone_and_skips_the_unknown(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "ghost"))
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["alpha"]

    def test_only_unknown_zone_is_a_bad_request(self) -> None:
        response = NextClient().get_zones("/zoned/", "ghost")
        assert response.status_code == 400


class TestDuplicateZoneNamePatchedOnce:
    """A repeated zone name renders once and emits a single morph."""

    def test_duplicate_name_emits_one_morph(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "alpha"))
        assert envelope_of(response).zone_targets() == ["alpha"]

    def test_duplicate_name_renders_only_once(self, counted_page) -> None:
        NextClient().get_zones("/counted/", ("alpha", "alpha"))
        assert counted_page.counters["alpha"] == 1


class TestLazyZoneDataStaysHonest:
    """The lazy report body and its database hit stay off the full render."""

    def test_full_render_skips_the_lazy_body(self, counted_page) -> None:
        body = Client().get("/counted/").content.decode()
        assert "loading report" in body
        assert "report-rows" not in body

    def test_full_render_never_touches_the_database(self, counted_page) -> None:
        Client().get("/counted/")
        assert counted_page.counters["db"] == 0

    def test_full_render_executes_the_eager_zone_bodies(self, counted_page) -> None:
        Client().get("/counted/")
        assert counted_page.counters["alpha"] == 1
        assert counted_page.counters["beta"] == 1
        assert counted_page.counters["gamma"] == 1

    def test_requesting_the_lazy_zone_hits_the_database(self, counted_page) -> None:
        response = NextClient().get_zones("/counted/", "report")
        assert envelope_of(response).zone_targets() == ["report"]
        assert counted_page.counters["db"] == 1

    def test_requesting_other_zone_leaves_the_database_cold(self, counted_page) -> None:
        NextClient().get_zones("/counted/", "alpha")
        assert counted_page.counters["db"] == 0
