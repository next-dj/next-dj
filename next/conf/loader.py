"""The shape of the `LOADER` scope, the batch defaults of every `Loader`."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from next.caches import IdentityMemo
from next.errors import DurationError
from next.utils import parse_duration

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import Section, Shape, count, enforce, pick


SCOPE: Final = "LOADER"

_ONE_UP: Final = count(1)


def _seconds(value: object) -> bool:
    try:
        parse_duration(value)
    except DurationError:
        return False
    return True


SHAPE: Final = Section(
    {
        "WINDOW": Shape(_seconds, "a duration from 0, in seconds or as '2s'"),
        "MAX_BATCH": Shape(_ONE_UP, "an integer from 1"),
    }
)
"""Every `LOADER` key with what it accepts."""


@dataclass(frozen=True, slots=True)
class LoaderSettings:
    """The `LOADER` scope read once, each omitted key at its default."""

    window: float
    max_batch: int


def read_settings(scope: Mapping[str, object]) -> LoaderSettings:
    """Return the settings `scope` asks for, refusing a value outside its shape."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    enforce(f"NEXT_FRAMEWORK[{SCOPE!r}]", scope, SHAPE)
    return LoaderSettings(
        window=parse_duration(scope.get("WINDOW", defaults["WINDOW"])),
        max_batch=pick(scope, "MAX_BATCH", _ONE_UP, defaults),
    )


def _read() -> LoaderSettings:
    """Read the settings of the merged `LOADER` mapping."""
    return read_settings(next_framework_settings.LOADER)


_SETTINGS: Final[IdentityMemo[LoaderSettings]] = IdentityMemo()


def loader_settings() -> LoaderSettings:
    """Return the `LOADER` settings, read again once the merged mapping moves."""
    return _SETTINGS.get(next_framework_settings.LOADER, _read)


__all__ = ["SCOPE", "SHAPE", "LoaderSettings", "loader_settings", "read_settings"]
