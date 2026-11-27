import base64
import hashlib
import hmac
import os
import struct
import sys
import time
import types

import pytest

from next.defer.tokens.seal import crypto_library


CRYPTO_MODULES = ("cryptography", "cryptography.fernet")
_VERSION = b"\x80"
_BLOCK = 16
_MAC = 32


class FakeInvalidTokenError(Exception):
    """The `cryptography.fernet.InvalidToken` a failed decryption raises."""


def _stream(key: bytes, iv: bytes, size: int) -> bytes:
    blocks = (
        hmac.new(key, iv + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        for counter in range(size // _MAC + 1)
    )
    return b"".join(blocks)[:size]


class FakeFernet:
    """A Fernet with the token layout of the real one, a keyed stream for the cipher.

    Version, timestamp, IV, padded ciphertext and HMAC take the bytes Fernet's do.
    """

    def __init__(self, key: bytes) -> None:
        """Split the key as Fernet does, refusing one of the wrong length."""
        raw = base64.urlsafe_b64decode(key)
        if len(raw) != _MAC:
            msg = "Fernet key must be 32 url-safe base64-encoded bytes."
            raise ValueError(msg)
        self._signing, self._encryption = raw[:_BLOCK], raw[_BLOCK:]

    def encrypt(self, data: bytes) -> bytes:
        iv = os.urandom(_BLOCK)
        pad = _BLOCK - len(data) % _BLOCK
        padded = data + bytes([pad]) * pad
        cipher = bytes(
            a ^ b
            for a, b in zip(
                padded, _stream(self._encryption, iv, len(padded)), strict=True
            )
        )
        body = _VERSION + struct.pack(">Q", int(time.time())) + iv + cipher
        mac = hmac.new(self._signing, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body + mac)

    def decrypt(self, token: bytes) -> bytes:
        try:
            raw = base64.urlsafe_b64decode(token)
        except ValueError as exc:
            raise FakeInvalidTokenError from exc
        body, mac = raw[:-_MAC], raw[-_MAC:]
        expected = hmac.new(self._signing, body, hashlib.sha256).digest()
        if len(raw) < 1 + 8 + _BLOCK + _MAC or not hmac.compare_digest(mac, expected):
            raise FakeInvalidTokenError
        iv, cipher = body[9 : 9 + _BLOCK], body[9 + _BLOCK :]
        padded = bytes(
            a ^ b
            for a, b in zip(
                cipher, _stream(self._encryption, iv, len(cipher)), strict=True
            )
        )
        return padded[: -padded[-1]]


class FakeMultiFernet:
    """Encrypt under the first Fernet and decrypt under any, as `MultiFernet` does."""

    def __init__(self, fernets: list[FakeFernet]) -> None:
        """Keep the Fernets in order."""
        self._fernets = list(fernets)

    def encrypt(self, data: bytes) -> bytes:
        return self._fernets[0].encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        for fernet in self._fernets:
            try:
                return fernet.decrypt(token)
            except FakeInvalidTokenError:
                continue
        raise FakeInvalidTokenError


def install_crypto(monkeypatch: pytest.MonkeyPatch, *, present: bool = True) -> None:
    """Make `cryptography.fernet` import as the fake, or refuse it, the probe reset."""
    package = types.ModuleType("cryptography")
    fernet = types.ModuleType("cryptography.fernet")
    fernet.Fernet = FakeFernet
    fernet.MultiFernet = FakeMultiFernet
    fernet.InvalidToken = FakeInvalidTokenError
    fakes = {"cryptography": package, "cryptography.fernet": fernet}
    for name in CRYPTO_MODULES:
        monkeypatch.setitem(sys.modules, name, fakes[name] if present else None)
    crypto_library.cache_clear()
