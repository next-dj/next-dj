import re
from collections.abc import Iterator

import pytest
from django.test import override_settings
from django.test.utils import override_script_prefix

from next.router.checks import check_exclude_prefixes, prefixes as router_checks
from next.testing import override_next_settings


HOLDS = re.compile(r"holds '([^']*)', which")


def _unmatched(*prefixes: str, enabled: bool = True) -> list[str]:
    with override_next_settings(ROUTER={"ENABLED": enabled, "EXCLUDE": prefixes}):
        messages = check_exclude_prefixes()
    assert {message.id for message in messages} <= {"next.W184"}
    return [HOLDS.search(message.msg)[1] for message in messages]


class TestExcludePrefixes:
    """`next.W184` names an `EXCLUDE` prefix that starts no path of the URLconf."""

    @pytest.fixture(autouse=True)
    def _urls(self) -> Iterator[None]:
        with override_settings(
            ROOT_URLCONF="tests.router.urls_exclude",
            LANGUAGES=[("en", "English"), ("de", "German")],
            MEDIA_URL="/uploads/",
        ):
            yield

    @pytest.mark.parametrize(
        "prefix",
        [
            "/legacy/",
            "/legacy",
            "/legacy/reports/7/",
            "/about",
            "/old-site/post/",
            "/feed",
            "/col",
            "/color/",
            "/raw/any/depth/",
            "/shop/",
            "/de/shop/",
            "/static/admin/",
            "/uploads/",
        ],
    )
    def test_a_prefix_some_route_serves_is_silent(self, prefix: str) -> None:
        assert _unmatched(prefix) == []

    @pytest.mark.parametrize(
        "prefix",
        ["/legcy/", "/legacy/nope/", "/about/team/", "/feed.json", "/fr/shop/"],
    )
    def test_a_prefix_no_route_serves_warns(self, prefix: str) -> None:
        assert _unmatched(prefix) == [prefix]

    def test_the_message(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": True, "EXCLUDE": ["/legcy/"]}):
            (message,) = check_exclude_prefixes()
        assert message.msg == (
            "NEXT_FRAMEWORK['ROUTER']['EXCLUDE'] holds '/legcy/', which starts no "
            "path the URLconf serves, so it keeps nothing out of the client router."
        )
        assert "Silence next.W184" in str(message.hint)

    def test_a_disabled_router_is_silent(self) -> None:
        assert _unmatched("/legcy/", enabled=False) == []

    def test_no_prefixes_is_silent(self) -> None:
        assert _unmatched() == []

    def test_the_script_prefix_is_read_off_the_prefix(self) -> None:
        with override_script_prefix("/app/"):
            assert _unmatched("/app/legacy/", "/app/legcy/", "/elsewhere/") == [
                "/app/legcy/"
            ]


class TestRegexHead:
    """The literal start of a pattern regex stops where the regex stops being literal."""

    @pytest.mark.parametrize(
        ("regex", "head", "rest"),
        [
            pytest.param(r"^a\.b/\Z", "a.b/", r"\Z", id="escaped"),
            pytest.param(r"^ab?c/$", "a", "b?c/$", id="quantified"),
            pytest.param(r"^a\-?b/$", "a", r"\-?b/$", id="quantified-escape"),
            pytest.param(r"^a/(?P<x>\d+)/$", "a/", r"(?P<x>\d+)/$", id="group"),
            pytest.param(r"^a/\d+/$", "a/", r"\d+/$", id="class-escape"),
            pytest.param(r"^a/$|^b/$", "", r"^a/$|^b/$", id="alternation"),
        ],
    )
    def test_the_head_and_the_rest(self, regex: str, head: str, rest: str) -> None:
        assert router_checks._regex_head(regex) == (head, rest)
