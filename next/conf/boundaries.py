"""The shape of the `BOUNDARIES` scope and its values, read once per mapping."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from next.caches import IdentityMemo

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings
from .shapes import Section, Shape, choice, enforce, flag, optional_text, pick
from .transactions import ATOMIC, atomic_choice


SCOPE: Final = "BOUNDARIES"

_FLAG: Final = Shape(flag, "True or False")
_TEMPLATE: Final = Shape(optional_text, "a template path or None")
_OPAQUE: Final = "opaque"

SHAPE: Final = Section(
    {
        "LOADING_TEMPLATE": _TEMPLATE,
        "ERROR_TEMPLATE": _TEMPLATE,
        "RAISE": _FLAG,
        "REPORTER": Shape(optional_text, "a dotted path or None"),
        "ATOMIC": ATOMIC,
        "KINDS": choice(("detailed", _OPAQUE)),
        "DEBUG_DETAIL": _FLAG,
    }
)
"""Every `BOUNDARIES` key with what it accepts."""


@dataclass(frozen=True, slots=True)
class BoundarySettings:
    """The `BOUNDARIES` scope read once, each omitted key at its default."""

    loading_template: str | None
    error_template: str | None
    reraise: bool
    reporter: str | None
    atomic: bool | Literal["writes"]
    opaque: bool
    detail: bool


def read_settings(scope: Mapping[str, object]) -> BoundarySettings:
    """Return the settings `scope` asks for, refusing a value outside its shape."""
    defaults: Mapping[str, Any] = FROZEN_DEFAULTS[SCOPE]
    enforce(f"NEXT_FRAMEWORK[{SCOPE!r}]", scope, SHAPE)
    return BoundarySettings(
        loading_template=pick(scope, "LOADING_TEMPLATE", optional_text, defaults),
        error_template=pick(scope, "ERROR_TEMPLATE", optional_text, defaults),
        reraise=pick(scope, "RAISE", flag, defaults),
        reporter=pick(scope, "REPORTER", optional_text, defaults),
        atomic=pick(scope, "ATOMIC", atomic_choice, defaults),
        opaque=scope.get("KINDS", defaults["KINDS"]) == _OPAQUE,
        detail=pick(scope, "DEBUG_DETAIL", flag, defaults),
    )


def _read() -> BoundarySettings:
    """Read the settings of the merged `BOUNDARIES` mapping."""
    return read_settings(next_framework_settings.BOUNDARIES)


_SETTINGS: Final[IdentityMemo[BoundarySettings]] = IdentityMemo()


def boundary_settings() -> BoundarySettings:
    """Return the `BOUNDARIES` settings, read again once the merged mapping moves."""
    return _SETTINGS.get(next_framework_settings.BOUNDARIES, _read)


__all__ = ["SCOPE", "SHAPE", "BoundarySettings", "boundary_settings", "read_settings"]
