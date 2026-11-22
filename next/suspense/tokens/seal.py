"""The seals that protect a boundary token, signing it or encrypting it."""

import functools
import hashlib
import hmac
import importlib
import re
import secrets
from abc import ABC, abstractmethod
from types import SimpleNamespace
from typing import ClassVar, Final, NoReturn, cast, override

from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import salted_hmac

from next.suspense.errors import BoundaryTokenError
from next.suspense.markers import TokenRejection

from .context import PURPOSE, TokenContext


_BASE64: Final = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_SIGNED: Final = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", re.ASCII)
_SEP: Final = "."
_MAC_CHARS: Final = 43
_SIGNER: Final = "signer"
_MAC_KEYS: Final = 1024

ENCRYPTION_INFO: Final = b"next.defer.boundary.enc"
"""The HKDF `info` prefix of every per-token key, the purpose of the token after it."""

KID_SALT: Final = "next.defer.kid"
"""The salt of the key id an encrypted token carries, naming no project secret."""

_KEY_BYTES: Final = 32
_KID_BYTES: Final = 4
_SALT_BYTES: Final = 16
_TAG_BYTES: Final = 16
_HEAD: Final = _KID_BYTES + _SALT_BYTES
_NONCE: Final = bytes(12)


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


def encoded_length(size: int) -> int:
    """Return the characters unpadded URL-safe base64 writes `size` bytes in."""
    return (4 * size + 2) // 3


def _decoded(text: str) -> bytes:
    """Return the bytes of canonical unpadded URL-safe base64, else refuse the text.

    Only the encoding of the bytes passes, so no two strings open as one token.
    """
    if not _BASE64.fullmatch(text):
        _refuse(TokenRejection.MALFORMED)
    try:
        data = signing.b64_decode(text.encode())
    except ValueError:
        _refuse(TokenRejection.MALFORMED)
    if signing.b64_encode(data).decode() != text:
        _refuse(TokenRejection.MALFORMED)
    return data


class TokenSeal(ABC):
    """Turn a stored reference into the sealed part of a token and back.

    `open` refuses text of the wrong shape before it reads the keys or the binding.
    """

    encrypts: ClassVar[bool] = False
    """Whether tokens differ on every mint, which `BUCKET` defeats."""

    @classmethod
    def available(cls) -> bool:
        """Whether the library the seal needs imports in this process."""
        return True

    def length(self, size: int, context: TokenContext) -> int:
        """Return the characters the sealed text of `size` reference bytes takes.

        The default seals that many zero bytes, the shipped seals count instead.
        """
        return len(self.seal(bytes(size), context))

    @abstractmethod
    def seal(self, payload: bytes, context: TokenContext) -> str:
        """Return the sealed text of `payload` under the context's keys and salt."""

    @abstractmethod
    def open(self, sealed: str, context: TokenContext) -> bytes:
        """Return the payload of verified text, else refuse it with a reason."""


class NoSeal(TokenSeal):
    """Seal nothing, for tests only, which `next.E221` refuses outside `DEBUG`."""

    @override
    def length(self, size: int, context: TokenContext) -> int:
        return encoded_length(size)

    @override
    def seal(self, payload: bytes, context: TokenContext) -> str:
        return signing.b64_encode(payload).decode()

    @override
    def open(self, sealed: str, context: TokenContext) -> bytes:
        return _decoded(sealed)


def signed_salt(context: TokenContext) -> str:
    """Return the signer salt, the binding digest mixed in and never sent."""
    if not context.binding:
        return context.purpose
    return f"{context.purpose}.{context.binding}"


@functools.lru_cache(maxsize=_MAC_KEYS)
def _mac_key(salt: str, key: str) -> bytes:
    """Return the HMAC key Django's `Signer` derives from a ring key under `salt`.

    A salt repeats for every token of one visitor, so its key is derived once.
    """
    return hashlib.sha256(f"{salt}{_SIGNER}{key}".encode()).digest()


def _mac(salt: str, key: str, body: str) -> bytes:
    """Return the encoded MAC of `body`, the one a SHA-256 `Signer` writes."""
    digest = hmac.digest(_mac_key(salt, key), body.encode(), "sha256")
    return signing.b64_encode(digest)


class SignedSeal(TokenSeal):
    """Sign as Django's `Signer` does under the first key, every ring key verifying.

    The claims carry their own `issued`, so the signature holds no timestamp.
    """

    @override
    def length(self, size: int, context: TokenContext) -> int:
        return encoded_length(size) + len(_SEP) + _MAC_CHARS

    @override
    def seal(self, payload: bytes, context: TokenContext) -> str:
        body = signing.b64_encode(payload).decode()
        mac = _mac(signed_salt(context), context.keys[0], body)
        return f"{body}{_SEP}{mac.decode()}"

    @override
    def open(self, sealed: str, context: TokenContext) -> bytes:
        if not _SIGNED.fullmatch(sealed):
            _refuse(TokenRejection.MALFORMED)
        body = sealed[: -_MAC_CHARS - len(_SEP)]
        payload = _decoded(body)
        salt = signed_salt(context)
        held = sealed[-_MAC_CHARS:].encode()
        for key in context.keys:
            if hmac.compare_digest(_mac(salt, key, body), held):
                return payload
        _refuse(TokenRejection.SIGNATURE)


@functools.cache
def crypto_library() -> SimpleNamespace | None:
    """Return the `cryptography` parts the encrypted seal uses, `None` without them."""
    try:
        aead = importlib.import_module("cryptography.hazmat.primitives.ciphers.aead")
        hkdf = importlib.import_module("cryptography.hazmat.primitives.kdf.hkdf")
        hashes = importlib.import_module("cryptography.hazmat.primitives.hashes")
        exceptions = importlib.import_module("cryptography.exceptions")
    except ImportError:
        return None
    return SimpleNamespace(
        AESGCM=aead.AESGCM,
        HKDF=hkdf.HKDF,
        SHA256=hashes.SHA256,
        InvalidTag=exceptions.InvalidTag,
    )


def _crypto() -> SimpleNamespace:
    library = crypto_library()
    if library is None:
        msg = (
            "EncryptedSeal needs the cryptography package, which does not import. "
            "Install the next-dj[crypto] extra or keep the default SignedSeal."
        )
        raise ImproperlyConfigured(msg)
    return library


@functools.lru_cache(maxsize=16)
def key_id(key: str) -> bytes:
    """Return the id an encrypted token names its ring key by, a MAC that hides it."""
    mac = salted_hmac(KID_SALT, b"kid", secret=key, algorithm="sha256")
    return mac.digest()[:_KID_BYTES]


def forget_crypto() -> None:
    """Drop the derived keys and the library probe, so the next use reads them anew."""
    key_id.cache_clear()
    _mac_key.cache_clear()
    crypto_library.cache_clear()


def encryption_info(context: TokenContext) -> bytes:
    """Return the HKDF `info` of a token's key, its purpose after the fixed prefix."""
    return ENCRYPTION_INFO + b"|" + context.purpose.encode()


def token_key(
    library: SimpleNamespace, key: str, salt: bytes, context: TokenContext
) -> bytes:
    """Return the AES key of one token, HKDF over a ring key and the token's salt."""
    hkdf = library.HKDF(
        algorithm=library.SHA256(),
        length=_KEY_BYTES,
        salt=salt,
        info=encryption_info(context),
    )
    return cast("bytes", hkdf.derive(key.encode()))


def associated_data(context: TokenContext) -> bytes:
    """Return what the cipher authenticates beside the payload, the binding included."""
    return f"{PURPOSE}|{context.version}|{context.epoch}|{context.binding}".encode()


class EncryptedSeal(TokenSeal):
    """Encrypt with AES-GCM under a key HKDF derives per token from a ring key.

    The text is `kid | salt | ciphertext | tag`, and a fresh key lets the nonce be zero.
    """

    encrypts = True

    def __init__(self) -> None:
        """Refuse to build without `cryptography`, so a project learns it at startup."""
        self._library = _crypto()

    @classmethod
    @override
    def available(cls) -> bool:
        return crypto_library() is not None

    @override
    def length(self, size: int, context: TokenContext) -> int:
        return encoded_length(_HEAD + size + _TAG_BYTES)

    @override
    def seal(self, payload: bytes, context: TokenContext) -> str:
        library = self._library
        key = context.keys[0]
        salt = secrets.token_bytes(_SALT_BYTES)
        cipher = library.AESGCM(token_key(library, key, salt, context)).encrypt(
            _NONCE, payload, associated_data(context)
        )
        return signing.b64_encode(key_id(key) + salt + cipher).decode()

    @override
    def open(self, sealed: str, context: TokenContext) -> bytes:
        library = self._library
        blob = _decoded(sealed)
        if len(blob) < _HEAD + _TAG_BYTES:
            _refuse(TokenRejection.MALFORMED)
        kid, salt, cipher = blob[:_KID_BYTES], blob[_KID_BYTES:_HEAD], blob[_HEAD:]
        data = associated_data(context)
        for key in context.keys:
            if not hmac.compare_digest(key_id(key), kid):
                continue
            aead = library.AESGCM(token_key(library, key, salt, context))
            try:
                return cast("bytes", aead.decrypt(_NONCE, cipher, data))
            except library.InvalidTag:
                continue
        _refuse(TokenRejection.SIGNATURE)


__all__ = [
    "ENCRYPTION_INFO",
    "KID_SALT",
    "EncryptedSeal",
    "NoSeal",
    "SignedSeal",
    "TokenSeal",
    "associated_data",
    "crypto_library",
    "encoded_length",
    "encryption_info",
    "forget_crypto",
    "key_id",
    "signed_salt",
    "token_key",
]
