import zlib

import pytest
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.defer import BoundaryTokenError, TokenRejection
from next.defer.tokens import VERSION, EncryptedSeal, SignedSeal, TokenSeal
from next.defer.tokens.seal import (
    COMPRESS_FROM,
    FERNET_INFO,
    PURPOSE,
    crypto_library,
    decoded,
    fernet_key,
    token_salt,
)
from next.identity import bound_salt
from next.testing import NoSeal
from tests.support.crypto import install_crypto
from tests.support.tokens import KEY, OLD_KEY, b64decode, b64encode, flip, flip_bit


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_SALT = f"{PURPOSE}.v{VERSION}.1.0"
_SIGNED_VECTOR = "aXt9.zcOM2pyfa0NVSSRpXTdHrIoYzNC8TLc7zqB9pMi6Zug"
_COMPRESSED_VECTOR = ".eJzLTKQAAADOxhcm.x6CSRmGCWnSDOR5zZ82tEyblnf5bhfLVJl0NG_3hlnY"
_FERNET_KEY_VECTOR = b"jG2QcYbGRKHkPh91G8hXX8Wls-DSdt3mgUk_sZJksZs="
_FERNET_VECTOR = (
    "gAAAAAAAD0JA0hfYEtHILIFLL6Elr1NJ0p7uGKchDnyB10dNTBa7KSJNJG65gIvC2WVqckyCauvAgFI"
    "qAz0zSKVjLfy5zEyDBg"
)


class _Bytes:
    def dumps(self, obj: bytes) -> bytes:
        return obj

    def loads(self, data: bytes) -> bytes:
        return data


def _reason(seal: TokenSeal, sealed: str, salt: str = _SALT) -> TokenRejection:
    with pytest.raises(BoundaryTokenError) as caught:
        seal.open(sealed, salt)
    return caught.value.reason


def _twin(text: str) -> str:
    """Return another base64 text that decodes to the bytes of `text`."""
    for char in _ALPHABET:
        twin = text[:-1] + char
        if twin != text and b64decode(twin) == b64decode(text):
            return twin
    msg = f"{text!r} leaves no unused bits in its last character"
    raise AssertionError(msg)


@pytest.fixture(autouse=True)
def _keys():
    with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[]):
        yield


@pytest.fixture()
def crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    install_crypto(monkeypatch)


class TestSalt:
    """The salt names the claims version, the epoch, the profile and the subject."""

    def test_the_header_enters_the_salt(self) -> None:
        assert token_salt(2, 7, "0", "") == "next.defer.boundary.v2.7.0"

    def test_the_subject_enters_as_a_digest(self) -> None:
        salt = token_salt(2, 7, "0", "session:abc")
        assert salt == bound_salt("next.defer.boundary.v2.7.0", "session:abc")
        assert "abc" not in salt
        assert salt != token_salt(2, 7, "0", "session:xyz")


class TestCanonicalText:
    """Only the canonical encoding of the bytes opens, so no two strings are one token."""

    def test_a_twin_of_the_last_character_is_malformed(self) -> None:
        text = NoSeal().seal(b"ix", _SALT)
        assert _reason(NoSeal(), _twin(text)) is TokenRejection.MALFORMED

    @pytest.mark.parametrize("text", ["", "a=b", "a b"])
    def test_text_outside_the_alphabet_is_malformed(self, text: str) -> None:
        with pytest.raises(BoundaryTokenError) as caught:
            decoded(text)
        assert caught.value.reason is TokenRejection.MALFORMED

    def test_a_length_base64_cannot_decode_is_malformed(self) -> None:
        with pytest.raises(BoundaryTokenError) as caught:
            decoded("a")
        assert caught.value.reason is TokenRejection.MALFORMED

    def test_a_twin_of_an_encrypted_text_is_malformed(self, crypto) -> None:
        text = EncryptedSeal().seal(b"ix", _SALT)
        assert _reason(EncryptedSeal(), _twin(text)) is TokenRejection.MALFORMED


class TestSignedSeal:
    """``SignedSeal`` is Django's ``Signer`` under the project secrets."""

    def test_the_text_is_the_known_vector(self) -> None:
        assert SignedSeal().seal(b"i{}", _SALT) == _SIGNED_VECTOR
        assert SignedSeal().open(_SIGNED_VECTOR, _SALT) == b"i{}"

    def test_a_large_payload_that_compresses_is_what_a_django_signer_writes(
        self,
    ) -> None:
        payload = b"i" + b"a" * COMPRESS_FROM
        signer = signing.Signer(salt=_SALT, sep=".", algorithm="sha256")
        sealed = SignedSeal().seal(payload, _SALT)
        assert sealed.startswith(".")
        assert sealed == signer.sign_object(payload, serializer=_Bytes, compress=True)
        assert SignedSeal().open(sealed, _SALT) == payload

    def test_a_small_payload_is_sent_plain_even_when_it_compresses(self) -> None:
        payload = b"i" + b"a" * 60
        sealed = SignedSeal().seal(payload, _SALT)
        assert sealed == signing.Signer(salt=_SALT, sep=".", algorithm="sha256").sign(
            b64encode(payload)
        )
        assert SignedSeal().open(sealed, _SALT) == payload

    def test_a_large_payload_that_does_not_compress_is_sent_plain(self) -> None:
        payload = b"i" + bytes(range(COMPRESS_FROM))
        sealed = SignedSeal().seal(payload, _SALT)
        assert not sealed.startswith(".")
        assert SignedSeal().open(sealed, _SALT) == payload

    def test_a_compressed_small_payload_opens(self) -> None:
        assert SignedSeal().open(_COMPRESSED_VECTOR, _SALT) == b"i" + b"a" * 60

    def test_the_text_is_what_a_django_signer_writes(self) -> None:
        signer = signing.Signer(salt=_SALT, sep=".", algorithm="sha256")
        assert SignedSeal().seal(b"i{}", _SALT) == signer.sign("aXt9")

    def test_another_salt_fails(self) -> None:
        sealed = SignedSeal().seal(b"i{}", _SALT)
        other = token_salt(VERSION, 1, "0", "session:abc")
        assert _reason(SignedSeal(), sealed, other) is TokenRejection.SIGNATURE

    def test_a_token_minted_before_a_key_rotation_still_opens(self) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            sealed = SignedSeal().seal(b"i{}", _SALT)
        with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[OLD_KEY]):
            assert SignedSeal().open(sealed, _SALT) == b"i{}"

    def test_a_retired_key_no_longer_opens(self) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            sealed = SignedSeal().seal(b"i{}", _SALT)
        assert _reason(SignedSeal(), sealed) is TokenRejection.SIGNATURE

    def test_a_django_signer_under_a_fallback_key_opens(self) -> None:
        signer = signing.Signer(key=OLD_KEY, salt=_SALT, sep=".", algorithm="sha256")
        sealed = signer.sign_object(b"i{}", serializer=_Bytes, compress=True)
        with override_settings(SECRET_KEY_FALLBACKS=[OLD_KEY]):
            assert SignedSeal().open(sealed, _SALT) == b"i{}"

    def test_every_body_and_mac_position_is_covered(self) -> None:
        sealed = SignedSeal().seal(b"i" + bytes(range(40)), _SALT)
        for index, char in enumerate(sealed):
            if char == ".":
                continue
            assert _reason(SignedSeal(), flip(sealed, index)) in {
                TokenRejection.SIGNATURE,
                TokenRejection.MALFORMED,
            }

    @pytest.mark.parametrize(
        "mangle",
        [
            lambda text: text[:-1],
            lambda text: text + "A",
            lambda text: text.replace(".", ""),
            lambda text: f"{text}.{text}",
        ],
    )
    def test_truncation_and_concatenation_are_refused(self, mangle) -> None:
        sealed = SignedSeal().seal(b"i{}", _SALT)
        assert _reason(SignedSeal(), mangle(sealed)) in {
            TokenRejection.SIGNATURE,
            TokenRejection.MALFORMED,
        }

    def test_a_signed_body_of_a_length_base64_cannot_decode_is_malformed(self) -> None:
        signer = signing.Signer(salt=_SALT, sep=".", algorithm="sha256")
        assert _reason(SignedSeal(), signer.sign("a")) is TokenRejection.MALFORMED

    def test_a_signed_body_that_does_not_decode_is_malformed(self) -> None:
        signer = signing.Signer(salt=_SALT, sep=".", algorithm="sha256")
        sealed = signer.sign("." + b64encode(b"not zlib"))
        assert _reason(SignedSeal(), sealed) is TokenRejection.MALFORMED

    def test_a_compressed_body_is_only_inflated_after_its_signature(self) -> None:
        bomb = "." + b64encode(zlib.compress(bytes(10_000)))
        sealed = f"{bomb}.{'A' * 43}"
        assert _reason(SignedSeal(), sealed) is TokenRejection.SIGNATURE


class TestEncryptedSeal:
    """``EncryptedSeal`` is ``MultiFernet`` under keys derived per salt."""

    def test_payload_round_trips_and_is_hidden(self, crypto) -> None:
        sealed = EncryptedSeal().seal(b"i{secret}", _SALT)
        assert b"secret" not in b64decode(sealed)
        assert EncryptedSeal().open(sealed, _SALT) == b"i{secret}"

    def test_two_seals_of_one_payload_differ(self, crypto) -> None:
        seal = EncryptedSeal()
        assert seal.seal(b"i{}", _SALT) != seal.seal(b"i{}", _SALT)

    def test_the_fernet_key_is_the_known_vector(self) -> None:
        assert fernet_key(KEY, _SALT) == _FERNET_KEY_VECTOR
        assert fernet_key(KEY, f"{_SALT}.x") != _FERNET_KEY_VECTOR
        assert fernet_key(OLD_KEY, _SALT) != _FERNET_KEY_VECTOR
        assert FERNET_INFO == "next.defer.boundary.fernet"

    def test_another_salt_fails(self, crypto) -> None:
        sealed = EncryptedSeal().seal(b"i{}", _SALT)
        other = token_salt(VERSION, 1, "0", "session:abc")
        assert _reason(EncryptedSeal(), sealed, other) is TokenRejection.SIGNATURE

    @pytest.mark.parametrize("index", [0, 9, 30, 60, -1])
    def test_a_flipped_bit_fails(self, crypto, index: int) -> None:
        sealed = EncryptedSeal().seal(b"i{}", _SALT)
        assert _reason(EncryptedSeal(), flip_bit(sealed, index)) in {
            TokenRejection.SIGNATURE,
            TokenRejection.MALFORMED,
        }

    def test_a_token_minted_before_a_key_rotation_still_opens(self, crypto) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            sealed = EncryptedSeal().seal(b"i{}", _SALT)
        with override_settings(SECRET_KEY=KEY, SECRET_KEY_FALLBACKS=[OLD_KEY]):
            assert EncryptedSeal().open(sealed, _SALT) == b"i{}"

    def test_a_retired_key_no_longer_opens(self, crypto) -> None:
        with override_settings(SECRET_KEY=OLD_KEY):
            sealed = EncryptedSeal().seal(b"i{}", _SALT)
        assert _reason(EncryptedSeal(), sealed) is TokenRejection.SIGNATURE


class TestEncryptedSealWithoutTheLibrary:
    """``EncryptedSeal`` names the extra it needs when ``cryptography`` is missing."""

    def test_it_refuses_to_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_crypto(monkeypatch, present=False)
        assert crypto_library() is None
        assert not EncryptedSeal.available()
        with pytest.raises(ImproperlyConfigured, match=r"next-dj\[crypto\]"):
            EncryptedSeal()

    def test_a_built_seal_keeps_the_library_it_was_built_with(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_crypto(monkeypatch)
        seal = EncryptedSeal()
        install_crypto(monkeypatch, present=False)
        assert seal.open(seal.seal(b"i{}", _SALT), _SALT) == b"i{}"

    def test_signed_and_unsealed_seals_need_no_library(self) -> None:
        assert SignedSeal.available()
        assert NoSeal.available()


class TestEncryptedSealOnTheRealLibrary:
    """The seal against the installed ``cryptography``, skipped without it."""

    def test_the_known_vector_opens(self) -> None:
        pytest.importorskip("cryptography")
        assert EncryptedSeal().open(_FERNET_VECTOR, _SALT) == b"i{}"

    def test_payload_round_trips_and_tampering_fails(self) -> None:
        pytest.importorskip("cryptography")
        seal = EncryptedSeal()
        sealed = seal.seal(b"i{}", _SALT)
        assert seal.open(sealed, _SALT) == b"i{}"
        assert _reason(seal, flip_bit(sealed, 40)) is TokenRejection.SIGNATURE
