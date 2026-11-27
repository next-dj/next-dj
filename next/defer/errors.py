"""Exceptions of deferred boundaries, their tokens and the lanes that run their jobs."""

from typing import ClassVar, Final

from next.boundaries.failures import ErrorKind

from .markers import TokenRejection


_RELOADING: Final = frozenset(
    {
        TokenRejection.EXPIRED,
        TokenRejection.VERSION,
        TokenRejection.STORAGE_MISS,
        TokenRejection.STORAGE_DOWN,
    }
)
_EXPIRED: Final = "The boundary token expired."
_REFUSED: Final = "The boundary token was refused."
_DETAILS: Final = {
    TokenRejection.MALFORMED: "its text is not one the token backend mints",
    TokenRejection.SIGNATURE: (
        "no key of the ring verifies it, so KEYS changed or it was forged"
    ),
    TokenRejection.EXPIRED: "it outlived its MAX_AGE, or EPOCH was raised since",
    TokenRejection.VERSION: "it was minted under another version of the claims",
    TokenRejection.BINDING: "the visitor no longer proves the subject it is bound to",
    TokenRejection.URL_MISMATCH: (
        "it names another component, scope or URL than the request reached"
    ),
    TokenRejection.TOO_LARGE: "it is longer than MAX_BYTES",
    TokenRejection.STORAGE_MISS: "its claims are gone from the token storage",
    TokenRejection.STORAGE_DOWN: "the token storage failed to answer",
}


class BoundaryError(Exception):
    """The root of every exception a deferred boundary or its token raises."""


class BoundaryMintError(BoundaryError):
    """The base of mint-time refusals, each leaving the boundary without a token."""


class BoundaryPropsError(BoundaryMintError, ValueError):
    """Raised at mint time for a prop no token carries."""


class BoundarySizeError(BoundaryPropsError):
    """Raised at mint time when the values of one request outgrow a token limit."""


class BoundaryValueError(BoundaryPropsError):
    """Raised at mint time for a number of a carried type that is not finite."""


class BoundaryBindingError(BoundaryMintError):
    """Raised at mint time when the visitor proves no subject the binding can hold."""


class BoundaryStorageError(BoundaryMintError):
    """Raised at mint time when the token storage fails to keep the claims."""


class BoundaryTokenError(BoundaryError):
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

    @property
    def detail(self) -> str:
        """Return the reason with what usually causes it, for logs and `DEBUG` alone."""
        return f"{self.reason}, {_DETAILS[self.reason]}"


class BoundaryRefusedError(BoundaryError):
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


class ProtocolError(RuntimeError):
    """Raised when a driver breaks the event protocol of the response machine."""


class InvariantError(ProtocolError):
    """Raised when the response machine finds its own bookkeeping broken."""


__all__ = [
    "BoundaryBindingError",
    "BoundaryDeadlineError",
    "BoundaryError",
    "BoundaryMintError",
    "BoundaryPropsError",
    "BoundaryRefusedError",
    "BoundaryShedError",
    "BoundarySizeError",
    "BoundaryStorageError",
    "BoundaryTokenError",
    "BoundaryValueError",
    "ExecutorStoppedError",
    "InvariantError",
    "ProtocolError",
]
