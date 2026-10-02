from pathlib import Path

import pytest
from django.test import override_settings

from next.pages import ld
from next.pages.checks import check_metadata_jsonld
from next.pages.metadata.scope import SITE_SOURCE
from tests.pages.checks.metadata.trees import metadata_page, scope, templated_page
from tests.support import check_ids, patch_checks_router_manager


def _messages(tmp_path: Path, source: str) -> list[tuple[str, str]]:
    templated_page(tmp_path, source)
    with patch_checks_router_manager(pages_directory=tmp_path):
        return [(message.id, message.msg) for message in check_metadata_jsonld()]


TRAIL = """
from next.pages import ld

metadata = {"jsonld": [ld.BreadcrumbList(items=(
    ld.ListItem(name="Home", position=1, item="/"),
    ld.ListItem(name="Kit", position=2),
))]}
"""
NAIVE = """
from datetime import datetime

metadata = {"jsonld": {"@type": "Article", "datePublished": datetime(2026, 1, 2)}}
"""
AWARE_TIME = """
from datetime import UTC, time

from next.pages import ld

metadata = {"jsonld": ld.Node(type="Event", extra={"doorTime": time(9, tzinfo=UTC)})}
"""
UNSERIALISABLE = """
from next.pages import ld

metadata = {"jsonld": ld.BreadcrumbList(items=(), extra={"x": object()})}
"""
NOT_A_NUMBER = """
from next.pages import ld

metadata = {"jsonld": ld.Node(type="AggregateRating", extra={"x": float("nan")})}
"""


class TestNodes:
    """Each declared node serialises as JSON-LD."""

    def test_a_breadcrumb_list_is_silent(self, tmp_path: Path) -> None:
        assert _messages(tmp_path, TRAIL) == []

    def test_a_naive_datetime_is_silent(self, tmp_path: Path) -> None:
        """The renderer reads it in the current time zone, as it does an og time."""
        assert _messages(tmp_path, NAIVE) == []

    @pytest.mark.parametrize(
        ("source", "fragment"),
        [
            (AWARE_TIME, "a time without a time zone"),
            (UNSERIALISABLE, "does not serialise"),
            (NOT_A_NUMBER, "does not serialise"),
        ],
        ids=["aware_time", "object", "nan"],
    )
    def test_a_node_that_breaks_is_e127(
        self, tmp_path: Path, source: str, fragment: str
    ) -> None:
        messages = _messages(tmp_path, source)
        assert [code for code, _msg in messages] == ["next.E127"]
        assert fragment in messages[0][1]

    def test_the_defaults_are_checked(self) -> None:
        defaults = {"jsonld": [ld.Node(extra={"x": float("inf")})]}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_jsonld()
        assert check_ids(messages) == ["next.E127"]
        assert messages[0].msg.startswith(SITE_SOURCE)


class TestGraph:
    """One `@id` names one node type across the fold of a page."""

    def test_an_id_under_two_types_is_e101(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"jsonld": {"@id": "#org", "@type": "Organization"}}')
        metadata_page(
            tmp_path / "leaf",
            '{"jsonld": {"@id": "#site", "@type": "WebSite", '
            '"publisher": {"@id": "#org", "@type": "Person"}}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_jsonld()
        assert check_ids(messages) == ["next.E101"]
        assert "'/#org' as Organization, Person" in messages[0].msg

    def test_a_fragment_and_its_rooted_form_are_one_id(self, tmp_path: Path) -> None:
        """`#org` and `/#org` render as one `@id`, so they must share one type."""
        metadata_page(
            tmp_path,
            '{"jsonld": [{"@id": "/#org", "@type": "Organization"}, '
            '{"@type": "WebSite", "publisher": {"@id": "#org", "@type": "Person"}}]}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_jsonld()
        assert check_ids(messages) == ["next.E101"]

    def test_a_node_e127_reports_stays_out_of_the_graph(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path / "leaf",
            '{"jsonld": {"@id": "#org", "@type": "Person", "x": {1, 2}}}',
        )
        metadata_page(tmp_path, '{"jsonld": {"@id": "#org", "@type": "Organization"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_jsonld()
        assert "next.E101" not in check_ids(messages)

    def test_an_id_repeated_under_one_type_is_silent(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"jsonld": [{"@id": "#org", "@type": "Organization"}, '
            '{"@type": "WebSite", "publisher": {"@id": "#org", "@type": "Organization"}}]}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_jsonld() == []
