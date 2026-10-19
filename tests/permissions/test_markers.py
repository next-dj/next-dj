import operator
from pathlib import Path

import pytest

from next.permissions.markers import (
    OPEN,
    AccessRule,
    Predicate,
    Requirement,
    chained,
    joined,
    predicate,
)


def _constant(name: str, *, answer: bool = True) -> Predicate:
    def test() -> bool:
        return answer

    test.__name__ = name
    return predicate(test)


class _Recorder:
    """A test that answers fixed and records every call it receives."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple[object, object]] = []

    def __call__(self, user: object, obj: object) -> object:
        self.calls.append((user, obj))
        return self.answer


class TestPredicate:
    """A predicate calls its test with the user and the object."""

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

    @pytest.mark.parametrize("join", [operator.and_, operator.or_])
    def test_predicates_do_not_combine_with_operators(self, join) -> None:
        with pytest.raises(TypeError):
            join(_constant("a"), _constant("b"))

    def test_a_predicate_does_not_invert(self) -> None:
        with pytest.raises(TypeError):
            operator.invert(_constant("a"))


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
    """Tests of every arity, each logging the arguments it was given."""

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
    """A predicate counts positional parameters once and passes that many arguments."""

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
    def test_a_predicate_passes_exactly_its_arity(self, method: str, expected) -> None:
        calls = _Calls()
        assert predicate(getattr(calls, method))("user", "obj") is True
        assert calls.log == [(method, expected)]


_ROOT = Path("/tree/proxy.py")

_LEAF = Path("/tree/a/page.py")


class TestRequirement:
    """A login carries nothing more, a permission names at least one."""

    @pytest.mark.parametrize(
        "fields",
        [
            {"kind": "login", "perms": ("auth.view_group",)},
            {"kind": "login", "obj": "note"},
        ],
        ids=["perms", "obj"],
    )
    def test_a_login_with_more_raises(self, fields) -> None:
        with pytest.raises(ValueError, match="login requirement carries"):
            Requirement(source=_ROOT, **fields)

    def test_a_permission_without_a_name_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one permission"):
            Requirement("permission", _ROOT)


class TestJoined:
    """The joined rule keeps every requirement once, in the order of the chain."""

    def test_nothing_declared_is_the_open_rule(self) -> None:
        assert joined([AccessRule(), AccessRule()]) is OPEN

    def test_requirements_and_sources_keep_their_first_place(self) -> None:
        login = Requirement("login", _ROOT)
        view = Requirement("permission", _LEAF, ("auth.view_group",))
        rules = [
            AccessRule((login,), sources=(_ROOT,)),
            AccessRule((view, login), sources=(_LEAF, _ROOT)),
        ]

        assert joined(rules) == AccessRule((login, view), False, (_ROOT, _LEAF))

    def test_public_alone_makes_a_rule(self) -> None:
        rule = joined([AccessRule(public=True, sources=(_LEAF,))], public=True)

        assert rule == AccessRule((), True, (_LEAF,))


_MIDDLE = Path("/tree/a/proxy.py")

_LOGIN = Requirement("login", _ROOT)


def _declared(path: Path, *requirements: Requirement, public: bool = False):
    return AccessRule(requirements, public, (path,))


class TestChained:
    """A chain joins root first, and a public file lifts what the chain above asked."""

    def test_nothing_declared_is_the_open_rule(self) -> None:
        assert chained([AccessRule(), AccessRule()]) is OPEN

    def test_requirements_join_root_first(self) -> None:
        view = Requirement("permission", _LEAF, ("auth.view_group",))
        rule = chained([_declared(_ROOT, _LOGIN), _declared(_LEAF, view, _LOGIN)])

        assert rule == AccessRule((_LOGIN, view), False, (_ROOT, _LEAF))

    def test_a_public_file_lifts_the_chain_above(self) -> None:
        rule = chained([_declared(_ROOT, _LOGIN), _declared(_MIDDLE, public=True)])

        assert rule == AccessRule((), True, (_MIDDLE,))

    def test_a_requirement_below_the_lift_binds_again(self) -> None:
        login = Requirement("login", _LEAF)
        rule = chained(
            [
                _declared(_ROOT, _LOGIN),
                _declared(_MIDDLE, public=True),
                _declared(_LEAF, login),
            ]
        )

        assert rule == AccessRule((login,), False, (_LEAF,))

    def test_a_second_public_keeps_the_first(self) -> None:
        rule = chained(
            [
                _declared(_ROOT, _LOGIN),
                _declared(_MIDDLE, public=True),
                _declared(_LEAF, public=True),
            ]
        )

        assert rule == AccessRule((), True, (_MIDDLE,))

    def test_a_requirement_beside_public_binds(self) -> None:
        login = Requirement("login", _MIDDLE)
        rule = chained([_declared(_MIDDLE, login, public=True)])

        assert rule == AccessRule((login,), False, (_MIDDLE,))

    def test_a_public_with_a_requirement_lifts_nothing_above(self) -> None:
        secret = Requirement("permission", _ROOT, ("app.secret",))
        login = Requirement("login", _MIDDLE)
        rule = chained(
            [_declared(_ROOT, secret), _declared(_MIDDLE, login, public=True)]
        )

        assert rule == AccessRule((secret, login), False, (_ROOT, _MIDDLE))

    def test_a_public_after_a_bound_lift_opens_again(self) -> None:
        login = Requirement("login", _MIDDLE)
        rule = chained(
            [
                _declared(_ROOT, public=True),
                _declared(_MIDDLE, login),
                _declared(_LEAF, public=True),
            ]
        )

        assert rule == AccessRule((), True, (_LEAF,))

    def test_joined_rules_open_nothing_of_one_another(self) -> None:
        opened = AccessRule((), True, (_MIDDLE,))

        rule = joined([_declared(_LEAF, _LOGIN), opened])

        assert rule == AccessRule((_LOGIN,), False, (_LEAF, _MIDDLE))

    def test_a_public_join_keeps_the_opening_file(self) -> None:
        opened = AccessRule((), True, (_MIDDLE,))

        assert joined([opened], public=True) == opened
