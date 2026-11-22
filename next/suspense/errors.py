"""Exceptions of deferred boundaries, their tokens and the lanes that run their jobs."""

from typing import ClassVar, Final

from next.defer import BoundaryOptionError, ErrorKind
from next.suspense.markers import TokenRejection


_RELOADING: Final = frozenset(
    {TokenRejection.EXPIRED, TokenRejection.VERSION, TokenRejection.STORAGE_MISS}
)
_EXPIRED: Final = "The boundary token expired."
_REFUSED: Final = "The boundary token was refused."


class BoundaryPropsError(ValueError):
    """Raised at mint time for an unencodable prop or a token over `MAX_BYTES`."""


class BoundaryBindingError(BoundaryPropsError):
    """Raised at mint time when the visitor proves no subject the binding can hold."""


class BoundaryTokenError(Exception):
    """Raised when a boundary token is refused, `reason` naming why for the logs.

    The message tells an expired token from a refused one, never which check failed.
    """

    def __init__(self, reason: TokenRejection) -> None:
        """Keep the reason the response and `boundary_token_rejected` read."""
        self.reason = reason
        super().__init__(_EXPIRED if reason in _RELOADING else _REFUSED)

    @property
    def expired(self) -> bool:
        """Whether the client answers with a page reload rather than a refusal."""
        return self.reason in _RELOADING


class BoundaryRefusedError(Exception):
    """Raised for a job settled without running, as an error of `kind`."""

    kind: ClassVar[ErrorKind]
    message: ClassVar[str]

    def __init__(self) -> None:
        """Carry the message of the refusal."""
        super().__init__(self.message)


class BoundaryShedError(BoundaryRefusedError):
    """Raised for a job neither the pool queue nor its request thread can take."""

    kind = ErrorKind.OVERLOADED
    message = "The job was shed, as the pool queue is full and its request thread busy."


class BoundaryDeadlineError(BoundaryRefusedError):
    """Raised for a job whose deadline passed before it started."""

    kind = ErrorKind.TIMEOUT
    message = "The deadline of the boundary passed before its job started."


class ExecutorStoppedError(BoundaryRefusedError):
    """Raised for a job still queued when its executor stops."""

    kind = ErrorKind.OVERLOADED
    message = "The executor stopped before the job ran."


__all__ = [
    "BoundaryBindingError",
    "BoundaryDeadlineError",
    "BoundaryOptionError",
    "BoundaryPropsError",
    "BoundaryRefusedError",
    "BoundaryShedError",
    "BoundaryTokenError",
    "ExecutorStoppedError",
]
