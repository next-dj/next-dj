import pytest

from next.conf.defaults import DEFAULTS
from next.conf.transactions import (
    SHAPE,
    alias_list,
    aliases_usable,
    atomic_choice,
    method_choice,
    rule_usable,
)


def test_the_shape_covers_every_default_key() -> None:
    assert set(SHAPE.keys) == set(DEFAULTS["TRANSACTIONS"])


@pytest.mark.parametrize(
    ("value", "atomic", "method"),
    [
        (True, True, True),
        (False, True, True),
        ("writes", True, True),
        ("auto", False, True),
        (1, False, False),
        ("reads", False, False),
    ],
)
def test_the_atomic_and_method_modes(value: object, atomic, method) -> None:
    assert atomic_choice(value) is atomic
    assert method_choice(value) is method


@pytest.mark.parametrize(
    ("value", "listed", "usable"),
    [
        (["default"], True, True),
        (("a", "b"), True, True),
        ("all", False, True),
        ("default", False, False),
        ([1], False, False),
    ],
)
def test_the_aliases(value: object, listed, usable) -> None:
    assert alias_list(value) is listed
    assert aliases_usable(value) is usable


@pytest.mark.parametrize(("value", "usable"), [(None, True), ("a.b", True), (1, False)])
def test_the_rule(value: object, usable) -> None:
    assert rule_usable(value) is usable
