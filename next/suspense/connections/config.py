"""Reads of the `DEFER` keys job safety follows, a misfit read as its default."""

from collections.abc import Mapping
from typing import Final, Literal, cast

from next.conf.defer import SCOPE, SHAPE, Section, Shape, defer_seconds
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.scopes import scope_value


type DeadlineMode = Literal["state", "always", False]

_EXECUTOR: Final = "EXECUTOR"
_OPTIONS: Final = "OPTIONS"


def _usable(shape: Shape | Section, value: object) -> bool:
    return isinstance(shape, Shape) and shape.usable(value)


def _defer(key: str) -> object:
    value = scope_value(SCOPE, key)
    if _usable(SHAPE.keys[key], value):
        return value
    return FROZEN_DEFAULTS[SCOPE][key]


def _option(key: str) -> object:
    """Return `EXECUTOR["OPTIONS"][key]`, the default where it is missing or misfits."""
    executor = scope_value(SCOPE, _EXECUTOR)
    options = executor.get(_OPTIONS) if isinstance(executor, Mapping) else None
    section = cast("Section", cast("Section", SHAPE.keys[_EXECUTOR]).keys[_OPTIONS])
    if isinstance(options, Mapping) and key in options:
        value = options[key]
        if _usable(section.keys[key], value):
            return value
    return FROZEN_DEFAULTS[SCOPE][_EXECUTOR][_OPTIONS][key]


def backend_paths(key: Literal["THREAD_STATE", "CONNECTION_STATE"]) -> tuple[str, ...]:
    """Return the dotted paths of the `THREAD_STATE` or `CONNECTION_STATE` backends."""
    return tuple(cast("list[str]", _defer(key)))


def deadline_mode() -> DeadlineMode:
    """Return `DEADLINE_TO_DB`, how the job deadline reaches the database."""
    return cast("DeadlineMode", _defer("DEADLINE_TO_DB"))


def timeout_floor() -> float:
    """Return `DB_TIMEOUT_FLOOR` in seconds, the least timeout sent to the database."""
    return defer_seconds("DB_TIMEOUT_FLOOR")


def parallel() -> bool:
    """Return `EXECUTOR["OPTIONS"]["PARALLEL"]`, False keeping jobs on the request."""
    return cast("bool", _option("PARALLEL"))


def sqlite_parallel() -> bool:
    """Whether `EXECUTOR["OPTIONS"]["SQLITE"]` lets SQLite jobs run on the pool."""
    return _option("SQLITE") == "parallel"


def thread_local_middleware() -> tuple[str, ...]:
    """Return the middleware `EXECUTOR["OPTIONS"]["THREAD_LOCAL_MIDDLEWARE"]` lists."""
    return tuple(cast("list[str]", _option("THREAD_LOCAL_MIDDLEWARE")))


__all__ = [
    "DeadlineMode",
    "backend_paths",
    "deadline_mode",
    "parallel",
    "sqlite_parallel",
    "thread_local_middleware",
    "timeout_floor",
]
