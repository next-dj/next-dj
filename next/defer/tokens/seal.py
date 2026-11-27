"""The seals that protect a boundary token, signing it or encrypting it."""

import base64
import importlib
import re
import zlib
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import ClassVar, Final, NoReturn, Protocol, override

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import constant_time_compare, salted_hmac

from next.caches import SETTINGS, memo, pure_cache
from next.defer.errors import BoundaryTokenError
from next.defer.markers import TokenRejection
from next.identity import bound_salt


PURPOSE: Final = "next.defer.boundary"
"""The root of every salt a boundary token uses, apart from every realtime salt."""

FERNET_INFO: Final = "next.defer.boundary.fernet"
"""What `salted_hmac` derives a Fernet key from, the token salt as its key salt."""

_BASE64: Final = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_SIGNED: Final = re.compile(r"\.?[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", re.ASCII)
_SEP: Final = "."
_COMPRESSED: Final = "."

COMPRESS_FROM: Final = 256
"""The payload bytes from which a signed seal tries zlib, below it never paying off."""


def _refuse(reason: TokenRejection) -> NoReturn:
    raise BoundaryTokenError(reason)


def token_salt(version: int, epoch: int, profile: str, subject: str) -> str:
    """Return the salt of one token, its header and its bound subject mixed in."""
    return bound_salt(f"{PURPOSE}.v{version}.{epoch}.{profile}", subject)


def decoded(text: str) -> bytes:
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


@memo(SETTINGS)
def project_keys() -> tuple[str | bytes, ...]:
    """Return `SECRET_KEY` and then `SECRET_KEY_FALLBACKS`, the sealing key first."""
    return (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)


class TokenSeal(ABC):
    """Turn a stored reference into the sealed part of a token and back.

    The salt carries the header and the bound subject, so a seal of one opens no other.
    """

    protects: ClassVar[bool] = True
    """Whether no client can forge the sealed text, which `next.E221` asks for."""

    @classmethod
    def available(cls) -> bool:
        """Whether the library the seal needs imports in this process."""
        return True

    @abstractmethod
    def seal(self, payload: bytes, salt: str) -> str:
        """Return the sealed text of `payload` under the project keys and `salt`."""

    @abstractmethod
    def open(self, sealed: str, salt: str) -> bytes:
        """Return the payload of verified text, else refuse it with a reason."""


def _signature(value: str, salt: str, key: str | bytes) -> str:
    return signing.base64_hmac(f"{salt}signer", value, key, algorithm="sha256")


class SignedSeal(TokenSeal):
    """Sign in Django's `Signer` format, compressed when it saves room.

    The fallback keys verify too, and the claims carry `issued`, so it has no timestamp.
    """

    @override
    def seal(self, payload: bytes, salt: str) -> str:
        data, mark = payload, ""
        if len(payload) >= COMPRESS_FROM:
            compressed = zlib.compress(payload)
            if len(compressed) < len(payload) - 1:
                data, mark = compressed, _COMPRESSED
        value = mark + signing.b64_encode(data).decode()
        return f"{value}{_SEP}{_signature(value, salt, project_keys()[0])}"

    @override
    def open(self, sealed: str, salt: str) -> bytes:
        if not _SIGNED.fullmatch(sealed):
            _refuse(TokenRejection.MALFORMED)
        value, signature = sealed.rsplit(_SEP, 1)
        if not any(
            constant_time_compare(signature, _signature(value, salt, key))
            for key in project_keys()
        ):
            _refuse(TokenRejection.SIGNATURE)
        compressed = value.startswith(_COMPRESSED)
        try:
            data = signing.b64_decode(value.removeprefix(_COMPRESSED).encode())
            return zlib.decompress(data) if compressed else data
        except (ValueError, zlib.error):
            _refuse(TokenRejection.MALFORMED)


class FernetCipher(Protocol):
    """The part of a `Fernet` or `MultiFernet` the encrypted seal calls."""

    def encrypt(self, data: bytes) -> bytes:
        """Return the token that encrypts `data`."""

    def decrypt(self, token: bytes) -> bytes:
        """Return the data of `token`, raising `InvalidToken` for one that fails."""


class FernetLibrary(Protocol):
    """The Fernet surface of `cryptography` the encrypted seal uses."""

    Fernet: Callable[[bytes], FernetCipher]
    MultiFernet: Callable[[Sequence[FernetCipher]], FernetCipher]
    InvalidToken: type[Exception]


@pure_cache()
def crypto_library() -> FernetLibrary | None:
    """Return the Fernet module of `cryptography`, `None` when it does not import."""
    try:
        return importlib.import_module("cryptography.fernet")
    except ImportError:
        return None


def _crypto() -> FernetLibrary:
    library = crypto_library()
    if library is None:
        msg = (
            "EncryptedSeal needs the cryptography package, which does not import. "
            "Install the next-dj[crypto] extra or keep the default SignedSeal."
        )
        raise ImproperlyConfigured(msg)
    return library


def fernet_key(key: str | bytes, salt: str) -> bytes:
    """Return the Fernet key a project secret derives for the tokens of one salt."""
    digest = salted_hmac(salt, FERNET_INFO, secret=key, algorithm="sha256").digest()
    return base64.urlsafe_b64encode(digest)


class EncryptedSeal(TokenSeal):
    """Encrypt with `MultiFernet`, each project key derived per salt into a Fernet key.

    The text is the Fernet token in unpadded base64, its timestamp left unread.
    """

    def __init__(self) -> None:
        """Refuse to build without `cryptography`, so a project learns it at startup."""
        self._library = _crypto()

    @classmethod
    @override
    def available(cls) -> bool:
        return crypto_library() is not None

    @override
    def seal(self, payload: bytes, salt: str) -> str:
        fernet = self._library.Fernet(fernet_key(project_keys()[0], salt))
        token = fernet.encrypt(payload)
        return signing.b64_encode(base64.urlsafe_b64decode(token)).decode()

    @override
    def open(self, sealed: str, salt: str) -> bytes:
        library = self._library
        token = base64.urlsafe_b64encode(decoded(sealed))
        fernets = [library.Fernet(fernet_key(key, salt)) for key in project_keys()]
        try:
            return library.MultiFernet(fernets).decrypt(token)
        except library.InvalidToken:
            _refuse(TokenRejection.SIGNATURE)


__all__ = [
    "COMPRESS_FROM",
    "FERNET_INFO",
    "PURPOSE",
    "EncryptedSeal",
    "FernetCipher",
    "FernetLibrary",
    "SignedSeal",
    "TokenSeal",
    "crypto_library",
    "decoded",
    "fernet_key",
    "project_keys",
    "token_salt",
]
