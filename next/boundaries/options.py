"""The `DEFER["ERRORS"]` values a boundary reads, held once per settings mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from next.caches import IdentityMemo
from next.conf.defer import atomic_choice, defer_option, defer_read
from next.conf.settings import next_framework_settings
from next.conf.shapes import flag, optional_text


if TYPE_CHECKING:
    from next.transactions import AtomicMode


_KEY: Final = "ERRORS"


@dataclass(frozen=True, slots=True)
class Errors:
    """The `DEFER["ERRORS"]` values of one scope, a misfit read as its default."""

    reraise: bool
    reporter: str | None
    atomic: AtomicMode
    opaque: bool
    detail: bool


def _read_errors() -> Errors:
    return Errors(
        reraise=defer_read(flag, _KEY, "RAISE"),
        reporter=defer_read(optional_text, _KEY, "REPORTER"),
        atomic=defer_read(atomic_choice, _KEY, "ATOMIC"),
        opaque=defer_option(_KEY, "KINDS") == "opaque",
        detail=defer_read(flag, _KEY, "DEBUG_DETAIL"),
    )


_ERRORS: Final[IdentityMemo[Errors]] = IdentityMemo()


def error_settings() -> Errors:
    """Return the `ERRORS` values of the current `DEFER` mapping, read once per one."""
    return _ERRORS.get(next_framework_settings.DEFER, _read_errors)


def errors_opaque() -> bool:
    """Whether `DEFER["ERRORS"]["KINDS"]` hides every kind with a status as `error`."""
    return error_settings().opaque


__all__ = ["Errors", "error_settings", "errors_opaque"]
