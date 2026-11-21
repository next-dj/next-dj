"""Where the bytes of boundary claims live, in the token itself or in a cache."""

import hmac
from abc import ABC, abstractmethod
from typing import Final, NoReturn, override

from django.core.cache import caches
from django.utils.crypto import salted_hmac

from next.suspense.errors import BoundaryPropsError, BoundaryTokenError
from next.suspense.markers import TokenRejection

from .context import PURPOSE, TokenContext


INLINE: Final = b"i"
"""The tag of a reference that holds the claims bytes themselves."""

CACHED: Final = b"c"
"""The tag of a reference that holds the cache address of the claims bytes."""

STORE_SALT: Final = f"{PURPOSE}.store"
"""The salt of the content address, which is also the MAC over a cache entry."""

_ADDRESS_BYTES: Final = 32


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


class TokenStorage(ABC):
    """Keep the claims bytes of a token, returning the reference the token seals."""

    @abstractmethod
    def put(self, data: bytes, context: TokenContext) -> bytes:
        """Return the reference of `data`, raising `BoundaryPropsError` for a misfit."""

    @abstractmethod
    def get(self, reference: bytes, context: TokenContext) -> bytes:
        """Return the claims bytes of a verified reference, else raise with a reason."""


class InlineStorage(TokenStorage):
    """Carry the claims bytes in the token, which keeps them public."""

    @override
    def put(self, data: bytes, context: TokenContext) -> bytes:
        return INLINE + data

    @override
    def get(self, reference: bytes, context: TokenContext) -> bytes:
        if not reference.startswith(INLINE):
            _refuse(TokenRejection.MALFORMED)
        return reference[len(INLINE) :]


def content_address(data: bytes, key: str) -> bytes:
    """Return the keyed address of `data`, so equal claims share one cache entry."""
    return salted_hmac(STORE_SALT, data, secret=key, algorithm="sha256").digest()


def cache_key(address: bytes) -> str:
    """Return the cache key one content address is stored under."""
    return f"{PURPOSE}.{address.hex()}"


class CacheStorage(TokenStorage):
    """Keep the claims bytes in the `CACHE_ALIAS` cache, the token naming their MAC.

    Django unpickles an entry before its MAC is read, so trust its writers as sessions'.
    """

    @override
    def put(self, data: bytes, context: TokenContext) -> bytes:
        data = bytes(data)
        if len(data) > context.max_stored_bytes:
            msg = (
                f"the boundary claims take {len(data)} bytes, over MAX_STORED_BYTES "
                f"of {context.max_stored_bytes}"
            )
            raise BoundaryPropsError(msg)
        address = content_address(data, context.keys[0])
        caches[context.cache_alias].set(
            cache_key(address), data, timeout=max(1, context.lifetime)
        )
        return CACHED + address

    @override
    def get(self, reference: bytes, context: TokenContext) -> bytes:
        address = reference[len(CACHED) :]
        if not reference.startswith(CACHED) or len(address) != _ADDRESS_BYTES:
            _refuse(TokenRejection.MALFORMED)
        held = caches[context.cache_alias].get(cache_key(address))
        if held is None:
            _refuse(TokenRejection.STORAGE_MISS)
        if not isinstance(held, bytes):
            _refuse(TokenRejection.SIGNATURE)
        if len(held) > context.max_stored_bytes:
            _refuse(TokenRejection.TOO_LARGE)
        verified = [
            hmac.compare_digest(content_address(held, key), address)
            for key in context.keys
        ]
        if not any(verified):
            _refuse(TokenRejection.SIGNATURE)
        return held


class SpillStorage(CacheStorage):
    """Carry claims inline while the token fits `MAX_BYTES`, the cache the rest."""

    @override
    def put(self, data: bytes, context: TokenContext) -> bytes:
        if len(INLINE) + len(data) <= context.inline_bytes:
            return INLINE + data
        return super().put(data, context)

    @override
    def get(self, reference: bytes, context: TokenContext) -> bytes:
        if reference.startswith(INLINE):
            return reference[len(INLINE) :]
        return super().get(reference, context)


__all__ = [
    "CACHED",
    "INLINE",
    "STORE_SALT",
    "CacheStorage",
    "InlineStorage",
    "SpillStorage",
    "TokenStorage",
    "cache_key",
    "content_address",
]
