from __future__ import annotations

from django.template import Context

from next.seeding import COLLECTOR_KEY
from next.static import StaticCollector
from next.static.bundles import require_bundle
from next.static.runtime import BUNDLE_NOTE, URGENT_NOTE


URL = "/static/next/next.reload.min.js"


def _collector() -> StaticCollector:
    return StaticCollector(bundle_url=lambda name: f"/static/next/next.{name}.min.js")


class TestRequireBundle:
    """The one call a tag makes to declare the bundle its markup needs."""

    def test_a_context_records_on_its_collector(self) -> None:
        collector = _collector()
        require_bundle(Context({COLLECTOR_KEY: collector}), "reload")
        assert collector.notes(BUNDLE_NOTE) == [URL]

    def test_a_collector_records_directly(self) -> None:
        collector = _collector()
        require_bundle(collector, "reload", urgent=True)
        assert collector.notes(BUNDLE_NOTE) == [URL]
        assert collector.notes(URGENT_NOTE) == [True]

    def test_a_context_without_a_collector_records_nothing(self) -> None:
        require_bundle(Context({COLLECTOR_KEY: "not a collector"}), "reload")
        require_bundle(Context(), "more")

    def test_no_target_records_nothing(self) -> None:
        require_bundle(None, "defer")
