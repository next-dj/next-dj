"""What a boundary contains, the error it settles as, its error UI and its trace."""

from __future__ import annotations

import enum
import secrets
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from django.conf import settings
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError

from next.conf.boundaries import boundary_settings
from next.diagnostics import INTENDED_EXCEPTIONS
from next.ports import static_assets_slot
from next.utils import is_within, resolve_base_dir


if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.static.collector import StaticCollector

    from .calls import Addressing


_BAD_REQUESTS: Final = (BadRequest, SuspiciousOperation, MultiPartParserError)
_DIGEST_BYTES: Final = 6


class ErrorKind(enum.StrEnum):
    """Why a boundary settled as an error, the last two raised only by the client."""

    ERROR = "error"
    TIMEOUT = "timeout"
    OVERLOADED = "overloaded"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    BAD_REQUEST = "bad_request"
    INCOMPLETE = "incomplete"
    NETWORK = "network"

    @property
    def status(self) -> int | None:
        """Return the HTTP status this kind stands for, `None` for an unexpected one."""
        return _STATUSES.get(self)


_STATUSES: Final[Mapping[ErrorKind, int]] = {
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.BAD_REQUEST: 400,
    ErrorKind.OVERLOADED: 503,
}


@dataclass(frozen=True, slots=True)
class BoundaryFailure:
    """The `error` an `error.djx` reads, strings and integers only, never an exception.

    Outside `DEBUG` it has no `message` or `traceback`, which a template reads as empty.
    """

    kind: ErrorKind
    digest: str
    status: int | None
    retry: bool

    @staticmethod
    def of(
        kind: ErrorKind, digest: str, *, retry: bool, message: str, traceback: str
    ) -> BoundaryFailure:
        """Return the error of `kind`, with its message and traceback under `DEBUG`."""
        if settings.DEBUG:
            return DebugBoundaryFailure(
                kind, digest, kind.status, retry, message, traceback
            )
        return BoundaryFailure(kind, digest, kind.status, retry)


@dataclass(frozen=True, slots=True)
class DebugBoundaryFailure(BoundaryFailure):
    """A `BoundaryFailure` with the message and the traceback a `DEBUG` page shows."""

    message: str = field(repr=False)
    traceback: str = field(repr=False)


def errors_opaque() -> bool:
    """Whether `BOUNDARIES["KINDS"]` hides every kind with a status as `error`."""
    return boundary_settings().opaque


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


class ErrorUI(Protocol):
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


def retry_loads() -> bool:
    """Return whether the runtime script loads, which every retry needs."""
    return static_assets_slot.get().runtime_loads()


def unrun_error(kind: ErrorKind) -> BoundaryFailure:
    """Return the error a boundary settles as without running, under its shown kind."""
    shown = shown_kind(kind, opaque=errors_opaque())
    return BoundaryFailure.of(
        shown, new_digest(), retry=retry_loads(), message="", traceback=""
    )


def shown_source(source: Path) -> str:
    """Return `source` relative to `BASE_DIR` when it lies inside, as DEBUG shows it."""
    base = resolve_base_dir()
    if base is not None and is_within(source, base):
        return str(source.relative_to(base))
    return str(source)


__all__ = [
    "Boundary",
    "BoundaryFailure",
    "DebugBoundaryFailure",
    "ErrorKind",
    "ErrorUI",
    "Trace",
    "contained",
    "errors_opaque",
    "kind_of",
    "new_digest",
    "retry_loads",
    "shown_kind",
    "shown_source",
    "unrun_error",
]
