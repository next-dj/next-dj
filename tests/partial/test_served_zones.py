from pathlib import Path

import pytest

from next.intercept import Intercept, intercepts
from next.management.endpoints import served_zones
from next.partial.intercept import pick_intercept
from next.partial.registry import (
    own_standalone_zones,
    own_zones,
    target_zones,
    zones_served,
)
from next.urls.routes import collect_routes, with_intercepts
from tests.support import routed, write_page
from tests.support.access import COMPONENTS, PAGE_HEAD
from tests.support.cases.intercepts import zone_body
from tests.support.checks import write_files


pytestmark = pytest.mark.usefixtures("form_registries")

_SHELL = '<aside>{% zone "sidebar" %}side{% endzone %}</aside>{% template %}'
_BROKEN = '{% zone "peek" %}{% bogus %}{% endzone %}'


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _rule(target: Path, file: Path) -> Intercept:
    return Intercept(target, file, "(.)")


class TestPickSkipsBrokenRules:
    """A rule whose template does not compose is passed over, never a 500."""

    def test_a_later_healthy_rule_serves(self, root: Path) -> None:
        target = write_page(root, "photo", PAGE_HEAD, body="<p>x</p>")
        broken = write_page(root, "a", PAGE_HEAD, body=_BROKEN)
        healthy = write_page(root, "b", PAGE_HEAD, body=zone_body("peek"))
        intercepts.publish(root, [_rule(target, broken), _rule(target, healthy)])

        picked = pick_intercept(target, ("peek",))

        assert picked is not None
        assert picked.file == healthy

    def test_an_unknown_zone_picks_nothing(self, root: Path) -> None:
        target = write_page(root, "photo", PAGE_HEAD, body="<p>x</p>")
        broken = write_page(root, "a", PAGE_HEAD, body=_BROKEN)
        intercepts.publish(root, [_rule(target, broken)])

        assert pick_intercept(target, ("ghost",)) is None

    def test_a_broken_target_keeps_nothing_from_the_rule(self, root: Path) -> None:
        target = write_page(root, "photo", PAGE_HEAD, body=_BROKEN)
        rule = write_page(root, "a", PAGE_HEAD, body=zone_body("peek"))
        intercepts.publish(root, [_rule(target, rule)])

        picked = pick_intercept(target, ("peek",))

        assert picked is not None
        assert picked.file == rule

    def test_the_zone_of_the_target_is_not_served(self, root: Path) -> None:
        target = write_page(root, "photo", PAGE_HEAD, body=zone_body("peek"))
        rule = write_page(root, "a", PAGE_HEAD, body=zone_body("peek", "more"))
        intercepts.publish(root, [_rule(target, rule)])

        assert pick_intercept(target, ("peek",)) is None
        assert pick_intercept(target, ("peek", "more")) is None
        assert pick_intercept(target, ("more",)) is not None


class TestZonesServed:
    """The zones an intercept serves leave out its layouts and its target."""

    def test_layout_and_target_zones_are_subtracted(self, root: Path) -> None:
        write_files(root, {"layout.djx": _SHELL})
        target = write_page(root, "photo", PAGE_HEAD, body=zone_body("shared"))
        rule = write_page(
            root, "(.)photo", PAGE_HEAD, body=zone_body("shared", "peek", "tail")
        )

        assert own_standalone_zones(rule) == ("shared", "peek", "tail")
        assert zones_served(rule, target) == ("peek", "tail")

    def test_a_layout_that_fails_to_compose_has_no_zones(self, root: Path) -> None:
        write_files(root, {"layout.djx": "{% bogus %}{% template %}"})
        rule = write_page(root, "photo", PAGE_HEAD, body=zone_body("peek"))

        assert own_zones(rule) is None
        assert own_standalone_zones(rule) == ()

    def test_a_body_less_page_under_a_layout_serves_nothing(self, root: Path) -> None:
        write_files(root, {"layout.djx": _SHELL})
        target = write_page(root, "photo", PAGE_HEAD, body="<p>x</p>")
        carrier = write_page(root, "(.)photo", PAGE_HEAD)

        assert zones_served(carrier, target) == ()

    def test_a_read_layout_is_reused(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_files(root, {"layout.djx": _SHELL})
        rule = write_page(root, "photo", PAGE_HEAD, body=zone_body("peek"))
        first = own_zones(rule)
        monkeypatch.setattr("next.partial.registry.zones.Template", None, raising=True)

        assert own_zones(rule) == first

    def test_a_target_that_fails_to_compose_declares_nothing(self, root: Path) -> None:
        target = write_page(root, "photo", PAGE_HEAD, body="{% if %}")

        assert target_zones(target) == frozenset()


class TestServedZonesListing:
    """The listing of an intercept row prints what it serves over its target."""

    def test_a_layout_zone_is_not_listed(self, root: Path) -> None:
        write_files(root, {"layout.djx": _SHELL})
        write_page(root, "feed/photo", PAGE_HEAD, body="<p>x</p>")
        write_page(root, "feed/(.)photo", PAGE_HEAD, body=zone_body("sidebar", "peek"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [_target_row, row] = [
                route
                for route in with_intercepts(collect_routes())
                if root in route.file.parents
            ]

            assert served_zones(row) == "peek"

    def test_a_body_holding_only_layout_zones_reads_a_dash(self, root: Path) -> None:
        write_files(root, {"layout.djx": _SHELL})
        write_page(root, "feed/photo", f"{PAGE_HEAD}page.public()\n", body="<p>x</p>")
        write_page(root, "feed/(.)photo", PAGE_HEAD, body=zone_body("sidebar"))

        with routed(root, COMPONENT_BACKENDS=COMPONENTS):
            [_target_row, row] = [
                route
                for route in with_intercepts(collect_routes())
                if root in route.file.parents
            ]

            assert served_zones(row) == "-"
