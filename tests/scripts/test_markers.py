import pytest

from next.consent import NECESSARY
from next.scripts import Script, Strategy
from next.scripts.markers import HEAD_STRATEGIES, SCRIPT_ATTR, allowed_attr


class TestStrategy:
    """Three strategies render in the head, three wait for the runtime."""

    def test_the_wire_values(self) -> None:
        assert [str(member) for member in Strategy] == [
            "blocking",
            "async",
            "defer",
            "idle",
            "interaction",
            "manual",
        ]

    def test_the_head_strategies(self) -> None:
        assert {Strategy.BLOCKING, Strategy.ASYNC, Strategy.DEFER} == HEAD_STRATEGIES


class TestScript:
    """A script defaults to an async necessary one every page renders."""

    def test_the_defaults(self) -> None:
        script = Script("chat", src="https://widget.example/chat.js")
        assert script.strategy is Strategy.ASYNC
        assert script.category == NECESSARY
        assert script.auto is True
        assert script.attrs == {}
        assert not script.gated

    def test_a_category_to_grant_gates_it(self) -> None:
        assert Script("pixel", init="x", category="marketing").gated

    def test_only_the_allowed_attributes_are_kept(self) -> None:
        script = Script(
            "x",
            src="https://a.example/x.js",
            attrs={
                "integrity": "sha384-x",
                "crossorigin": "anonymous",
                "onload": "alert(1)",
                "data-id": "7",
                SCRIPT_ATTR: "forged",
                "id": "tag",
            },
        )
        assert script.allowed_attrs() == {
            "integrity": "sha384-x",
            "crossorigin": "anonymous",
            "data-id": "7",
        }

    @pytest.mark.parametrize(
        ("name", "allowed"),
        [
            ("referrerpolicy", True),
            ("id", False),
            ("data-cbid", True),
            ("data-", False),
            ("Data-x", False),
            ("src", False),
            (SCRIPT_ATTR, False),
            (3, False),
        ],
    )
    def test_the_attribute_allowlist(self, name: object, allowed) -> None:
        assert allowed_attr(name) is allowed
