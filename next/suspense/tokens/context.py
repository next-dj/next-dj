"""What one mint or redeem seals and stores under, and the purpose strings it uses."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


PURPOSE: Final = "next.defer.boundary"
"""The root of every salt a boundary token uses, apart from every realtime salt."""

_BINDING_LABEL: Final = b"next.defer.binding\x00"

type Lazy[T] = T | Callable[[], T]


def purpose(version: int, epoch: int) -> str:
    """Return the salt of tokens of one claims version under one `EPOCH`."""
    return f"{PURPOSE}.v{version}.{epoch}"


def binding_digest(subject: str) -> str:
    """Return the digest a binding subject enters a seal as, empty for no binding.

    It never leaves the server, so a plain hash keeps the subject out of every token.
    """
    if not subject:
        return ""
    return hashlib.sha256(_BINDING_LABEL + subject.encode()).hexdigest()


@dataclass(slots=True, kw_only=True)
class TokenContext:
    """What one mint or redeem seals and stores under, the subject as a digest only.

    A callable `ring`, `digest` or `room` runs once, when a seam first reads it.
    """

    version: int
    epoch: int
    ring: Lazy[tuple[str, ...]]
    digest: Lazy[str] = ""
    lifetime: int = 0
    max_stored_bytes: int = 65_536
    cache_alias: str = "default"
    room: Lazy[int] = 0

    @property
    def keys(self) -> tuple[str, ...]:
        """Return the key ring, the sealing key first."""
        ring = self.ring
        if callable(ring):
            ring = ring()
            self.ring = ring
        return ring

    @property
    def binding(self) -> str:
        """Return the digest of the bound subject, empty for no binding."""
        digest = self.digest
        if callable(digest):
            digest = digest()
            self.digest = digest
        return digest

    @property
    def inline_bytes(self) -> int:
        """Return the most reference bytes a token seals within its `MAX_BYTES`."""
        room = self.room
        if callable(room):
            room = room()
            self.room = room
        return room

    @property
    def purpose(self) -> str:
        """Return the salt of this version and epoch."""
        return purpose(self.version, self.epoch)


__all__ = ["PURPOSE", "Lazy", "TokenContext", "binding_digest", "purpose"]
