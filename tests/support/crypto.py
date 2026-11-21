import hashlib
import hmac
import json
import sys
import types
from typing import ClassVar

import pytest

from next.suspense.tokens.codec import msgpack_library
from next.suspense.tokens.seal import forget_crypto


CRYPTO_MODULES = (
    "cryptography",
    "cryptography.exceptions",
    "cryptography.hazmat",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf",
    "cryptography.hazmat.primitives.kdf.hkdf",
    "cryptography.hazmat.primitives.hashes",
)
_TAG = 16
_BLOCK = 32


class FakeTagError(Exception):
    """The `cryptography.exceptions.InvalidTag` a failed authentication raises."""


class FakeSHA256:
    """The `hashes.SHA256` algorithm marker."""

    name = "sha256"
    digest_size = 32


def _hmac(key: bytes, *parts: bytes) -> bytes:
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for part in parts:
        mac.update(len(part).to_bytes(4, "big"))
        mac.update(part)
    return mac.digest()


class FakeHKDF:
    """RFC 5869 HKDF over SHA-256, the `cryptography` constructor and `derive`."""

    def __init__(self, algorithm, length: int, salt: bytes | None, info: bytes) -> None:
        """Keep the parameters, refusing an algorithm other than SHA-256."""
        assert isinstance(algorithm, FakeSHA256)
        assert isinstance(info, bytes)
        self.length = length
        self.salt = salt or bytes(algorithm.digest_size)
        self.info = info
        self.used = False

    def derive(self, key_material: bytes) -> bytes:
        if self.used:
            msg = "derive may be called once"
            raise RuntimeError(msg)
        self.used = True
        prk = hmac.new(self.salt, key_material, hashlib.sha256).digest()
        okm, block, counter = b"", b"", 1
        while len(okm) < self.length:
            block = hmac.new(
                prk, block + self.info + bytes([counter]), hashlib.sha256
            ).digest()
            okm += block
            counter += 1
        return okm[: self.length]


class FakeAESGCM:
    """An authenticated cipher with the `AESGCM` surface, a keyed stream and a MAC.

    Every byte of key, nonce, associated data and ciphertext enters the tag, as in GCM.
    """

    sealed: ClassVar[set[tuple[bytes, bytes]]] = set()
    """Every key and nonce encrypted under, so a reuse that breaks GCM fails the test."""

    def __init__(self, key: bytes) -> None:
        """Refuse a key length AES does not take."""
        if len(key) not in {16, 24, 32}:
            msg = "AESGCM key must be 128, 192, or 256 bits."
            raise ValueError(msg)
        self._key = bytes(key)

    def _stream(self, nonce: bytes, size: int) -> bytes:
        blocks = (
            _hmac(self._key, b"stream", nonce, counter.to_bytes(4, "big"))
            for counter in range(size // _BLOCK + 1)
        )
        return b"".join(blocks)[:size]

    def _tag(self, nonce: bytes, cipher: bytes, data: bytes | None) -> bytes:
        return _hmac(self._key, b"tag", nonce, data or b"", cipher)[:_TAG]

    def encrypt(
        self, nonce: bytes, data: bytes, associated_data: bytes | None
    ) -> bytes:
        if not 8 <= len(nonce) <= 128:
            msg = "Nonce must be between 8 and 128 bytes"
            raise ValueError(msg)
        pair = (self._key, bytes(nonce))
        assert pair not in FakeAESGCM.sealed, "a nonce is reused under one key"
        FakeAESGCM.sealed.add(pair)
        cipher = bytes(
            a ^ b for a, b in zip(data, self._stream(nonce, len(data)), strict=True)
        )
        return cipher + self._tag(nonce, cipher, associated_data)

    def decrypt(
        self, nonce: bytes, data: bytes, associated_data: bytes | None
    ) -> bytes:
        if len(data) < _TAG:
            raise FakeTagError
        cipher, tag = data[:-_TAG], data[-_TAG:]
        if not hmac.compare_digest(tag, self._tag(nonce, cipher, associated_data)):
            raise FakeTagError
        return bytes(
            a ^ b for a, b in zip(cipher, self._stream(nonce, len(cipher)), strict=True)
        )


def fake_crypto() -> dict[str, types.ModuleType]:
    """Return every module the encrypted seal imports, with the fakes in place."""
    modules = {name: types.ModuleType(name) for name in CRYPTO_MODULES}
    modules["cryptography.exceptions"].InvalidTag = FakeTagError
    modules["cryptography.hazmat.primitives.ciphers.aead"].AESGCM = FakeAESGCM
    modules["cryptography.hazmat.primitives.kdf.hkdf"].HKDF = FakeHKDF
    modules["cryptography.hazmat.primitives.hashes"].SHA256 = FakeSHA256
    return modules


def install_crypto(monkeypatch: pytest.MonkeyPatch, *, present: bool = True) -> None:
    """Make `cryptography` import as the fake, or refuse it, the probes forgotten."""
    fakes = fake_crypto()
    for name in CRYPTO_MODULES:
        monkeypatch.setitem(sys.modules, name, fakes[name] if present else None)
    FakeAESGCM.sealed.clear()
    forget_crypto()


_PACKED = b"\xc7fake-msgpack:"


def _packb(value: object, *, use_bin_type: bool = True) -> bytes:
    assert use_bin_type
    return _PACKED + json.dumps(value, allow_nan=False).encode()


def _unpackb(data: bytes, *, raw: bool, strict_map_key: bool, use_list: bool) -> object:
    assert not raw
    assert strict_map_key
    assert use_list
    if not data.startswith(_PACKED):
        msg = "unpack(b) received extra data."
        raise ValueError(msg)
    return json.loads(data[len(_PACKED) :])


def install_msgpack(monkeypatch: pytest.MonkeyPatch, *, present: bool = True) -> None:
    """Make `msgpack` import as a fake with `packb` and `unpackb`, or refuse it."""
    module = types.ModuleType("msgpack")
    module.packb = _packb
    module.unpackb = _unpackb
    monkeypatch.setitem(sys.modules, "msgpack", module if present else None)
    msgpack_library.cache_clear()
