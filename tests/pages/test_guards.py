import functools
import inspect
import types
from collections.abc import Callable
from unittest import mock

import pytest
from django import shortcuts
from django.contrib.auth.models import Group
from django.core import exceptions
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from django.shortcuts import get_object_or_404, get_object_or_404 as g404, redirect

from next.deps import Depends, DObject
from next.pages import Context, guards as guards_module
from next.pages.guards import DEPTH, hidden_guards, scans_as_guard


_REAL_SOURCE = inspect.getsource


class _Gone(Http404):
    pass


def _found(request: object) -> object:
    return get_object_or_404(request)


def _aliased(request: object) -> object:
    return g404(request)


def _listed(request: object) -> object:
    return shortcuts.get_list_or_404(request)


def _moved(request: object) -> object:
    return redirect(request)


def _missing(flag: object) -> str:
    if flag:
        raise Http404
    return "ok"


def _forbidden(flag: object) -> str:
    if flag:
        msg = "no"
        raise PermissionDenied(msg)
    return "ok"


def _subclassed(flag: object) -> str:
    if flag:
        raise _Gone
    return "ok"


def _dotted(flag: object) -> str:
    if flag:
        raise exceptions.BadRequest
    return "ok"


def _suspicious(flag: object) -> str:
    if flag:
        raise SuspiciousOperation
    return "ok"


def _shadowed(flag: object) -> str:
    if flag:
        raise lookalikes.BadRequest
    return "ok"


def _held_in_variable(flag: object) -> str:
    error = Http404("no")
    if flag:
        raise error
    return "ok"


def _bare(items: list[str]) -> str:
    try:
        return items[0]
    except IndexError:
        items.append("missing")
        raise


def _plain(value: int) -> int:
    return value + 1


def _lookup(flag: object) -> str:
    if flag:
        raise Http404
    return "ok"


def _through_helper(flag: object) -> str:
    return _lookup(flag)


def _through_namespace(flag: object) -> str:
    return lookalikes.lookup(flag)


def _through_class() -> object:
    return _Holder()


def _through_static(flag: object) -> str:
    return _Holder.static(flag)


def _through_classmethod(flag: object) -> str:
    return _Holder.owned(flag)


def _through_bound(flag: object) -> str:
    return _bound(flag)


def _recursive(flag: object) -> str:
    return _recursive(flag) if flag else "ok"


def _through_builtin(values: list[int]) -> int:
    return len(values)


def _deep4(flag: object) -> str:
    if flag:
        raise Http404
    return "ok"


def _deep3(flag: object) -> str:
    return _deep4(flag)


def _deep2(flag: object) -> str:
    return _deep3(flag)


def _deep1(flag: object) -> str:
    return _deep2(flag)


def _deep0(flag: object) -> str:
    return _deep1(flag)


def _far4(flag: object) -> object:
    return flag


def _far3(flag: object) -> object:
    return _far4(flag)


def _far2(flag: object) -> object:
    return _far3(flag)


def _far1(flag: object) -> object:
    return _far2(flag)


def _far0(flag: object) -> object:
    return _far1(flag)


class _Holder:
    """A class whose methods guard."""

    @staticmethod
    def static(flag: object) -> str:
        return _lookup(flag)

    @classmethod
    def owned(cls, flag: object) -> str:
        return _lookup(flag)

    def method(self, flag: object) -> str:
        return _lookup(flag)


class _Calling:
    """A callable instance, which the scan does not follow."""

    def __call__(self, flag: object) -> str:
        raise Http404


_bound = _Holder().method

lookalikes = types.SimpleNamespace(BadRequest=Http404, lookup=_lookup)


def _plainly(func: Callable[..., object]) -> Callable[..., object]:
    def wrapper(*args: object) -> object:
        return func(*args)

    return wrapper


def _refusing(func: Callable[..., object]) -> Callable[..., object]:
    @functools.wraps(func)
    def wrapper(*args: object) -> object:
        raise PermissionDenied

    return wrapper


def _passing(func: Callable[..., object]) -> Callable[..., object]:
    @functools.wraps(func)
    def wrapper(*args: object) -> object:
        return func(*args)

    return wrapper


@_plainly
def _decorated(flag: object) -> str:
    raise Http404


@_refusing
def _refused(flag: object) -> int:
    return 1


@_passing
def _passed(flag: object) -> int:
    return 1


def _empty_cell() -> Callable[[], object]:
    def inner() -> object:
        return pending()

    if inner is None:
        pending = _missing
    return inner


def _row(group: DObject[Group]) -> str:
    return group.name


def _row_by_name(group: "DObject[Group]") -> str:
    return group.name


def _refusing_factory() -> object:
    raise Http404


def _row_factory(group: DObject[Group]) -> object:
    return group


def _plain_factory() -> object:
    return 1


def _through_factory(value: object = Depends(_refusing_factory)) -> object:
    return value


def _through_row_factory(value: object = Depends(_row_factory)) -> object:
    return value


def _through_source(value: object = Context(_refusing_factory)) -> object:
    return value


def _twice(
    first: object = Depends(_plain_factory), second: object = Depends(_plain_factory)
) -> object:
    return (first, second)


def _named(value: object = Depends("scope"), key: object = Context("key")) -> object:
    return (value, key)


@pytest.fixture(autouse=True)
def _fresh() -> None:
    guards_module._SCANNED.clear()


class TestScansAsGuard:
    """A callable that answers 404, 403 or 400, itself or through a call, guards."""

    @pytest.mark.parametrize(
        "func",
        [
            _found,
            _aliased,
            _listed,
            _missing,
            _forbidden,
            _subclassed,
            _dotted,
            _suspicious,
            _through_helper,
            _through_static,
            _through_classmethod,
            _through_bound,
            _deep1,
            _decorated,
            _refused,
        ],
        ids=lambda f: f.__name__,
    )
    def test_a_guard_call_or_raise_is_found(self, func) -> None:
        assert scans_as_guard(func) is True

    @pytest.mark.parametrize(
        "func",
        [
            _plain,
            _bare,
            _moved,
            _shadowed,
            _held_in_variable,
            _through_namespace,
            _through_class,
            _through_builtin,
            _recursive,
            _deep0,
            _passed,
        ],
        ids=lambda f: f.__name__,
    )
    def test_a_body_without_one_is_no_guard(self, func) -> None:
        assert scans_as_guard(func) is False

    def test_a_partial_and_a_wrapper_scan_their_target(self) -> None:
        @functools.wraps(_missing)
        def wrapper(flag: object) -> str:
            return _missing(flag)

        assert scans_as_guard(functools.partial(_missing, flag=True)) is True
        assert scans_as_guard(wrapper) is True

    def test_a_wrapper_naming_itself_is_read_once(self) -> None:
        def looped(flag: object) -> object:
            return flag

        vars(looped)["__wrapped__"] = looped

        assert scans_as_guard(looped) is False

    def test_a_callable_without_a_function_is_no_guard(self) -> None:
        assert scans_as_guard(len) is False
        assert scans_as_guard(_Calling()) is False

    def test_an_empty_closure_cell_names_nothing(self) -> None:
        assert scans_as_guard(_empty_cell()) is False

    def test_a_source_that_cannot_be_read_is_no_guard(self) -> None:
        with mock.patch("inspect.getsource", side_effect=OSError):
            assert scans_as_guard(_missing) is False

    def test_a_source_that_does_not_parse_is_no_guard(self) -> None:
        with mock.patch("inspect.getsource", return_value="def broken(:\n"):
            assert scans_as_guard(_plain) is False

    def test_the_verdict_is_kept_per_function(self) -> None:
        scans_as_guard(_missing)

        with mock.patch.object(guards_module, "_tree") as tree:
            assert scans_as_guard(_missing) is True

        tree.assert_not_called()


class TestInjectedGuards:
    """A parameter the resolver fills with what answers 404 makes a guard."""

    @pytest.mark.parametrize(
        "func",
        [_row, _row_by_name, _through_factory, _through_row_factory, _through_source],
        ids=lambda f: f.__name__,
    )
    def test_a_row_or_a_refusing_factory_guards(self, func) -> None:
        assert scans_as_guard(func) is True

    @pytest.mark.parametrize("func", [_twice, _named], ids=lambda f: f.__name__)
    def test_a_plain_factory_or_a_named_value_is_no_guard(self, func) -> None:
        assert scans_as_guard(func) is False

    def test_a_signature_that_cannot_be_read_is_read_by_its_body(self) -> None:
        with mock.patch.object(
            guards_module, "cached_signature", side_effect=ValueError
        ):
            assert scans_as_guard(_row) is False
            assert scans_as_guard(_missing) is True


class TestHiddenGuards:
    """A function past the depth the scan follows is named when it names a guard."""

    def test_the_scan_follows_three_calls_deep(self) -> None:
        assert DEPTH == 3

    def test_a_guard_past_the_depth_is_named(self) -> None:
        assert hidden_guards(_deep0) == ("_deep4",)

    def test_a_plain_function_past_the_depth_hides_nothing(self) -> None:
        assert hidden_guards(_far0) == ()

    def test_a_function_past_the_depth_without_source_hides_nothing(self) -> None:
        def source(target: object) -> str:
            if target is _deep4.__code__:
                raise OSError
            return _REAL_SOURCE(target)

        with mock.patch("inspect.getsource", side_effect=source):
            assert hidden_guards(_deep0) == ()

    def test_a_guard_the_scan_finds_hides_nothing(self) -> None:
        assert hidden_guards(_through_helper) == ()

    def test_a_callable_without_source_hides_nothing(self) -> None:
        assert hidden_guards(len) == ()
