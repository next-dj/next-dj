import importlib
import pkgutil
from collections.abc import Iterator
from types import ModuleType

import pytest
from django.conf import settings
from django.core.checks import ERROR, CheckMessage, run_checks
from django.test import override_settings

from next import conf
from next.conf.merge import (
    DICT_KEYS,
    LIST_KEYS,
    OPTIONAL_STR_KEYS,
    STR_KEYS,
    WARNED_KEYS,
)
from next.conf.shapes import Section, Shape


SHAPE_CODES = frozenset(
    {"next.E035", "next.E044", "next.E051", "next.E052", "next.E067", "next.E076"}
)
TOKEN_LIFETIME_CODE = "next.E233"

# One probe per kind, so every leaf meets a wrong number, a wrong text and a wrong type.
PROBES: tuple[tuple[object, ...], ...] = (
    (-1, 0, 1.5, 10**12),
    ("", "x"),
    (True, None, ["x"], {"X": 1}),
)
MISSING_PATH = "nowhere.Missing"


def _scopes() -> list[ModuleType]:
    """Return every `next.conf` module that declares the shape of a scope."""
    found = (
        importlib.import_module(f"next.conf.{info.name}")
        for info in pkgutil.iter_modules(conf.__path__)
    )
    return [
        module
        for module in found
        if isinstance(getattr(module, "SHAPE", None), Section)
        and isinstance(getattr(module, "SCOPE", None), str)
    ]


def _leaves(
    section: Section, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Shape | Section]]:
    for key, shape in section.keys.items():
        yield (*path, key), shape
        if isinstance(shape, Section):
            yield from _leaves(shape, (*path, key))


def _misfits(shape: Shape | Section) -> list[object]:
    if isinstance(shape, Section):
        unknown: list[object] = [{"UNKNOWN": 1}]
        other = shape.other
        return unknown if other is not None and other.usable("x") else ["x", *unknown]
    return [
        next(value for value in kind if not shape.usable(value))
        for kind in PROBES
        if not all(map(shape.usable, kind))
    ]


def _nested(path: tuple[str, ...], value: object) -> object:
    for key in reversed(path):
        value = {key: value}
    return value


def _address(path: tuple[str, ...]) -> str:
    return "NEXT_FRAMEWORK" + "".join(f"[{key!r}]" for key in path)


def _scope_cases() -> Iterator[object]:
    for module in _scopes():
        for path, shape in _leaves(module.SHAPE):
            for value in _misfits(shape):
                where = (module.SCOPE, *path)
                yield pytest.param(where, value, id=f"{'.'.join(where)}={value!r}"[:80])


def _top_level_cases() -> Iterator[object]:
    for key in sorted(LIST_KEYS | DICT_KEYS):
        yield pytest.param((key,), "x", id=f"{key}='x'")
    for key in sorted((STR_KEYS | OPTIONAL_STR_KEYS) - WARNED_KEYS):
        yield pytest.param((key,), 5, id=f"{key}=5")


def _dotted_cases() -> Iterator[object]:
    for module in _scopes():
        for path, shape in _leaves(module.SHAPE):
            if not isinstance(shape, Shape) or "dotted path" not in shape.expected:
                continue
            value = next(
                (
                    candidate
                    for candidate in (MISSING_PATH, [MISSING_PATH])
                    if shape.usable(candidate)
                ),
                None,
            )
            if value is not None:
                where = (module.SCOPE, *path)
                yield pytest.param(where, value, id=".".join(where))


@pytest.fixture(scope="module")
def baseline() -> frozenset[tuple[str | None, str]]:
    return frozenset(_keyed(run_checks(include_deployment_checks=True)))


def _keyed(messages: list[CheckMessage]) -> Iterator[tuple[str | None, str]]:
    return ((message.id, message.msg) for message in messages)


def _added(
    path: tuple[str, ...], value: object, baseline: frozenset[tuple[str | None, str]]
) -> list[CheckMessage]:
    """Run every check over one written value and return the messages it adds."""
    written = {**settings.NEXT_FRAMEWORK, path[0]: _nested(path[1:], value)}
    with override_settings(NEXT_FRAMEWORK=written):
        messages = run_checks(include_deployment_checks=True)
    return [m for m in messages if (m.id, m.msg) not in baseline]


class TestOneMistakeOneReport:
    """A value outside its shape reads as the errors of its key, never a traceback."""

    @pytest.mark.parametrize(("path", "value"), [*_scope_cases()])
    def test_a_scope_value_reports_only_its_shape(self, path, value, baseline) -> None:
        added = _added(path, value, baseline)
        codes = SHAPE_CODES | {TOKEN_LIFETIME_CODE}
        assert added
        assert {message.id for message in added} <= codes
        assert all(message.level >= ERROR for message in added)
        assert all(_address(path[:1]) in message.msg for message in added)

    @pytest.mark.parametrize(("path", "value"), [*_top_level_cases()])
    def test_a_top_level_value_reports_only_its_type(
        self, path, value, baseline
    ) -> None:
        added = _added(path, value, baseline)
        assert len(added) == 1
        assert added[0].id in SHAPE_CODES
        assert _address(path) in added[0].msg

    @pytest.mark.parametrize(("path", "value"), [*_dotted_cases()])
    def test_a_dotted_path_that_names_nothing_is_reported(
        self, path, value, baseline
    ) -> None:
        assert _added(path, value, baseline)

    def test_every_scope_with_a_shape_is_probed(self) -> None:
        assert {module.SCOPE for module in _scopes()} == {
            "BOUNDARIES",
            "COMPRESSION",
            "DEFER",
            "LOADER",
            "ROUTER",
            "TRANSACTIONS",
        }
