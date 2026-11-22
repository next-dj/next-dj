"""What a boundary contains, its fallback, and the trace and kind of a failure."""

from __future__ import annotations

import secrets
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError

from next.defer import Addressing, BoundaryFailure, ErrorKind
from next.diagnostics import INTENDED_EXCEPTIONS


if TYPE_CHECKING:
    from collections.abc import Hashable

    from django.http import HttpRequest

    from next.static.collector import StaticCollector


_BAD_REQUESTS: Final = (BadRequest, SuspiciousOperation, MultiPartParserError)
_DIGEST_BYTES: Final = 6


@dataclass(frozen=True, slots=True)
class Boundary:
    """One error boundary as a render meets it, named as `boundary_failed` names it.

    `owner` tells boundaries of one name apart, and `ident` is the id of a wrapper drawn
    around it, which its failure reports instead of a fresh one.
    """

    name: str
    addressing: Addressing
    request: HttpRequest | None = None
    collector: StaticCollector | None = None
    started: bool = False
    owner: Hashable | None = None
    site: str = ""
    source: str | None = None
    ident: str | None = None


class Fallback(Protocol):
    """What a boundary shows in place of a failed subtree, asked after the failure."""

    __slots__ = ()

    def retry(self) -> bool:
        """Return whether a `data-next-retry` inside the error UI finds a target."""

    def retry_token(self, error: BoundaryFailure) -> str:
        """Return the token attributes the failed wrapper retries with, else nothing."""

    def tag(self) -> str | None:
        """Return the wrapper element, `None` where no element may wrap the error UI."""

    def render(self, error: BoundaryFailure, kind: ErrorKind) -> str:
        """Render the error UI, `kind` being the kind before `KINDS` hides it."""


@dataclass(frozen=True, slots=True)
class Trace:
    """The exception line and the formatted traceback of one contained failure."""

    line: str
    text: str

    @classmethod
    def of(cls, exc: BaseException) -> Trace:
        """Return the trace of `exc`, formatted once for every reader."""
        line = "".join(traceback.format_exception_only(exc)).strip()
        return cls(line, "".join(traceback.format_exception(exc)))


def contained(exc: BaseException, *, started: bool = False) -> bool:
    """Return whether a boundary contains `exc` rather than letting it propagate.

    Before the first byte Django answers the intended exceptions with their own status.
    """
    if not isinstance(exc, Exception) or isinstance(exc, RequestAborted):
        return False
    return started or not isinstance(exc, INTENDED_EXCEPTIONS)


def new_digest() -> str:
    """Return a fresh digest, which ties an error UI to its line in the log."""
    return secrets.token_hex(_DIGEST_BYTES)


def kind_of(exc: BaseException) -> ErrorKind:
    """Return the kind a contained exception settles a boundary as."""
    if isinstance(exc, Http404):
        return ErrorKind.NOT_FOUND
    if isinstance(exc, PermissionDenied):
        return ErrorKind.FORBIDDEN
    if isinstance(exc, _BAD_REQUESTS):
        return ErrorKind.BAD_REQUEST
    return ErrorKind.ERROR


def shown_kind(kind: ErrorKind, *, opaque: bool) -> ErrorKind:
    """Return the kind the error UI reads, every kind with a status reading `error`."""
    if opaque and kind.status is not None:
        return ErrorKind.ERROR
    return kind


__all__ = [
    "Boundary",
    "Fallback",
    "Trace",
    "contained",
    "kind_of",
    "new_digest",
    "shown_kind",
]
