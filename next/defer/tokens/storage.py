"""Where the bytes of boundary claims live, in the token itself or in a cache."""

import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, NoReturn, override

from django.core.cache import caches
from django.utils.crypto import salted_hmac

from next.defer.errors import (
    BoundarySizeError,
    BoundaryStorageError,
    BoundaryTokenError,
)
from next.defer.markers import TokenRejection

from .seal import PURPOSE, project_keys


INLINE: Final = b"i"
"""The tag of a reference that holds the claims bytes themselves."""

CACHED: Final = b"c"
"""The tag of a reference that holds the cache address of the claims bytes."""

STORE_SALT: Final = f"{PURPOSE}.store"
"""The salt of the content address, which is also the MAC over a cache entry."""

_ADDRESS_BYTES: Final = 32


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


@dataclass(frozen=True, slots=True)
class StorageTerms:
    """The limits one mint or redeem keeps claims under, known before a seam runs."""

    max_stored_bytes: int
    cache_alias: str
    lifetime: int = 0


class TokenStorage(ABC):
    """Keep the claims bytes of a token, returning the reference the token seals."""

    inline: ClassVar[bool] = False
    """Whether every token carries its claims, so `MAX_BYTES` caps a prop."""

    @abstractmethod
    def put(self, data: bytes, terms: StorageTerms) -> bytes:
        """Return the reference of `data`, raising `BoundarySizeError` past its cap."""

    @abstractmethod
    def get(self, reference: bytes, terms: StorageTerms) -> bytes:
        """Return the claims bytes of a verified reference, else raise with a reason."""

    def overflow(self, data: bytes, terms: StorageTerms) -> bytes | None:
        """Return the reference of claims whose token outgrew `MAX_BYTES`, `None` here.

        `None` lets the mint refuse the token, which renders the boundary in place.
        """
        del data, terms
        return None


class InlineStorage(TokenStorage):
    """Carry the claims bytes in the token, which keeps them public."""

    inline = True

    @override
    def put(self, data: bytes, terms: StorageTerms) -> bytes:
        return INLINE + data

    @override
    def get(self, reference: bytes, terms: StorageTerms) -> bytes:
        if not reference.startswith(INLINE):
            _refuse(TokenRejection.MALFORMED)
        return reference[len(INLINE) :]


def content_address(data: bytes, key: str | bytes) -> bytes:
    """Return the keyed address of `data`, so equal claims share one cache entry."""
    return salted_hmac(STORE_SALT, data, secret=key, algorithm="sha256").digest()


def cache_key(address: bytes) -> str:
    """Return the cache key one content address is stored under."""
    return f"{PURPOSE}.{address.hex()}"


class CacheStorage(TokenStorage):
    """Keep the claims bytes in the `CACHE_ALIAS` cache, the token naming their MAC.

    Django unpickles an entry before its MAC is read, so trust its writers as sessions'.
    A failed cache call raises a boundary error, as cache clients share no base error.
    """

    @override
    def put(self, data: bytes, terms: StorageTerms) -> bytes:
        data = bytes(data)
        if len(data) > terms.max_stored_bytes:
            msg = (
                f"the boundary claims take {len(data)} bytes, over MAX_STORED_BYTES "
                f"of {terms.max_stored_bytes}"
            )
            raise BoundarySizeError(msg)
        address = content_address(data, project_keys()[0])
        cache = caches[terms.cache_alias]
        try:
            cache.set(cache_key(address), data, timeout=max(1, terms.lifetime))
        except Exception as exc:
            msg = f"the {terms.cache_alias!r} cache failed to keep the claims: {exc}"
            raise BoundaryStorageError(msg) from exc
        return CACHED + address

    @override
    def get(self, reference: bytes, terms: StorageTerms) -> bytes:
        address = reference[len(CACHED) :]
        if not reference.startswith(CACHED) or len(address) != _ADDRESS_BYTES:
            _refuse(TokenRejection.MALFORMED)
        cache = caches[terms.cache_alias]
        try:
            held = cache.get(cache_key(address))
        except Exception as exc:
            raise BoundaryTokenError(TokenRejection.STORAGE_DOWN) from exc
        if held is None:
            _refuse(TokenRejection.STORAGE_MISS)
        if not isinstance(held, bytes):
            _refuse(TokenRejection.SIGNATURE)
        if len(held) > terms.max_stored_bytes:
            _refuse(TokenRejection.TOO_LARGE)
        verified = [
            hmac.compare_digest(content_address(held, key), address)
            for key in project_keys()
        ]
        if not any(verified):
            _refuse(TokenRejection.SIGNATURE)
        return held


class SpillStorage(CacheStorage):
    """Carry claims inline while the token fits `MAX_BYTES`, the cache the rest."""

    @override
    def put(self, data: bytes, terms: StorageTerms) -> bytes:
        return INLINE + data

    @override
    def overflow(self, data: bytes, terms: StorageTerms) -> bytes:
        return super().put(data, terms)

    @override
    def get(self, reference: bytes, terms: StorageTerms) -> bytes:
        if reference.startswith(INLINE):
            return reference[len(INLINE) :]
        return super().get(reference, terms)


__all__ = [
    "CACHED",
    "INLINE",
    "STORE_SALT",
    "CacheStorage",
    "InlineStorage",
    "SpillStorage",
    "StorageTerms",
    "TokenStorage",
    "cache_key",
    "content_address",
]
