"""How a contained failure is reported, through `REPORTER` or the framework log."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, cast

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from next.caches import SETTINGS, memo
from next.diagnostics import Capture, FailureLog

from .failures import Boundary, new_digest


if TYPE_CHECKING:
    from next.conf.boundaries import BoundarySettings

    from .failures import ErrorKind


logger = logging.getLogger("next.boundaries")

failure_log = FailureLog(logger)

_REPORTER_SETTING: Final = "NEXT_FRAMEWORK['BOUNDARIES']['REPORTER']"

_LOGGED: Final = (
    "The %s %r failed and rendered its error.djx in place of its subtree, digest %s."
)
_REPORTER_FAILED: Final = (
    "%s %r raised, so the default digest and logging stand in for it."
)
_NO_DIGEST: Final = (
    "%s %r answered %r rather than a digest string, so the default digest and "
    "logging stand in for it."
)


type Reporter = Callable[..., object]


@memo(SETTINGS)
def _reporter(dotted: str) -> Reporter:
    """Return the `REPORTER` callable at `dotted`, imported once per path."""
    try:
        target = import_string(dotted)
    except ImportError as exc:
        msg = f"{_REPORTER_SETTING} {dotted!r} could not be imported: {exc}."
        raise ImproperlyConfigured(msg) from exc
    if not callable(target):
        msg = f"{_REPORTER_SETTING} {dotted!r} names no callable."
        raise ImproperlyConfigured(msg)
    return cast("Reporter", target)


def _reported(
    dotted: str, boundary: Boundary, exc: Exception, kind: ErrorKind
) -> str | None:
    """Return the digest `REPORTER` answers, `None` when it fails or has no reference.

    A digest means the reporter owns the failure, so the framework logs nothing of it.
    """
    capture = Capture()
    with capture:
        reporter = _reporter(dotted)
        answer = reporter(
            exc,
            name=boundary.name,
            addressing=boundary.addressing,
            kind=kind,
            request=boundary.request,
        )
    if capture.error is not None:
        failure_log.emit(
            logging.ERROR,
            ("reporter", dotted),
            _REPORTER_FAILED,
            (_REPORTER_SETTING, dotted),
            capture.error,
        )
        return None
    if answer is None:
        return None
    if isinstance(answer, str) and answer:
        return answer
    failure_log.emit(
        logging.ERROR,
        ("digest", dotted),
        _NO_DIGEST,
        (_REPORTER_SETTING, dotted, answer),
    )
    return None


def report(
    boundary: Boundary, exc: Exception, kind: ErrorKind, errors: BoundarySettings
) -> str:
    """Hand the failure to `REPORTER`, else log it, and return its digest.

    Inside the quiet period the failure is logged at `DEBUG`, so every digest is found.
    """
    if errors.reporter is not None:
        answered = _reported(errors.reporter, boundary, exc, kind)
        if answered is not None:
            return answered
    digest = new_digest()
    failure_log.emit(
        logging.ERROR,
        (boundary.addressing, boundary.name, type(exc).__qualname__),
        _LOGGED,
        (boundary.addressing, boundary.name, digest),
        exc,
        extra={"digest": digest},
        quiet=logging.DEBUG,
    )
    return digest


__all__ = ["Reporter", "failure_log", "report"]
