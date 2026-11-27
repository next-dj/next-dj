import enum

import pytest

from next.conf.shapes import (
    Misfit,
    Section,
    Shape,
    alternatives,
    choice,
    count,
    flag,
    member,
    misfits,
    modes,
    number,
    owned,
    pick,
    shown,
    text,
    texts,
)


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


FLAG = Shape(flag, "True or False")
NESTED = Section({"DEPTH": Shape(count(1), "an integer from 1")})
SWITCHED = Section({"ON": FLAG}, other=FLAG)
OWNED = Section(
    {"BACKEND": Shape(text, "a dotted path"), "OPTIONS": Section({"SIZE": FLAG})}
)
FOREIGN = Section({"SIZE": FLAG}, default="shipped.Backend")
BOUNDED = Section(
    {
        "LOW": Shape(count(0), "an integer from 0"),
        "HIGH": Shape(count(1), "an integer from 1"),
    },
    below=(("LOW", "HIGH"),),
)
SHAPE = Section(
    {
        "FLAG": FLAG,
        "MODE": modes(Mode),
        "NESTED": NESTED,
        "SWITCHED": SWITCHED,
        "PLUGIN": Section(
            {"BACKEND": Shape(text, "a dotted path"), "OPTIONS": FOREIGN}
        ),
        "BOUNDED": BOUNDED,
    }
)


class TestShown:
    @pytest.mark.parametrize(
        ("value", "named"),
        [
            (None, "None"),
            (3, "3"),
            (1.5, "1.5"),
            (True, "True"),
            ("a", "'a'"),
            ([1], "a list"),
            ({}, "a dict"),
        ],
    )
    def test_a_scalar_by_its_repr_else_by_its_type(self, value, named) -> None:
        assert shown(value) == named


class TestAlternatives:
    @pytest.mark.parametrize(
        ("values", "named"),
        [
            (("a", "b"), "'a' or 'b'"),
            (("state", "always", False), "'state', 'always' or False"),
            (Mode, "'fast' or 'slow'"),
        ],
    )
    def test_the_last_value_follows_or(self, values, named) -> None:
        assert alternatives(values) == named


class TestPredicates:
    @pytest.mark.parametrize(
        ("usable", "value", "fits"),
        [
            (flag, False, True),
            (flag, 0, False),
            (text, "a", True),
            (text, "", False),
            (text, 1, False),
            (texts, ("a", "b"), True),
            (texts, ["a", ""], False),
            (texts, "ab", False),
            (count(0), 0, True),
            (count(1), 0, False),
            (count(0), True, False),
            (count(0), 1.0, False),
            (number(positive=True), 0.5, True),
            (number(positive=True), 0, False),
            (number(positive=False), 0, True),
            (number(positive=False), -1, False),
            (number(positive=False), float("inf"), False),
            (number(positive=False), False, False),
            (number(positive=False), "1", False),
            (member(Mode), "fast", True),
            (member(Mode), Mode.SLOW, True),
            (member(Mode), "quick", False),
            (member(Mode), 1, False),
        ],
    )
    def test_the_predicate(self, usable, value, fits) -> None:
        assert usable(value) is fits

    @pytest.mark.parametrize(
        ("value", "fits"),
        [("state", True), (False, True), (0, False), ("False", False), (True, False)],
    )
    def test_a_choice_matches_the_type_too(self, value, fits) -> None:
        shape = choice(("state", False))
        assert shape.usable(value) is fits
        assert shape.expected == "'state' or False"

    def test_modes_name_the_members(self) -> None:
        assert modes(Mode).expected == "'fast' or 'slow'"


class TestMisfits:
    def test_a_clean_scope(self) -> None:
        assert (
            misfits({"FLAG": True, "MODE": "slow", "NESTED": {"DEPTH": 2}}, SHAPE) == []
        )

    def test_unknown_keys_at_every_level_name_the_allowed_ones(self) -> None:
        found = misfits({"EXTRA": 1, "NESTED": {"WIDTH": 1}}, SHAPE)
        assert found == [
            Misfit((), unknown=("EXTRA",), allowed=tuple(sorted(SHAPE.keys))),
            Misfit(("NESTED",), unknown=("WIDTH",), allowed=("DEPTH",)),
        ]

    def test_a_value_outside_its_shape(self) -> None:
        assert misfits({"MODE": "quick", "NESTED": {"DEPTH": 0}}, SHAPE) == [
            Misfit(("MODE",), "'fast' or 'slow'", "'quick'"),
            Misfit(("NESTED", "DEPTH"), "an integer from 1", "0"),
        ]

    def test_a_section_takes_a_dict_alone_by_default(self) -> None:
        assert misfits({"NESTED": True}, SHAPE) == [
            Misfit(("NESTED",), "a dict", "True")
        ]

    def test_a_section_may_take_another_shape(self) -> None:
        assert misfits({"SWITCHED": False}, SHAPE) == []
        assert misfits({"SWITCHED": "off"}, SHAPE) == [
            Misfit(("SWITCHED",), "True or False", "'off'")
        ]

    def test_the_section_of_a_foreign_backend_passes_unread(self) -> None:
        scope = {"PLUGIN": {"BACKEND": "my.Backend", "OPTIONS": {"ANY": object()}}}
        assert misfits(scope, SHAPE) == []

    def test_the_section_of_the_shipped_backend_is_read(self) -> None:
        scope = {"PLUGIN": {"BACKEND": "shipped.Backend", "OPTIONS": {"SIZE": 1}}}
        assert misfits(scope, SHAPE) == [
            Misfit(("PLUGIN", "OPTIONS", "SIZE"), "True or False", "1")
        ]

    @pytest.mark.parametrize(
        ("bounded", "defaults", "found"),
        [
            ({"LOW": 5, "HIGH": 5}, {}, ["5"]),
            ({"LOW": 4, "HIGH": 5}, {}, []),
            ({"LOW": 5}, {"HIGH": 5}, ["5"]),
            ({"LOW": 5}, {}, []),
            ({"LOW": 5, "HIGH": 0}, {}, []),
        ],
    )
    def test_a_key_stays_below_its_ceiling(self, bounded, defaults, found) -> None:
        misfit = [
            m.found
            for m in misfits({"BOUNDED": bounded}, SHAPE, {"BOUNDED": defaults})
            if m.path == ("BOUNDED", "LOW")
        ]
        assert misfit == found

    def test_a_ceiling_names_its_value(self) -> None:
        [misfit] = misfits({"BOUNDED": {"LOW": 7, "HIGH": 3}}, SHAPE)
        assert misfit.expected == "an integer below HIGH, which is 3, or None"


class TestOwned:
    @pytest.mark.parametrize(
        ("parent", "known"),
        [({}, True), ({"BACKEND": "shipped.Backend"}, True), ({"BACKEND": "x"}, False)],
    )
    def test_the_shipped_backend_owns_its_section(self, parent, known) -> None:
        assert owned(FOREIGN, parent) is known

    def test_a_section_without_a_backend_is_always_known(self) -> None:
        assert owned(NESTED, {"BACKEND": "x"}) is True


class TestPick:
    @pytest.mark.parametrize(
        ("scope", "value"), [({"FLAG": False}, False), ({"FLAG": 1}, True), ({}, True)]
    )
    def test_a_usable_value_else_the_default(self, scope, value) -> None:
        assert pick(scope, "FLAG", flag, {"FLAG": True}) is value


def test_owned_sections_are_walked_below_a_backend() -> None:
    scope = {"BACKEND": "my.Backend", "OPTIONS": {"SIZE": 1}}
    assert misfits(scope, OWNED) == [Misfit(("OPTIONS", "SIZE"), "True or False", "1")]
