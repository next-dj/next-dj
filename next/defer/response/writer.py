"""The frames a stream writes for its batches, built without reading or writing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final

from next.boundaries.failures import ErrorKind
from next.defer.markers import Piece

from .lifetime import release_connections


if TYPE_CHECKING:
    from collections.abc import Iterable

    from .lifetime import Lifetime


type Part = bytes | Piece
"""One frame of a write, or the piece whose frame failed and waits for its rescue."""

_FRAME: Final = "A frame of a stream failed to build, so it shows its error UI instead."


def apart(pieces: Iterable[Piece]) -> list[tuple[Piece, ...]]:
    """Return a batch cut into writes so no chunk shares one with its parent's.

    A target the parent's chunk inserts is missing until that chunk applies.
    """
    writes: list[tuple[Piece, ...]] = []
    held: list[Piece] = []
    ids: set[str] = set()
    for piece in pieces:
        if piece.job.parent in ids:
            writes.append(tuple(held))
            held, ids = [], set()
        held.append(piece)
        ids.add(piece.id)
    if held:
        writes.append(tuple(held))
    return writes


def errored(piece: Piece) -> Piece:
    """Return `piece` settled as an `error`, the error UI of its job in its place."""
    job = piece.job
    return Piece(job, job.error_ui(ErrorKind.ERROR), parent=piece.parent)


class Writer(ABC):
    """What one stream writes for each batch, every frame built here and none sent.

    A pump reads the batches and writes the bytes, and it calls `rescue` and `closing`
    on the request thread, as both render an error UI.
    """

    __slots__ = ("life", "split")

    def __init__(self, life: Lifetime, *, split: bool) -> None:
        """Count into `life`, cutting a batch apart when `split` says so."""
        self.life = life
        self.split = split

    @abstractmethod
    def frame(self, piece: Piece) -> bytes:
        """Return the frame of one settled piece."""

    def feed(self, pieces: tuple[Piece, ...]) -> list[list[Part]]:
        """Return the writes of one batch, a piece whose frame failed left in its place.

        A page stream writes a nested chunk apart from its parent's, a partial one the
        batch whole, so an idle batch makes one empty write.
        """
        self.life.count(pieces)
        parts = apart(pieces) if self.split else (pieces,)
        return [[self._part(piece) for piece in part] for part in parts]

    def _part(self, piece: Piece) -> Part:
        built: bytes | None = None
        with self.life.guard(_FRAME):
            built = self.frame(piece)
        return piece if built is None else built

    def rescue(self, piece: Piece) -> bytes:
        """Return the frame of one piece settled as an `error` with its error UI."""
        self.life.failed += piece.kind is None
        return self.frame(errored(piece))

    def joined(self, write: list[Part]) -> bytes:
        """Return one write as bytes, rescuing a failed frame here."""
        return b"".join(
            part if isinstance(part, bytes) else self.rescue(part) for part in write
        )

    def closing(self) -> bytes:
        """Close the connections the jobs left, then frame the boundaries left unsent.

        It runs on the request thread, so a failed stream still frames each boundary.
        """
        life = self.life
        with life.guard():
            release_connections()
        owed = b""
        if life.broken:
            with life.guard():
                pieces = life.batches.unsettled()
                life.count(pieces)
                owed = self.joined([self._part(piece) for piece in pieces])
        return owed


__all__ = ["Part", "Writer", "apart", "errored"]
