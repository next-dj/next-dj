import operator
import re
from itertools import product

import pytest

from next.permissions import Predicate, predicate


_ANSWERS = list(product((True, False), repeat=2))


def _constant(name: str, *, answer: bool = True) -> Predicate:
    def test() -> bool:
        return answer

    test.__name__ = name
    return predicate(test)


class _Recorder:
    """A leaf test that answers fixed and records every call it receives."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple[object, object]] = []

    def __call__(self, user: object, obj: object) -> object:
        self.calls.append((user, obj))
        return self.answer


class TestLeaf:
    """A leaf predicate calls its test with the user and the object."""

    def test_the_test_receives_user_and_obj(self) -> None:
        recorder = _Recorder(answer=True)
        user, obj = object(), object()
        assert predicate(recorder)(user, obj) is True
        assert recorder.calls == [(user, obj)]

    def test_obj_defaults_to_none(self) -> None:
        recorder = _Recorder(answer=True)
        user = object()
        predicate(recorder)(user)
        assert recorder.calls == [(user, None)]

    @pytest.mark.parametrize(
        ("raw", "expected"), [(1, True), ("", False), (None, False)]
    )
    def test_a_truthy_answer_becomes_a_bool(self, raw: object, expected) -> None:
        assert predicate(_Recorder(answer=raw))(None) is expected

    def test_the_name_is_the_test_name(self) -> None:
        def is_owner(user: object, obj: object) -> bool:
            return True

        assert predicate(is_owner).name == "is_owner"

    def test_a_nameless_test_is_named_by_its_repr(self) -> None:
        recorder = _Recorder(answer=True)
        assert predicate(recorder).name == repr(recorder)

    def test_a_predicate_passes_through_unchanged(self) -> None:
        rule = _constant("always")
        assert predicate(rule) is rule

    def test_a_leaf_yields_its_own_test(self) -> None:
        rule = _constant("always")
        assert list(rule.tests()) == [("always", rule.test)]


def no_params():
    return True


def user_only(user):
    return user


def defaulted_obj(user, obj=None):
    return user, obj


def three_params(user, obj, extra):
    return extra


def var_positional(*args):
    return args


def var_keyword(**kwargs):
    return kwargs


class _Calls:
    """Leaf tests of every arity, each logging the arguments it was given."""

    def __init__(self) -> None:
        self.log: list[tuple[str, tuple[object, ...]]] = []

    def none(self) -> bool:
        self.log.append(("none", ()))
        return True

    def one(self, user: object) -> bool:
        self.log.append(("one", (user,)))
        return True

    def two(self, user: object, obj: object) -> bool:
        self.log.append(("two", (user, obj)))
        return True


class TestArity:
    """A leaf counts its positional parameters once and passes that many arguments."""

    @pytest.mark.parametrize(
        ("test", "arity"),
        [
            pytest.param(no_params, 0, id="none"),
            pytest.param(user_only, 1, id="user"),
            pytest.param(defaulted_obj, 2, id="user-and-defaulted-obj"),
            pytest.param(three_params, 2, id="capped-at-two"),
            pytest.param(var_positional, 0, id="var-positional"),
            pytest.param(var_keyword, 0, id="var-keyword"),
            pytest.param(max, 2, id="unreadable"),
        ],
    )
    def test_the_arity_is_counted_at_decoration(self, test, arity: int) -> None:
        assert predicate(test).arity == arity

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            pytest.param("none", (), id="none"),
            pytest.param("one", ("user",), id="user"),
            pytest.param("two", ("user", "obj"), id="user-and-obj"),
        ],
    )
    def test_a_leaf_passes_exactly_its_arity(self, method: str, expected) -> None:
        calls = _Calls()
        assert predicate(getattr(calls, method))("user", "obj") is True
        assert calls.log == [(method, expected)]

    def test_mixed_arities_compose(self) -> None:
        calls = _Calls()
        rule = (predicate(calls.none) & predicate(calls.one)) | ~predicate(calls.two)
        assert rule("user", "obj") is True
        assert calls.log == [("none", ()), ("one", ("user",))]

    def test_mixed_arities_reach_every_operand_when_open(self) -> None:
        calls = _Calls()
        rule = ~predicate(calls.none) | (predicate(calls.one) & predicate(calls.two))
        assert rule("user") is True
        assert calls.log == [("none", ()), ("one", ("user",)), ("two", ("user", None))]

    def test_tests_yields_every_leaf_callable_in_order(self) -> None:
        calls = _Calls()
        rule = predicate(calls.two) & ~(predicate(calls.none) | predicate(calls.one))
        assert list(rule.tests()) == [
            ("two", calls.two),
            ("none", calls.none),
            ("one", calls.one),
        ]


class TestShape:
    """A predicate is either a leaf or a composite its operator can evaluate."""

    @pytest.mark.parametrize(
        ("fields", "problem"),
        [
            pytest.param({}, "holds neither a test nor an operator", id="empty"),
            pytest.param(
                {"test": no_params, "operator": "&"},
                "holds a test and an operator or operands at once",
                id="test-and-operator",
            ),
            pytest.param(
                {"test": no_params, "operands": (predicate(no_params),)},
                "holds a test and an operator or operands at once",
                id="test-and-operands",
            ),
            pytest.param(
                {"test": no_params, "arity": 3},
                "passes 3 arguments to its test, not 0 to 2",
                id="arity-above",
            ),
            pytest.param(
                {"test": no_params, "arity": -1},
                "passes -1 arguments to its test, not 0 to 2",
                id="arity-below",
            ),
            pytest.param(
                {"operator": "~"}, "inverts other than one operand", id="invert-none"
            ),
            pytest.param(
                {"operator": "~", "operands": (predicate(no_params),) * 2},
                "inverts other than one operand",
                id="invert-two",
            ),
            pytest.param(
                {"operator": "&", "operands": (predicate(no_params),)},
                "joins fewer than two operands with '&'",
                id="and-one",
            ),
            pytest.param(
                {"operator": "|"},
                "joins fewer than two operands with '|'",
                id="or-none",
            ),
        ],
    )
    def test_a_malformed_shape_raises(self, fields, problem: str) -> None:
        with pytest.raises(ValueError, match=re.escape(f"'odd' {problem}.")):
            Predicate(name="odd", **fields)

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            pytest.param({"test": no_params, "arity": 0}, True, id="leaf"),
            pytest.param(
                {"operator": "&", "operands": (predicate(no_params),) * 3},
                True,
                id="three-way-and",
            ),
            pytest.param(
                {"operator": "~", "operands": (predicate(no_params),)},
                False,
                id="invert-one",
            ),
        ],
    )
    def test_a_well_formed_shape_builds(self, fields, expected) -> None:
        assert Predicate(name="fine", **fields)(None) is expected


class TestComposition:
    """`&`, `|` and `~` follow the boolean truth tables."""

    @pytest.mark.parametrize(("left", "right"), _ANSWERS)
    def test_and(self, left, right) -> None:
        rule = _constant("a", answer=left) & _constant("b", answer=right)
        assert rule(None) is (left and right)

    @pytest.mark.parametrize(("left", "right"), _ANSWERS)
    def test_or(self, left, right) -> None:
        rule = _constant("a", answer=left) | _constant("b", answer=right)
        assert rule(None) is (left or right)

    @pytest.mark.parametrize("answer", [True, False])
    def test_invert(self, answer) -> None:
        assert (~_constant("a", answer=answer))(None) is (not answer)

    def test_operands_receive_user_and_obj(self) -> None:
        left, right = _Recorder(answer=True), _Recorder(answer=True)
        user, obj = object(), object()
        (predicate(left) & ~~predicate(right))(user, obj)
        assert left.calls == [(user, obj)]
        assert right.calls == [(user, obj)]


class TestShortCircuit:
    """A composite stops at the operand that settles the answer."""

    @pytest.mark.parametrize(
        ("join", "settling"),
        [
            pytest.param(operator.and_, False, id="and-stops-on-false"),
            pytest.param(operator.or_, True, id="or-stops-on-true"),
        ],
    )
    def test_the_right_operand_is_not_called(self, join, settling) -> None:
        right = _Recorder(answer=not settling)
        rule = join(_constant("a", answer=settling), predicate(right))
        assert rule(None) is settling
        assert right.calls == []

    @pytest.mark.parametrize(
        ("join", "open_answer"),
        [
            pytest.param(operator.and_, True, id="and-goes-on-after-true"),
            pytest.param(operator.or_, False, id="or-goes-on-after-false"),
        ],
    )
    def test_an_open_answer_reaches_the_right_operand(self, join, open_answer) -> None:
        right = _Recorder(answer=open_answer)
        join(_constant("a", answer=open_answer), predicate(right))(None)
        assert len(right.calls) == 1


class TestNames:
    """A composite is named by the expression it was built from."""

    def test_and_name(self) -> None:
        assert (_constant("a") & _constant("b")).name == "(a & b)"

    def test_or_name(self) -> None:
        assert (_constant("a") | _constant("b")).name == "(a | b)"

    def test_invert_name(self) -> None:
        assert (~_constant("a")).name == "~a"

    def test_nested_name(self) -> None:
        a, b, c = (_constant(name) for name in "abc")
        assert ((a & ~b) | c).name == "((a & ~b) | c)"


class TestForeignOperand:
    """A non-Predicate operand leaves the operator to the other side."""

    @pytest.mark.parametrize("method", ["__and__", "__or__"])
    def test_returns_not_implemented(self, method: str) -> None:
        rule = _constant("a")
        assert getattr(rule, method)(lambda: True) is NotImplemented

    @pytest.mark.parametrize("join", [operator.and_, operator.or_])
    def test_the_operator_raises_type_error(self, join) -> None:
        with pytest.raises(TypeError):
            join(_constant("a"), True)
