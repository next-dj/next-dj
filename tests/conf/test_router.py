import re

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.compression.manager import OPT_OUT as COMPRESSION_OPT_OUT
from next.conf.compression import OPT_OUT
from next.conf.defaults import DEFAULTS
from next.conf.router import (
    SHAPE,
    Eagerness,
    ForeignMode,
    InterceptMode,
    LinkPass,
    PrefetchMode,
    PressMode,
    RouterScope,
    is_simple_selector,
    read_settings,
    router_enabled,
    router_settings,
    speculation_eagerness,
    speculation_problem,
)
from next.conf.shapes import Misfit, Section, misfits
from next.testing import override_next_settings


class TestReadSettings:
    """`read_settings` answers every key, the default for an omitted or misfit one."""

    def test_the_defaults(self) -> None:
        current = read_settings({})
        assert current.enabled is True
        assert (current.scope, current.foreign, current.intercept) == (
            "pages",
            "hard",
            "opt_in",
        )
        assert (current.prefetch, current.prefetch_delay) == ("intent", 0.065)
        assert (current.prefetch_ttl, current.prefetch_entries) == (30.0, 16)
        assert current.prefetch_shell_ttl == 300.0
        assert current.press == "mouse"
        assert (current.snapshots, current.progress_delay) == (10, 0.15)
        assert current.progress is True
        assert current.transitions is False
        assert current.speculation is None
        assert (current.cut, current.digest) == (
            "next.router.VerifiedCut",
            "next.router.ChromeDigest",
        )
        assert current.links_enabled is True
        assert current.links_pass is LinkPass.TEMPLATE
        assert current.links_containers == (
            "nav",
            "[role=navigation]",
            "[data-next-nav]",
        )
        assert current.exclude == ()

    def test_the_shapes_cover_every_default_key(self) -> None:
        current = SHAPE.keys["ACTIVE_LINKS"]
        assert isinstance(current, Section)
        assert set(SHAPE.keys) == set(DEFAULTS["ROUTER"])
        assert set(current.keys) == set(DEFAULTS["ROUTER"]["ACTIVE_LINKS"])

    def test_the_modes_read_as_their_enums(self) -> None:
        current = read_settings({"SCOPE": "opt_in", "INTERCEPT": "links"})
        assert current.scope is RouterScope.OPT_IN
        assert current.intercept is InterceptMode.LINKS
        assert current.foreign is ForeignMode.HARD
        assert current.prefetch is PrefetchMode.INTENT
        assert current.press is PressMode.MOUSE

    def test_written_values_are_read(self) -> None:
        current = read_settings(
            {
                "ENABLED": False,
                "EXCLUDE": ["/files/"],
                "PROGRESS": False,
                "PREFETCH_SHELL_TTL": 60,
                "ACTIVE_LINKS": {"ENABLED": False, "PASS": "document"},
            }
        )
        assert current.enabled is False
        assert current.progress is False
        assert current.prefetch_shell_ttl == 60.0
        assert current.exclude == ("/files/",)
        assert current.links_enabled is False
        assert current.links_pass is LinkPass.DOCUMENT
        assert current.links_containers[0] == "nav"

    @pytest.mark.parametrize("mode", ["mouse", "all", "off"])
    def test_every_press_mode_is_read(self, mode: str) -> None:
        assert read_settings({"PRESS": mode}).press == mode

    @pytest.mark.parametrize("value", ["touch", "", 1, None])
    def test_a_misfit_press_raises(self, value) -> None:
        match = re.escape("NEXT_FRAMEWORK['ROUTER']['PRESS'] must be 'mouse'")
        with pytest.raises(ImproperlyConfigured, match=match):
            read_settings({"PRESS": value})

    def test_a_pass_outside_its_modes_raises(self) -> None:
        match = re.escape(
            "NEXT_FRAMEWORK['ROUTER']['ACTIVE_LINKS']['PASS'] must be 'template' or "
            "'document'"
        )
        with pytest.raises(ImproperlyConfigured, match=match):
            read_settings({"ACTIVE_LINKS": {"PASS": "next.router.DocumentLinks"}})

    def test_a_misfit_nested_in_current_raises(self) -> None:
        match = re.escape("NEXT_FRAMEWORK['ROUTER']['ACTIVE_LINKS']['ENABLED'] must be")
        with pytest.raises(ImproperlyConfigured, match=match):
            read_settings({"ACTIVE_LINKS": {"ENABLED": 0}})

    @pytest.mark.parametrize("switch", [True, False])
    def test_a_bool_switches_the_pass(self, *, switch: bool) -> None:
        current = read_settings({"ACTIVE_LINKS": switch})
        assert current.links_enabled is switch
        assert current.links_containers[0] == "nav"

    def test_the_merged_mapping_is_read_once_per_reload(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert router_enabled() is False
            assert router_settings() is router_settings()
        with override_next_settings(ROUTER={"ENABLED": True}):
            assert router_enabled() is True


class TestMisfits:
    """Every unknown key and value outside its shape is named with its path."""

    def test_a_clean_scope(self) -> None:
        assert misfits(DEFAULTS["ROUTER"], SHAPE) == []

    def test_every_misfit(self) -> None:
        found = misfits(
            {
                "BOGUS": 1,
                "SCOPE": "some",
                "PREFETCH_DELAY": 0,
                "PROGRESS": "on",
                "PRESS": "touch",
                "PREFETCH_SHELL_TTL": -1,
                "EXCLUDE": ["files/"],
                "SPECULATION": 3,
                "ACTIVE_LINKS": {"CONTAINERS": ["nav a"], "EXTRA": True},
            },
            SHAPE,
        )
        assert (
            Misfit((), unknown=("BOGUS",), allowed=tuple(sorted(SHAPE.keys))) in found
        )
        assert Misfit(("SCOPE",), "'pages', 'all' or 'opt_in'", "'some'") in found
        assert Misfit(("PREFETCH_DELAY",), "a number of seconds above 0", "0") in found
        assert Misfit(("PROGRESS",), "True or False", "'on'") in found
        assert Misfit(("PRESS",), "'mouse', 'all' or 'off'", "'touch'") in found
        assert (
            Misfit(("PREFETCH_SHELL_TTL",), "a number of seconds above 0", "-1")
            in found
        )
        paths = {misfit.path for misfit in found}
        assert {("EXCLUDE",), ("SPECULATION",), ("ACTIVE_LINKS", "CONTAINERS")} <= paths
        assert ("ACTIVE_LINKS",) in {m.path for m in found if m.unknown == ("EXTRA",)}

    def test_a_bool_current_fits(self) -> None:
        assert misfits({"ACTIVE_LINKS": False}, SHAPE) == []
        assert misfits({"ACTIVE_LINKS": True}, SHAPE) == []

    def test_a_current_that_is_no_mapping(self) -> None:
        assert misfits({"ACTIVE_LINKS": "off"}, SHAPE) == [
            Misfit(("ACTIVE_LINKS",), "True, False or a dict", "'off'")
        ]

    @pytest.mark.parametrize(
        ("value", "simple"),
        [
            ("nav", True),
            ("my-menu", True),
            ("[role=navigation]", True),
            ("[data-next-nav]", True),
            ('[data-next-nav="main"]', True),
            ("[data-next-nav='main']", True),
            ('[data-next-nav=""]', True),
            ("[data-next-nav=1]", False),
            (".menu", False),
            ("#top", False),
            ("ul.menu#top", False),
            ("nav[role=navigation]", False),
            (" nav", False),
            ("[ role = navigation ]", False),
            ("[role~=navigation]", False),
            ("nav a", False),
            ("nav > a", False),
            ("", False),
            (3, False),
        ],
    )
    def test_simple_selectors(self, value: object, simple) -> None:
        assert is_simple_selector(value) is simple

    def test_a_bool_is_no_count(self) -> None:
        assert Misfit(("SNAPSHOTS",), "an integer from 1", "True") in misfits(
            {"SNAPSHOTS": True, "PREFETCH_TTL": True}, SHAPE
        )


class TestSpeculation:
    """`SPECULATION` names an eagerness as a string or under `eagerness`."""

    @pytest.mark.parametrize(
        ("value", "eagerness"),
        [
            (None, None),
            (3, None),
            ("moderate", "moderate"),
            ("prerender", None),
            ({"eagerness": "eager"}, "eager"),
            ({"eagerness": "eager", "prerender": True}, None),
        ],
        ids=repr,
    )
    def test_the_eagerness(self, value: object, eagerness: str | None) -> None:
        found = speculation_eagerness(value)
        assert found == eagerness
        assert found is None or isinstance(found, Eagerness)

    @pytest.mark.parametrize(
        ("value", "problem"),
        [
            ("conservative", None),
            ({"eagerness": "moderate"}, None),
            ("soon", "names the eagerness 'soon', not one of conservative,"),
            ({"prerender": "x"}, "asks for prerender, which the router never emits"),
            ({"eagerness": "eager", "when": 1}, "has unknown keys 'when'"),
            ({}, "names the eagerness None"),
        ],
        ids=repr,
    )
    def test_the_problem(self, value: object, problem: str | None) -> None:
        found = speculation_problem(value)
        if problem is None:
            assert found is None
        else:
            assert found is not None
            assert found.startswith(problem)


class TestOptOut:
    """The compression opt-out attribute lives in the configuration layer."""

    def test_one_name(self) -> None:
        assert OPT_OUT == COMPRESSION_OPT_OUT == "next_compress"
