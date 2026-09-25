import dataclasses
from datetime import date

import pytest

from next.seo import Entry, Rule


class TestEntry:
    def test_defaults_to_no_kwargs_and_no_hints(self) -> None:
        entry = Entry()
        assert entry.kwargs == {}
        assert (entry.lastmod, entry.changefreq, entry.priority) == (None, None, None)

    def test_is_frozen(self) -> None:
        entry = Entry(kwargs={"slug": "a"}, lastmod=date(2026, 1, 1))
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.priority = 0.5


class TestRule:
    def test_defaults_to_every_agent_with_no_directives(self) -> None:
        rule = Rule()
        assert rule.user_agent == "*"
        assert rule.user_agents == ("*",)
        assert (rule.allow, rule.disallow, rule.crawl_delay) == ((), (), None)

    def test_lists_are_pinned_as_tuples(self) -> None:
        rule = Rule(user_agent=["a", "b"], allow=["/"], disallow=["/private/"])
        assert rule.user_agent == ("a", "b")
        assert rule.user_agents == ("a", "b")
        assert rule.allow == ("/",)
        assert rule.disallow == ("/private/",)

    def test_a_single_agent_stays_a_string(self) -> None:
        rule = Rule(user_agent="Googlebot", crawl_delay=5)
        assert rule.user_agent == "Googlebot"
        assert rule.user_agents == ("Googlebot",)
        assert rule.crawl_delay == 5
