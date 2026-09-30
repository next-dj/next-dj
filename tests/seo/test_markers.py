import math
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from next.seo import RobotsRule, RobotsRuleError, SitemapEntry, SitemapEntryError
from next.seo.markers import CHANGEFREQS, is_number


class TestSitemapEntry:
    """A `SitemapEntry` is a frozen value with read-only kwargs and checked hints."""

    def test_defaults_to_no_kwargs_and_no_hints(self) -> None:
        entry = SitemapEntry()
        assert entry.kwargs == {}
        assert (entry.lastmod, entry.changefreq, entry.priority) == (None, None, None)

    def test_the_kwargs_are_a_read_only_copy(self) -> None:
        kwargs = {"slug": "a"}
        entry = SitemapEntry(kwargs=kwargs)
        kwargs["slug"] = "b"
        assert isinstance(entry.kwargs, MappingProxyType)
        assert entry.kwargs == {"slug": "a"}
        with pytest.raises(TypeError):
            entry.kwargs["slug"] = "c"

    @pytest.mark.parametrize(
        ("hints", "field"),
        [
            ({"lastmod": "2026-01-01"}, "lastmod"),
            ({"changefreq": "sometimes"}, "changefreq"),
            ({"priority": 1.5}, "priority"),
            ({"priority": -0.1}, "priority"),
            ({"priority": True}, "priority"),
            ({"priority": math.nan}, "priority"),
            ({"priority": "0.5"}, "priority"),
        ],
        ids=[
            "lastmod-string",
            "unknown-changefreq",
            "priority-above-one",
            "priority-below-zero",
            "priority-bool",
            "priority-nan",
            "priority-string",
        ],
    )
    def test_a_value_the_protocol_refuses_raises(
        self, hints: dict[str, object], field: str
    ) -> None:
        with pytest.raises(SitemapEntryError) as caught:
            SitemapEntry(**hints)
        assert caught.value.field == field
        assert caught.value.value is hints[field]
        assert str(caught.value).startswith(f"SitemapEntry.{field} is ")

    @pytest.mark.parametrize("changefreq", sorted(CHANGEFREQS))
    def test_every_protocol_changefreq_passes(self, changefreq: str) -> None:
        assert SitemapEntry(changefreq=changefreq).changefreq == changefreq

    def test_the_bounds_and_a_datetime_pass(self) -> None:
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        assert SitemapEntry(priority=0).priority == 0
        assert SitemapEntry(priority=1.0, lastmod=stamp).lastmod == stamp


class TestRobotsRule:
    """A `RobotsRule` pins its lists as tuples and refuses what breaks the grammar."""

    def test_defaults_to_every_agent_with_no_directives(self) -> None:
        rule = RobotsRule()
        assert rule.user_agent == ("*",)
        assert rule.user_agents == ("*",)
        assert (rule.allow, rule.disallow, rule.crawl_delay) == ((), (), None)

    def test_lists_and_bare_strings_are_pinned_as_tuples(self) -> None:
        rule = RobotsRule(user_agent=["a", "b"], allow="/", disallow=["/private/"])
        assert rule.user_agents == ("a", "b")
        assert rule.allow == ("/",)
        assert rule.disallow == ("/private/",)

    def test_wildcards_an_anchor_and_a_delay_pass(self) -> None:
        rule = RobotsRule(
            user_agent="Google-Extended",
            disallow=("*.pdf$", "/search/*?q="),
            crawl_delay=2.5,
        )
        assert rule.disallow == ("*.pdf$", "/search/*?q=")
        assert rule.crawl_delay == 2.5

    @pytest.mark.parametrize(
        ("values", "field"),
        [
            ({"user_agent": ()}, "user_agent"),
            ({"user_agent": "Bad Bot"}, "user_agent"),
            ({"user_agent": "evil\nAllow"}, "user_agent"),
            ({"user_agent": [3]}, "user_agent"),
            ({"disallow": "private/"}, "disallow"),
            ({"disallow": ""}, "disallow"),
            ({"disallow": "/x\nUser-agent: evil"}, "disallow"),
            ({"disallow": "/x\r\nAllow: /"}, "disallow"),
            ({"allow": "/a b"}, "allow"),
            ({"allow": "/a#b"}, "allow"),
            ({"allow": "/a$b"}, "allow"),
            ({"allow": [None]}, "allow"),
            ({"crawl_delay": True}, "crawl_delay"),
            ({"crawl_delay": -1}, "crawl_delay"),
            ({"crawl_delay": math.inf}, "crawl_delay"),
            ({"crawl_delay": "5"}, "crawl_delay"),
        ],
        ids=[
            "no-agent",
            "agent-with-space",
            "agent-with-line-break",
            "agent-not-a-string",
            "relative-path",
            "empty-path",
            "path-with-lf",
            "path-with-crlf",
            "path-with-space",
            "path-with-comment",
            "dollar-inside",
            "path-not-a-string",
            "delay-bool",
            "delay-negative",
            "delay-infinite",
            "delay-string",
        ],
    )
    def test_a_value_that_breaks_the_group_raises(
        self, values: dict[str, object], field: str
    ) -> None:
        with pytest.raises(RobotsRuleError) as caught:
            RobotsRule(**values)
        assert caught.value.field == field
        assert str(caught.value).startswith(f"RobotsRule.{field} is ")


class TestIsNumber:
    """A number is a finite int or float, a bool never one."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1, True), (0.5, True), (True, False), (math.nan, False), ("1", False)],
    )
    def test_reads_a_finite_number(self, value: object, *, expected: bool) -> None:
        assert is_number(value) is expected
