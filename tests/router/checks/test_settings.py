import pytest

from next.router.checks import (
    check_router_seams,
    check_router_settings,
    check_speculation,
)
from next.testing import override_next_settings
from tests.support.checks import written


class TestSettings:
    """`next.E035` and `next.E076` name every key and value outside the `ROUTER` shape."""

    def test_a_clean_scope(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert check_router_settings() == []

    def test_a_bool_current_is_legal(self) -> None:
        with override_next_settings(ROUTER={"ACTIVE_LINKS": False}):
            assert check_router_settings() == []

    def test_no_scope(self) -> None:
        with written({"ROUTER": "on"}):
            assert check_router_settings() == []
            assert check_router_seams() == []

    def test_misfits(self) -> None:
        with written({"ROUTER": {"BOGUS": 1, "SCOPE": "some"}}):
            messages = check_router_settings()
        assert [message.id for message in messages] == ["next.E035", "next.E076"]
        assert "'BOGUS'" in messages[0].msg
        assert (
            "['SCOPE'] must be 'pages', 'all' or 'opt_in', got 'some'"
            in messages[1].msg
        )


class TestSeams:
    """`next.E219` names a seam path that names no usable class."""

    @pytest.mark.parametrize(
        ("scope", "problem"),
        [
            ({"CUT": "missing.module.Cut"}, "does not import"),
            ({"CUT": "tests.support.broken.Cut"}, "raised RuntimeError"),
            ({"CUT": "next.router.navigation.CLEAR"}, "names no class"),
            ({"DIGEST": "next.router.VerifiedCut"}, "is no subclass of ChromeDigest"),
            ({"CUT": "next.router.NavigationCut"}, "is an abstract class"),
            ({"SHED": "next.router.ChromeDigest"}, "is no subclass of PrefetchShed"),
        ],
    )
    def test_an_unusable_path(self, scope: dict[str, object], problem: str) -> None:
        with override_next_settings(ROUTER=scope):
            (message,) = check_router_seams()
        assert message.id == "next.E219"
        assert problem in message.msg

    def test_usable_and_unset_paths(self) -> None:
        scope = {"CUT": "next.router.VerifiedCut", "DIGEST": "", "ACTIVE_LINKS": {}}
        with written({"ROUTER": scope}):
            assert check_router_seams() == []


@pytest.mark.usefixtures("router_on")
class TestSpeculation:
    """`next.E242` names a `SPECULATION` value that emits no rules."""

    @pytest.mark.parametrize(
        "value", [None, 3, "moderate", {"eagerness": "eager"}], ids=repr
    )
    def test_nothing_to_report(self, value: object) -> None:
        with written({"ROUTER": {"SPECULATION": value}}):
            assert check_speculation() == []

    def test_no_scope(self) -> None:
        with written({"ROUTER": "on"}):
            assert check_speculation() == []

    @pytest.mark.parametrize(
        ("value", "problem"),
        [
            ("prerender", "names the eagerness 'prerender'"),
            ({"eagerness": "eager", "prerender": True}, "asks for prerender"),
            ({"eagerness": "eager", "source": "list"}, "unknown keys 'source'"),
            ({}, "names the eagerness None"),
        ],
        ids=["string", "prerender", "unknown", "empty"],
    )
    def test_a_value_without_rules(self, value: object, problem: str) -> None:
        with override_next_settings(ROUTER={"SPECULATION": value}):
            (message,) = check_speculation()
        assert message.id == "next.E242"
        assert problem in message.msg
        assert "NEXT_FRAMEWORK['ROUTER']['SPECULATION']" in message.msg
